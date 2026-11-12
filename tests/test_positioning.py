#!/usr/bin/env python3
"""
Le positionnement du perpétuel : la foule contre l'argent des gros.

Ce qui se vérifie mal à l'écran se vérifie ici :

- Binance sert deux séries au même pas, en parts de 0 à 1 : elles
  voyagent en %, l'une passe quand l'autre tombe, rien ne se met en cache
  quand les deux manquent ;
- le relevé de marché les note, et le journal les prolonge au-delà des
  trente jours de Binance, rééchantillonnées sans couture ;
- le titre du panneau dit la part des gros en plein écran seulement.

Aucun réseau n'est touché.

Lancement :
    python tests/test_positioning.py
"""

import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm import sources  # noqa: E402

T0 = 1_794_441_600_000          # 17 novembre 2026, en ms


class Reponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.payload


def lignes(parts):
    return [{"symbol": "BTCUSDT", "longAccount": str(p),
             "timestamp": T0 + k * 4 * 3_600_000} for k, p in enumerate(parts)]


def test_deux_series_en_pour_cent():
    vues = []

    def binance(url, params=None, **_):
        vues.append((url.rsplit("/", 1)[1], params))
        if url.endswith("globalLongShortAccountRatio"):
            return Reponse(lignes([0.748, 0.75]))
        return Reponse(lignes([0.695, 0.69]))

    with mock.patch.object(sources._http, "get", binance):
        cadre = sources.fetch_long_short(period="4h", limit=2)
    assert [v[0] for v in vues] == ["globalLongShortAccountRatio",
                                    "topLongShortPositionRatio"]
    assert vues[0][1] == {"symbol": "BTCUSDT", "period": "4h", "limit": 2}
    assert list(cadre.columns) == ["time", "crowd", "top"]
    assert cadre["crowd"].round(6).tolist() == [74.8, 75.0]
    assert cadre["top"].round(6).tolist() == [69.5, 69.0]
    assert cadre["time"].iloc[0] == pd.Timestamp(T0, unit="ms")

    def seule(url, **_):
        if url.endswith("topLongShortPositionRatio"):
            return Reponse({}, status=418)
        return Reponse(lignes([0.748]))

    with mock.patch.object(sources._http, "get", seule):
        partiel = sources.fetch_long_short(limit=1)
    assert partiel["crowd"].tolist() == [74.8] and partiel["top"].isna().all()

    with mock.patch.object(sources._http, "get",
                           lambda url, **_: Reponse([])):
        try:
            sources.fetch_long_short()
        except RuntimeError as exc:
            assert "global" in str(exc) and "top" in str(exc)
        else:
            raise AssertionError("deux vides : une erreur, pas un cache vide")
    print("  ✓ foule et gros en %, l'une sans l'autre, jamais un vide en cache")


def test_le_journal_les_note_et_les_prolonge():
    from btcterm import options as opt
    from btcterm.hub import MarketHub
    from btcterm.journal import Journal

    with tempfile.TemporaryDirectory() as dossier:
        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = Journal(Path(dossier) / "journal.db")
        hub.market_global = lambda: {}
        hub.open_interest = lambda **kw: pd.DataFrame(columns=["time", "oi", "oi_usd"])
        hub.option_chain = lambda: opt.option_chain([])
        hub.futures_curve = lambda: opt.futures_curve([])
        hub.dvol = lambda: pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        hub.perp_snapshot = lambda: {"long_account": 0.748}
        maintenant = time.time()
        recent = pd.Timestamp(maintenant, unit="s").floor("4h")
        binance = pd.DataFrame({"time": [recent - pd.Timedelta(hours=4), recent],
                                "crowd": [75.0, 74.8], "top": [70.0, 69.5]})
        with mock.patch.object(sources, "fetch_long_short",
                               return_value=binance):
            # Deux relevés d'il y a deux jours — avant la fenêtre servie —,
            # et un du moment.
            for decalage in (49 * 3600, 48 * 3600 + 600, 0):
                hub.record_market_snapshot(now=maintenant - decalage)
            lus = hub.market_snapshots()
            # Les lignes de rupture, après chaque trou de séance, écartées.
            lus = lus[~lus["gap"].astype(bool)]
            assert lus["crowd_long"].round(6).tolist() == [74.8] * 3
            assert lus["top_long"].round(6).tolist() == [69.5] * 3, \
                "l'argent des gros : le dernier pas de Binance"
            etendu = hub.long_short_extended()
        avant = etendu[etendu["time"] < binance["time"].iloc[0]]
        assert len(avant) >= 1 and avant["top"].dropna().round(6).eq(69.5).all()
        assert etendu["time"].is_monotonic_increasing
        assert etendu.tail(2)["crowd"].tolist() == [75.0, 74.8], "Binance au bout"
        hub.journal.close()

    # Binance muet, rien en cache : une série vide, sans lever.
    seul = MarketHub(collect_news=False, keep_journal=False)
    with mock.patch.object(sources, "fetch_long_short",
                           side_effect=ConnectionError("muet")):
        assert seul.long_short().empty and seul.long_short_extended().empty
    print("  ✓ notées toutes les cinq minutes, prolongées par le journal")


def test_le_titre_dit_les_gros_en_plein_ecran():
    from terminal.panels import perp

    def texte(composant):
        if isinstance(composant, (list, tuple)):
            return "".join(texte(c) for c in composant)
        if isinstance(composant, str):
            return composant
        return texte(getattr(composant, "children", "") or "")

    instant = {"funding_rate": 0.0001, "long_account": 0.748}
    grille = texte(perp._badges(instant, None, verbose=False, top_long=69.5))
    assert "gros" not in grille and "longs 75 %" in grille
    plein = texte(perp._badges(instant, None, verbose=True, top_long=69.5))
    assert "comptes longs 75 %" in plein and "gros 70 %" in plein, plein
    sans = texte(perp._badges(instant, None, verbose=True))
    assert "gros" not in sans
    print("  ✓ la part des gros au titre, en plein écran seulement")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nPositionnement du perpétuel — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La foule et l'argent ne pèsent pas pareil.\n")
