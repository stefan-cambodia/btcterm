#!/usr/bin/env python3
"""
La carte de chaleur du carnet : colonnes, journal, matrice à peindre.

Ce qui se vérifie mal à l'écran se vérifie ici :

- le pas des tranches est un nombre rond, proche de 0,04 % du prix ;
- une colonne additionne les carnets dans la bande de ±2 %, et ignore ce
  qui en sort ;
- la colonne traverse le journal sans perte ;
- la matrice moyenne les colonnes d'une même bougie, ramène un pas plus
  fin à la grille commune, et se quantifie sur 256 niveaux bornés au
  99e centile — un mur isolé n'éteint pas le reste.

Aucun réseau n'est touché.

Lancement :
    python tests/test_heatmap.py
"""

import base64
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from btcterm import heatmap  # noqa: E402


def test_le_pas_est_rond():
    assert heatmap.nice_step(74_000) == 25      # 29,6 → 25
    assert heatmap.nice_step(100_000) == 25     # 40 → 25
    assert heatmap.nice_step(150_000) == 50     # 60 → 50
    assert heatmap.nice_step(6_000) == 2        # 2,4 → 2
    assert heatmap.nice_step(30_000) == 10      # 12 → 10
    print("  ✓ 25 $ vers 74 000 $, 50 $ vers 150 000 $ : des pas ronds")


def test_une_colonne_additionne_les_carnets():
    binance = ({99_990.0: 1.0, 99_980.0: 2.0, 90_000.0: 5.0},
               {100_010.0: 3.0, 110_000.0: 9.0})
    kraken = ({99_990.0: 0.5}, {})
    base, pas, valeurs = heatmap.column([binance, kraken], 100_000.0)
    assert pas == 25 and base == 98_000 // 25
    assert len(valeurs) == (102_000 - 98_000) // 25 + 1
    assert valeurs.dtype == np.float32
    assert valeurs[99_990 // 25 - base] == 3.5, "deux niveaux et deux carnets"
    assert valeurs[100_010 // 25 - base] == 3.0
    assert valeurs.sum() == 6.5, "hors de la bande, rien ne compte"
    assert heatmap.column([binance], 0) is None
    assert heatmap.column([({}, {})], 100_000.0)[2].sum() == 0
    print("  ✓ une colonne : ±2 %, carnets additionnés, le reste ignoré")


def test_la_colonne_traverse_le_journal():
    from btcterm.journal import Journal

    valeurs = np.array([0.0, 1.5, 0.0, 12.25], dtype=np.float32)
    assert np.array_equal(heatmap.decode(heatmap.encode(valeurs)), valeurs)
    with tempfile.TemporaryDirectory() as dossier:
        journal = Journal(Path(dossier) / "journal.db")
        journal.record_heatmap(1_000.0, 3_920, 25.0, heatmap.encode(valeurs))
        journal.record_heatmap(2_000.0, 3_921, 25.0, heatmap.encode(valeurs))
        lues = journal.heatmap_between(1_500, 2_500)
        assert [(ts, base, pas) for ts, base, pas, _ in lues] == [(2_000.0, 3_921, 25.0)]
        assert np.array_equal(lues[0][3], valeurs)
        journal.close()
    print("  ✓ la colonne s'écrit au journal et s'y relit à l'identique")


def lire(matrice):
    octets = np.frombuffer(base64.b64decode(matrice["data"]), dtype=np.uint8)
    return octets.reshape(len(matrice["times"]), matrice["rows"])


def test_la_matrice_moyenne_regrille_et_quantifie():
    un = np.array([2.0, 0.0, 4.0], dtype=np.float32)
    deux = np.array([4.0, 0.0, 0.0], dtype=np.float32)
    colonnes = [(3_600.0, 100, 25.0, un), (3_610.0, 100, 25.0, deux),
                (7_200.0, 100, 25.0, un)]
    matrice = heatmap.aggregate(colonnes, 3_600)
    assert matrice["times"] == [3_600, 7_200] and matrice["rows"] == 3
    assert matrice["step"] == 25 and matrice["low"] == 2_500
    cases = lire(matrice)
    # Première bougie : la moyenne des deux colonnes, (3, 0, 2).
    assert cases[0][1] == 0 and cases[0][0] > cases[0][2] > 0
    assert cases.max() == 255 and cases.min() == 0

    # Un pas plus fin rejoint la grille commune par ses prix.
    fin = (10.0, 200, 12.5, np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32))
    large = (20.0, 100, 25.0, np.array([0.0, 0.0, 0.0], dtype=np.float32))
    commune = heatmap.aggregate([fin, large], 3_600)
    assert commune["step"] == 25 and commune["low"] == 2_500
    assert commune["rows"] == 3
    assert lire(commune)[0].tolist().count(0) == 1, "deux tranches fines par tranche commune"

    # Un mur isolé : borné au 99e centile, il n'éteint pas le reste.
    beaucoup = np.full(200, 1.0, dtype=np.float32)
    beaucoup[0] = 1_000.0
    mur = heatmap.aggregate([(0.0, 0, 25.0, beaucoup)], 3_600)
    assert mur["vmax"] < 1_000 and lire(mur)[0][1] > 100

    assert heatmap.aggregate([], 3_600) is None
    assert heatmap.aggregate([(0.0, 0, 25.0, np.zeros(3, dtype=np.float32))],
                             3_600) is None
    print("  ✓ matrice : moyenne par bougie, grille commune, racine carrée "
          "bornée au 99e centile")



def test_le_hub_photographie_les_carnets_et_le_journal_purge():
    """Une colonne par passage, des seuls carnets qui disent le marché —
    connectés, frais, non croisés —, et une purge qui ne garde que trois
    jours de carte (la purge tourne désormais dans la boucle, pas
    seulement au démarrage)."""
    import time
    from btcterm.hub import MarketHub
    from btcterm.journal import HEATMAP_RETENTION_DAYS, Journal

    with tempfile.TemporaryDirectory() as dossier:
        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = Journal(Path(dossier) / "journal.db")
        assert MarketHub.PURGE_EVERY <= 24 * 3600
        assert not hub._sample_heatmap(), "aucun carnet : rien à écrire"
        hub.books["Binance"].replace({73_990.0: 2.0}, {74_010.0: 1.0})
        hub.books["Binance"].connected = True
        hub.books["Kraken"].replace({73_990.0: 0.5}, {74_010.0: 0.25})
        hub.books["Kraken"].connected = True
        hub.books["OKX"].replace({75_000.0: 9.0}, {74_000.0: 9.0})  # croisé
        hub.books["OKX"].connected = True
        maintenant = time.time()
        assert hub._sample_heatmap(now=maintenant)
        ((ts, base, pas, valeurs),) = hub.journal.heatmap_between(0, maintenant + 1)
        assert pas == 25 and valeurs.sum() == 3.75, "le carnet croisé ne compte pas"

        vieux = maintenant - (HEATMAP_RETENTION_DAYS + 1) * 86_400
        hub.journal.record_heatmap(vieux, base, pas, heatmap.encode(valeurs))
        hub.journal.purge()
        restants = [c[0] for c in hub.journal.heatmap_between(0, maintenant + 1)]
        assert restants == [maintenant], restants
        hub.journal.close()
    print("  ✓ une colonne des carnets sains, purge à trois jours")


def test_l_endpoint_rend_une_matrice_ou_dit_pourquoi():
    import time
    from btcterm.hub import MarketHub
    from btcterm.journal import Journal
    from terminal.app import create_app

    sans = create_app(MarketHub(collect_news=False, keep_journal=False))
    web = sans.server.test_client()
    assert web.get("/api/heatmap?from=1&to=2").status_code == 400
    assert web.get("/api/heatmap?from=1&to=2&step=60").get_json()["reason"] \
        == "journal désactivé"

    with tempfile.TemporaryDirectory() as dossier:
        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = Journal(Path(dossier) / "journal.db")
        debut = 1_794_461_400
        for k in range(12):                 # deux minutes, dix secondes
            valeurs = np.zeros(161, dtype=np.float32)
            valeurs[80] = 5.0 + k
            hub.journal.record_heatmap(debut + 10 * k, 3_920, 25.0,
                                       heatmap.encode(valeurs))
        web = create_app(hub).server.test_client()
        matrice = web.get("/api/heatmap", query_string={
            "from": debut, "to": debut + 120, "step": 60}).get_json()
        assert matrice["times"] == [debut, debut + 60] and matrice["rows"] == 161
        assert matrice["bucket"] == 60 and matrice["step"] == 25
        cases = lire(matrice)
        assert cases.shape == (2, 161) and cases[1][80] == 255
        assert cases[0][80] > 0 and cases[0][79] == 0
        assert web.get("/api/heatmap", query_string={
            "from": debut, "to": debut + 120, "step": 1}).get_json()["bucket"] == 60
        vide = web.get("/api/heatmap", query_string={
            "from": 1, "to": 1_000, "step": 60}).get_json()
        assert vide["empty"] and "photographie" in vide["reason"]
        hub.journal.close()
    print("  ✓ /api/heatmap : matrice par bougie, tranche bornée, vide dit")

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCarte de chaleur du carnet — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le carnet se lit dans le temps.\n")
