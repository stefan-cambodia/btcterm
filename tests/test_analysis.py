#!/usr/bin/env python3
"""
Le bilan des signaux et le panneau analyse, contrôlés sans réseau.

Un bilan faux ne se voit pas à l'écran : il affiche des chiffres plausibles.
Ce test verrouille donc ce qui le fausserait en silence :

- le regard sur le futur — une bougie ajoutée ne change pas un signal passé,
  et l'entrée se fait à l'ouverture suivante ;
- l'absence de référence — dans un marché qui monte partout, un signal
  d'achat gagne sans rien apporter : son écart doit être nul ;
- le sens — un signal de vente réussit quand le cours baisse ;
- la dérive — ces signaux sont bien ceux que le graphique affiche.

Le complément de l'historique, l'export et le bandeau sont couverts ici
aussi, sur la fausse source de `test_history.py`.

Lancement :
    python tests/test_analysis.py
"""

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_history import DEBUT, HEURE, Banc, hub_sur  # noqa: E402,I001

from btcterm import analysis, sources  # noqa: E402
from btcterm.history import PAGE  # noqa: E402
from btcterm.hub import MarketHub  # noqa: E402


def marche(n=600, derive=0.0, graine=7):
    """Une marche aléatoire, au format des chandeliers."""
    alea = np.random.default_rng(graine)
    close = 50_000 * np.exp(np.cumsum(alea.normal(derive, 0.01, n)))
    return pd.DataFrame({
        "time": pd.date_range("2024-01-01", periods=n, freq="h"),
        "open": np.r_[close[0], close[:-1]], "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": alea.uniform(10, 100, n),
    })


def avec_signaux(df, positions):
    """Remplace les signaux calculés par ceux qu'on veut éprouver."""
    def faux(frame):
        work = frame[["open", "close"]].copy()
        work["signal"] = 0
        for i, grade in positions.items():
            work.iloc[i, work.columns.get_loc("signal")] = grade
        return work
    return faux


def test_ce_sont_les_signaux_du_graphique():
    from terminal.charts import prepare_price_frame

    df = marche()
    graphique = prepare_price_frame(df)["signal"]
    assert (analysis.price_signals(df)["signal"] == graphique).all()
    assert (graphique != 0).sum() > 5, "marche trop plate pour le test"
    print("  ✓ mêmes signaux que le panneau prix")


def test_pas_de_regard_sur_le_futur():
    df = marche()
    entier = analysis.price_signals(df)["signal"]
    tronque = analysis.price_signals(df.iloc[:400])["signal"]
    assert (entier.iloc[:400] == tronque).all(), "un signal passé a changé"

    #: Entrée à l'ouverture suivante, sortie à la clôture de l'horizon.
    r = analysis.forward_return(df, 5)
    attendu = (df["close"].iloc[15] / df["open"].iloc[11] - 1) * 100
    assert abs(r.iloc[10] - attendu) < 1e-9
    assert r.iloc[-5:].isna().all(), "fin de série sans horizon complet"
    print("  ✓ signaux stables, entrée à l'ouverture suivante")


def test_un_marche_qui_monte_ne_prouve_rien():
    """Tout monte de 1 % par bougie : l'achat gagne, sans aucune avance."""
    n = 400
    close = 1000 * 1.01 ** np.arange(n)
    df = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=n, freq="h"),
                       "open": close / 1.01, "close": close})
    vrai = analysis.price_signals
    analysis.price_signals = avec_signaux(df, {50: 2, 120: 2, 200: -2})
    try:
        stats = analysis.signal_stats(df, horizons=(5,))
    finally:
        analysis.price_signals = vrai
    achat, *_, vente = stats["rows"]
    cellule = achat["cells"][5]
    assert cellule["mean"] > 0 and cellule["hit"] == 100
    assert abs(cellule["edge"]) < 1e-6, "le simple fait de détenir, compté en avance"

    #: La vente échoue toujours : un signal de vente veut une baisse.
    assert vente["cells"][5]["hit"] == 0
    print("  ✓ gagner dans un marché haussier n'est pas une avance")


def test_le_sens_d_un_signal_de_vente():
    """La baisse suit la vente : réussite, et avance positive."""
    n = 400
    close = np.full(n, 1000.0)
    close[101:] = 900.0
    df = pd.DataFrame({"time": pd.date_range("2024-01-01", periods=n, freq="h"),
                       "open": np.r_[close[0], close[:-1]], "close": close})
    vrai = analysis.price_signals
    analysis.price_signals = avec_signaux(df, {99: -2})
    try:
        stats = analysis.signal_stats(df, horizons=(5,))
    finally:
        analysis.price_signals = vrai
    cellule = stats["rows"][3]["cells"][5]
    assert stats["rows"][3]["grade"] == -2 and cellule["count"] == 1
    assert cellule["mean"] < 0 and cellule["hit"] == 100 and cellule["edge"] > 0
    print("  ✓ une vente suivie d'une baisse est une réussite")


def test_rien_a_mesurer():
    assert analysis.signal_stats(marche(100)) is None, "historique trop court"
    demo = marche()
    demo.attrs["demo"] = True
    assert analysis.signal_stats(demo) is None
    assert analysis.timeframe_summary(demo) is None
    assert analysis.timeframe_summary(marche(100)) is None
    print("  ✓ ni la démonstration ni un historique court ne font un bilan")


def test_l_etat_d_un_intervalle():
    etat = analysis.timeframe_summary(marche(300, derive=0.01))
    assert etat["trend"] == "up" and etat["gap"] > 0 and etat["rsi"] > 50
    etat = analysis.timeframe_summary(marche(300, derive=-0.01))
    assert etat["trend"] == "down" and etat["gap"] < 0 and etat["rsi"] < 50
    print("  ✓ tendance, écart à la MA 200 et RSI")


def test_le_complement_va_jusqu_au_debut():
    banc = Banc(2500)
    with banc as binance:
        h = banc.base()
        assert h.extend_back("1h") is True, "store vide : première page"
        assert h.extend_back("1h") is True
        assert h.extend_back("1h") is False, "début de l'historique atteint"
        binance.appels.clear()
        assert h.extend_back("1h") is False and not binance.appels
        serie = h.stored("1h", 5000)
        assert serie["open_time"].iloc[0] == DEBUT
        assert (serie["open_time"].diff().dropna() == HEURE).all()
    print("  ✓ recul page par page, sans trou, puis silence")


def test_la_boucle_de_fond_tient_la_panne_et_l_arret():
    banc = Banc(1500)
    with banc as binance:
        hub = hub_sur(banc)
        hub.BACKFILL_DELAY = hub.BACKFILL_PAUSE = hub.KLINES_RETRY = 0.0
        hub.BACKFILL_INTERVALS = ("1h",)

        #: Une panne au premier appel : la boucle patiente et reprend.
        appels = {"n": 0}
        reel = sources.fetch_klines

        def capricieuse(*a, **k):
            appels["n"] += 1
            if appels["n"] == 1:
                raise ConnectionError("injoignable")
            return reel(*a, **k)

        sources.fetch_klines = capricieuse
        hub._backfill_loop()
        assert hub.history.stored("1h", 5000)["open_time"].iloc[0] == DEBUT

        #: L'arrêt du hub la coupe sans attendre la fin.
        hub._observe_stop.set()
        hub.BACKFILL_DELAY = 30.0
        debut = time.monotonic()
        hub._backfill_loop()
        assert time.monotonic() - debut < 1
        hub.history.close()
    print("  ✓ panne traversée, arrêt immédiat")


def test_les_lectures_profondes_excluent_la_bougie_en_cours():
    banc = Banc(1500)
    with banc as binance:
        hub = hub_sur(banc)
        hub.klines("1h", 100)
        binance.appels.clear()
        profond = hub.deep_klines("1h")
        assert not binance.appels, "le disque suffisait"
        assert profond["open_time"].iloc[-1] == DEBUT + 1498 * HEURE
        hub.history.close()

    #: Sans disque : la dernière page, moins la bougie en cours.
    banc = Banc(1500)
    with banc:
        sans = MarketHub(collect_news=False, keep_journal=False)
        assert sans.deep_klines("1h")["open_time"].iloc[-1] == DEBUT + 1498 * HEURE
    print("  ✓ bilan et export sur des bougies closes uniquement")


def test_l_export_csv():
    from terminal.app import create_app

    banc = Banc(1500)
    with banc as binance:
        hub = hub_sur(banc)
        hub.klines("1h", 100)
        web = create_app(hub).server.test_client()

        reponse = web.get("/api/export?interval=1h")
        assert reponse.status_code == 200 and reponse.mimetype == "text/csv"
        assert "btcusdt-1h.csv" in reponse.headers["Content-Disposition"]
        lignes = reponse.get_data(as_text=True).strip().splitlines()
        assert lignes[0].startswith("time,open,high,low,close,volume")
        assert len(lignes) == 1 + PAGE - 1, len(lignes)

        assert web.get("/api/export?interval=7m").status_code == 400

        #: Rien sur le disque et Binance muet : pas de fausses bougies.
        binance.panne = True
        hub._klines_retry_at = 0.0
        assert web.get("/api/export?interval=4h").status_code == 404
        hub.history.close()
    print("  ✓ CSV des bougies closes, jamais la démonstration")


def test_le_panneau_rend_ses_reserves():
    from terminal.panels import analysis as panneau

    df = marche(800, derive=0.0005)
    stats = analysis.signal_stats(df)
    rendu = str(panneau.render_stats(stats))
    assert "rester investi" in rendu and "sans frais" in rendu
    assert "achat fort" in rendu and "vente forte" in rendu
    assert "historique insuffisant" in str(panneau.render_stats(None))

    class Hub:
        def klines(self, interval, limit):
            return marche(limit)

    tableau = str(panneau.render_summary(Hub()))
    for intervalle in panneau.SUMMARY_INTERVALS:
        assert intervalle in tableau
    print("  ✓ réserves écrites en face des chiffres, tous les intervalles")


def test_le_bandeau_24_h():
    from terminal.fx import Display
    from terminal.header import range_text

    ticker = {"highPrice": "78000", "lowPrice": "76000",
              "quoteVolume": "1200000000"}
    assert range_text(ticker) == "H 78,000 · L 76,000 · vol 1.20 Md$"
    assert range_text(ticker, Display(0.5, "€")) == "H 39,000 · L 38,000 · vol 0.60 Md€"
    assert range_text({}) == ""
    print("  ✓ plus haut, plus bas et volume, en devise, et vides sans réponse")


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    print(f"\nBilan des signaux — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le bilan mesure sans regarder le futur, et contre une référence.\n")
