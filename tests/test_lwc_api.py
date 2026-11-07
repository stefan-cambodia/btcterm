#!/usr/bin/env python3
"""
La route `/api/klines`, contrôlée sans réseau via le client de test Flask.

Les sources sont remplacées par une « base » synthétique de 2 000 bougies
horaires : le faux `fetch_klines` en sert des tranches en respectant
`limit` et `end_time`, exactement comme Binance. Ce que la route doit
tenir :

- la page sans `before` est la fin de la série, indicateurs compris ;
- la page `before` se termine exactement où commence la suivante — pas
  de trou, pas de recouvrement ;
- la marge de calcul (WARMUP_BARS) rend la MA 200 juste dès la première
  bougie d'une page, sans jamais être renvoyée ;
- le repli hors ligne passe par le même endpoint, drapeau `demo` levé ;
- historique épuisé ou source injoignable en pagination → page vide,
  jamais une erreur ; `stale` dans le second cas seulement, que le
  client réessaie au lieu de clore le passé.

Lancement :
    python tests/test_lwc_api.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm import sources  # noqa: E402

#: Le vrai collecteur : `client()` le remplace pour tout le module.
_VRAI_FETCH = sources.fetch_klines
from btcterm.hub import MarketHub  # noqa: E402
from btcterm.sources import generate_demo_ohlcv  # noqa: E402
from terminal.app import create_app  # noqa: E402
from terminal.lwc import PAGE_MAX, WARMUP_BARS  # noqa: E402

#: La « base » de la fausse plateforme : 2 000 bougies horaires figées.
#: L'horodatage est ramené à la nanoseconde, la résolution que produit
#: le vrai `fetch_klines` (`pd.to_datetime(unit="ms")`) — la série de
#: démo, construite sur `datetime.now()`, sort en microsecondes.
DB = generate_demo_ohlcv(2_000, interval="1h", index=False)
DB["time"] = DB["time"].astype("datetime64[ns]")
DB.attrs.clear()  # la base simule des données réelles, pas la démo


def faux_fetch_klines(symbol="BTCUSDT", interval="1h", limit=350,
                      index=False, end_time=None):
    """Sert DB comme Binance servirait ses bougies : les `limit`
    dernières ouvertes au plus tard à `end_time` (millisecondes)."""
    frame = DB
    if end_time is not None:
        epoch_ms = frame["time"].astype("int64") // 1_000_000
        frame = frame[epoch_ms <= int(end_time)]
    return frame.iloc[-limit:].reset_index(drop=True)


def panne_reseau(*args, **kwargs):
    raise ConnectionError("source injoignable (simulée)")


def client(fetch=faux_fetch_klines):
    """Un terminal assemblé sur un hub jamais démarré, sources mockées."""
    sources.fetch_klines = fetch
    sources.fetch_eur_rate = lambda: 0.9
    hub = MarketHub(collect_news=False, keep_journal=False)
    return create_app(hub).server.test_client()


def get(web, **params):
    reponse = web.get("/api/klines", query_string=params)
    assert reponse.status_code == 200, reponse.status_code
    return reponse.get_json()


def test_page_initiale_complete():
    web = client()
    page = get(web, interval="1h", limit=350)

    assert len(page["bars"]) == 350
    assert page["interval"] == "1h"
    assert page["eur_rate"] == 0.9 and page["eur_stale"] is False
    assert page["demo"] is False
    assert set(page["overlays"]) >= {"ma9", "ma26", "ma200"}
    assert page["panes"]["rsi"] and page["volume"] and page["volume_ma"]

    #: La fin de la page est la fin de la base.
    fin_db = int(DB["time"].iloc[-1].value // 1_000_000_000)
    assert page["bars"][-1]["time"] == fin_db
    print("  ✓ 350 bougies, indicateurs et taux €, ancrées à la fin de la série")


def test_pagination_sans_trou_ni_recouvrement():
    web = client()
    page1 = get(web, interval="1h", limit=300)
    page2 = get(web, interval="1h", limit=300, before=page1["bars"][0]["time"])
    page3 = get(web, interval="1h", limit=300, before=page2["bars"][0]["time"])

    #: Les trois pages recollées reproduisent la fin de la base, à
    #: l'heure près : chaque page se termine exactement où commence la
    #: suivante.
    temps = [b["time"] for b in page3["bars"] + page2["bars"] + page1["bars"]]
    attendu = [int(t.value // 1_000_000_000) for t in DB["time"].iloc[-900:]]
    assert temps == attendu, "pages non contiguës"
    print("  ✓ trois pages contiguës — 900 bougies sans trou ni recouvrement")


def test_marge_de_calcul_invisible_mais_efficace():
    web = client()
    page1 = get(web, interval="1h", limit=300)
    page2 = get(web, interval="1h", limit=300, before=page1["bars"][0]["time"])

    #: La marge ne fuit pas : une page fait `limit` bougies, pas plus.
    assert len(page2["bars"]) == 300

    #: Et elle a servi : la MA 200 est définie dès la première bougie de
    #: la page, alors que sans marge elle ne commencerait qu'à la 200e.
    assert page2["overlays"]["ma200"][0]["time"] == page2["bars"][0]["time"]

    #: Enfin elle est juste : la MA 200 de la première bougie de page2
    #: vaut la moyenne des 200 closes que la base tient à cet endroit.
    epoch = DB["time"].astype("int64") // 1_000_000_000
    position = int(epoch.searchsorted(page2["bars"][0]["time"]))
    attendu = float(DB["close"].iloc[position - 199:position + 1].mean())
    obtenu = page2["overlays"]["ma200"][0]["value"]
    assert abs(obtenu - attendu) / attendu < 1e-9, (obtenu, attendu)
    print("  ✓ marge de 200 bougies : calculée, exacte, jamais renvoyée")


def test_repli_demo_par_le_meme_endpoint():
    web = client(fetch=panne_reseau)
    page = get(web, interval="1h")
    assert page["demo"] is True, "le repli hors ligne doit se déclarer"
    assert page["bars"], "le repli doit quand même servir une série"
    print("  ✓ hors ligne : série de démonstration servie, drapeau demo levé")


def test_historique_epuise_page_vide():
    web = client()
    debut_db = int(DB["time"].iloc[0].value // 1_000_000_000)
    page = get(web, interval="1h", limit=300, before=debut_db)
    assert page["bars"] == [], "avant le début de la base : page vide"
    assert page["demo"] is False
    assert page["stale"] is False, "l'historique épuisé ne se réessaie pas"

    #: Une panne en pagination aussi : vide, pas une erreur ni de la démo
    #: — mais dite, sans quoi le client clôt le passé pour la séance.
    sources.fetch_klines = panne_reseau
    en_panne = get(web, interval="1h", limit=300, before=debut_db + 3_600 * 50)
    assert en_panne["bars"] == []
    assert en_panne["stale"] is True, "une source muette doit se dire"
    print("  ✓ historique épuisé ou source en panne : page vide, statut 200,"
          " et seule la panne se dit")


def test_profil_fenetre_sur_la_plage():
    """`/api/profile` calcule sur la tranche demandée, pas sur la série :
    deux fenêtres différentes donnent deux profils différents, chacun
    borné aux prix réellement parcourus dans sa fenêtre."""
    web = client()
    epoch = DB["time"].astype("int64") // 10**9

    def profil(i_debut, i_fin):
        reponse = web.get("/api/profile", query_string={
            "interval": "1h",
            "from": int(epoch.iloc[i_debut]), "to": int(epoch.iloc[i_fin])})
        assert reponse.status_code == 200
        return reponse.get_json()

    recent = profil(-200, -1)
    ancien = profil(-1200, -1001)

    for page, (i_debut, i_fin) in ((recent, (-200, -1)),
                                   (ancien, (-1200, -1001))):
        tranche = DB.iloc[i_debut:i_fin if i_fin != -1 else None]
        assert len(page["centers"]) == len(page["volumes"])
        assert tranche["low"].min() <= page["poc"] <= tranche["high"].max()
        assert page["va_low"] <= page["poc"] <= page["va_high"]
    assert recent["poc"] != ancien["poc"], "deux fenêtres, deux profils"
    print("  ✓ profil borné à sa fenêtre, POC dans la Value Area, fenêtres distinctes")


def test_niveaux_cles():
    """`/api/levels` sert les niveaux de la veille, de la semaine et du mois
    en dollars, et rien sur la démonstration — des repères tirés d'une
    marche aléatoire seraient faux."""
    web = client()
    page = web.get("/api/levels").get_json()
    assert page["demo"] is False
    etiquettes = {niveau["label"].split("/")[0] for niveau in page["levels"]}
    assert {"H J-1", "B J-1", "O J"} <= etiquettes, etiquettes
    for niveau in page["levels"]:
        assert DB["low"].min() <= niveau["price"] <= DB["high"].max(), niveau
        assert niveau["span"] in ("day", "week", "month")

    demo = client(panne_reseau).get("/api/levels").get_json()
    assert demo["demo"] is True and demo["levels"] == []
    print("  ✓ niveaux clés en dollars, aucun sur la démonstration")


def test_profil_bornes_et_tranche_vide():
    web = client()
    #: Paramètres refusés : intervalle inconnu, bornes absentes ou inversées.
    assert web.get("/api/profile?interval=7m&from=1&to=2").status_code == 400
    assert web.get("/api/profile?interval=1h").status_code == 400
    assert web.get("/api/profile?interval=1h&from=9&to=3").status_code == 400

    #: Une plage sans aucune bougie — avant le début de la base — se dit
    #: vide au lieu de lever.
    debut = int(DB["time"].iloc[0].value // 10**9)
    page = web.get("/api/profile", query_string={
        "interval": "1h", "from": debut - 9_000, "to": debut - 3_600,
    }).get_json()
    assert page.get("empty") is True
    print("  ✓ paramètres hostiles refusés, tranche vide dite vide")


def test_profil_repli_demo():
    """Hors ligne, le profil se rabat sur la dernière page du hub — la
    série de démonstration — et le dit."""
    web = client(fetch=panne_reseau)
    serie = get(web, interval="1h")
    page = web.get("/api/profile", query_string={
        "interval": "1h",
        "from": serie["bars"][0]["time"], "to": serie["bars"][-1]["time"],
    }).get_json()
    assert page["demo"] is True
    assert page.get("centers"), "le repli doit quand même profiler"
    print("  ✓ hors ligne : profil sur la série de démonstration, drapeau levé")


def test_api_perp_series_et_unites():
    """`/api/perp` : financement en % par 8 h, open interest en dollars
    bruts, tous deux au contrat de série (epoch croissants, sans NaN) —
    et la série d'OI prolongée par le journal quand il en tient."""
    debut = pd.Timestamp("2026-07-01")
    sources.fetch_funding_history = lambda symbol, limit: pd.DataFrame({
        "time": pd.date_range(debut, periods=limit, freq="8h"),
        "rate": [0.0001 * (-1) ** k for k in range(limit)],
    })
    sources.fetch_open_interest = lambda symbol, period, limit: pd.DataFrame({
        "time": pd.date_range(debut, periods=limit, freq="4h"),
        "oi": [80_000.0] * limit,
        "oi_usd": [1.2e10] * limit,
    })
    web = client()
    page = web.get("/api/perp").get_json()

    assert len(page["funding"]) == 90, "90 points de financement — 30 jours"
    assert page["funding"][0]["value"] == 0.01, "le taux voyage en %"
    assert page["funding"][1]["value"] == -0.01
    assert len(page["oi"]) == 180
    assert page["oi"][-1]["value"] == 1.2e10, "l'OI voyage en dollars bruts"
    for serie in (page["funding"], page["oi"]):
        temps = [p["time"] for p in serie]
        assert all(a < b for a, b in zip(temps, temps[1:]))
    print("  ✓ financement en %, OI en dollars, contrat de série tenu")


def test_carte_des_liquidations():
    """`/api/liqmap` lit le journal sur la plage demandée ; sans journal, ou
    sur une plage vide, la carte dit pourquoi au lieu de lever."""
    import tempfile
    import time as horloge
    from btcterm.journal import Journal
    from btcterm.liquidations import Liquidation

    web = client()
    assert web.get("/api/liqmap?from=9&to=3").status_code == 400
    assert web.get("/api/liqmap").status_code == 400
    sans = web.get("/api/liqmap?from=1&to=2").get_json()
    assert sans["empty"] and sans["reason"] == "journal désactivé"

    with tempfile.TemporaryDirectory() as tmp:
        sources.fetch_klines = faux_fetch_klines
        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = Journal(Path(tmp) / "journal.db")
        maintenant = horloge.time()
        for prix, cote in ((70_000.0, "long"), (71_000.0, "short")):
            hub.journal.record_liquidation(Liquidation(
                time=maintenant - 60, symbol="BTCUSDT", side=cote, price=prix,
                quantity=1.0, exchange="Bybit"))
        web = create_app(hub).server.test_client()
        carte = web.get("/api/liqmap", query_string={
            "from": int(maintenant - 3600), "to": int(maintenant)}).get_json()
        assert carte["count"] == 2 and carte["exchanges"] == ["Bybit"]
        assert sum(carte["longs"]) == 70_000 and sum(carte["shorts"]) == 71_000
        vide = web.get("/api/liqmap", query_string={
            "from": 1, "to": 1000}).get_json()
        assert vide["empty"] and "aucune" in vide["reason"]
        hub.journal.close()
    print("  ✓ carte lue au journal, plage vide et journal absent dits")


def test_api_perp_hors_ligne():
    """Sources en panne : deux listes vides, statut 200 — le panneau
    l'écrit, le terminal vit."""
    sources.fetch_funding_history = panne_reseau
    sources.fetch_open_interest = panne_reseau
    web = client()
    reponse = web.get("/api/perp")
    assert reponse.status_code == 200
    page = reponse.get_json()
    assert page["funding"] == [] and page["oi"] == []
    assert isinstance(page["eur_rate"], float), "le taux voyage avec la série"
    print("  ✓ hors ligne : listes vides, jamais une erreur")


def test_binance_ne_recoit_jamais_plus_de_mille():
    """La minute en demande 1440 : Binance refuse au-delà de 1000. Sans
    historique sur disque, la série est servie tronquée, pas refusée."""
    vus = []

    class Reponse:
        def raise_for_status(self):
            pass

        def json(self):
            return []

    vrai = sources._klines_http.get
    sources._klines_http.get = (
        lambda url, params=None, timeout=None: vus.append(params) or Reponse())
    try:
        _VRAI_FETCH("BTCUSDT", "1m", 1440)
    except Exception:  # noqa: BLE001 — seule la requête compte ici
        pass
    finally:
        sources._klines_http.get = vrai
    assert vus and vus[0]["limit"] == sources.KLINES_MAX == 1000, vus
    print("  ✓ 1440 demandées, 1000 demandées à Binance")


def test_parametres_hostiles():
    web = client()
    assert web.get("/api/klines?interval=7m").status_code == 400

    #: `limit` est borné, pas refusé.
    assert len(get(web, interval="1h", limit=50_000)["bars"]) <= PAGE_MAX
    assert len(get(web, interval="1h", limit=-3)["bars"]) == 1
    print(f"  ✓ intervalle inconnu refusé (400), limit borné à [1, {PAGE_MAX}]")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nRoute /api/klines — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("L'historique se pagine sans trou, se replie sans mentir.\n")
