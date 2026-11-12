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


#: L'indice de la première MA 200 : deux cents bougies, la dernière comprise.
TREND_FIRST_VALID = 199


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


def rampe_puis_krach(n=400, krach=320):
    """Une montée régulière, un effondrement de moitié sur une bougie, un plat."""
    close = np.linspace(100.0, 300.0, n)
    close[krach:] = close[krach - 1] / 2
    close = close + np.sin(np.arange(n))   # le RSI veut des baisses aussi
    return pd.DataFrame({
        "time": pd.date_range("2020-01-01", periods=n, freq="D"),
        "open": np.r_[close[0], close[:-1]], "close": close})


def test_une_regle_ne_voit_pas_venir_le_krach():
    """La règle lit la clôture : elle encaisse la bougie qui la fait sortir.

    C'est l'erreur qui rend un backtest magnifique et faux : sortir à la
    clôture d'une bougie qu'on vient de voir s'effondrer, ou pire, avant.
    """
    stats = analysis.rule_stats(rampe_puis_krach(), fee_pct=0.0)
    rangs = {row["rule"]: row for row in stats["rows"]}
    assert rangs["ma200"]["drawdown"] < -45, "le krach a été évité : regard sur le futur"
    assert abs(rangs["ma200"]["total"] - rangs["hold"]["total"]) < 15
    print("  ✓ le krach est encaissé en entier, la règle ne sort qu'après")


def test_les_regles_partent_du_meme_jour():
    df = marche(800, derive=0.0005)
    stats = analysis.rule_stats(df, fee_pct=0.0)
    assert stats["first"] == df["time"].iloc[TREND_FIRST_VALID + 1]
    detention = stats["rows"][0]
    debut, fin = TREND_FIRST_VALID + 1, len(df) - 1
    attendu = (df["open"].iloc[fin] / df["open"].iloc[debut] - 1) * 100
    assert abs(detention["total"] - attendu) < 1e-6
    assert detention["exposure"] == 100 and detention["entries"] == 1
    print("  ✓ même période pour toutes, détention égale au cours")


def test_les_frais_comptent():
    df = marche(800)
    sans = {r["rule"]: r for r in analysis.rule_stats(df, fee_pct=0.0)["rows"]}
    avec = {r["rule"]: r for r in analysis.rule_stats(df, fee_pct=0.1)["rows"]}
    assert sans["cross"]["entries"] > 5, "pas assez de passages pour le test"
    assert avec["cross"]["total"] < sans["cross"]["total"]
    #: Deux passages par entrée : la règle qui bouge le plus paie le plus.
    perte = sans["cross"]["total"] - avec["cross"]["total"]
    assert perte > sans["hold"]["total"] - avec["hold"]["total"]
    assert analysis.rule_stats(marche(150)) is None
    print("  ✓ chaque passage se paie, la détention une seule fois")


def test_les_perpetuels_des_autres_plateformes():
    """Les réponses réelles de Bybit et d'OKX, réduites à ce qui est lu."""
    class Reponse:
        def __init__(self, charge):
            self.charge = charge

        def raise_for_status(self):
            pass

        def json(self):
            return self.charge

    def faux_get(url, params=None, timeout=None):
        if "tickers" in url and "bybit" in url:
            return Reponse({"result": {"list": [{
                "fundingRate": "0.00007", "markPrice": "77626.3",
                "indexPrice": "77664.6", "openInterestValue": "5356868002.9"}]}})
        champ = {"funding-rate": {"fundingRate": "0.0001"},
                 "mark-price": {"markPx": "77635.4"},
                 "index-tickers": {"idxPx": "77661.3"},
                 "open-interest": {"oiUsd": "2656956416.1"}}
        cle = next(c for c in champ if c in url)
        return Reponse({"data": [champ[cle]]})

    vrai = sources._http.get
    sources._http.get = faux_get
    try:
        assert sources.fetch_bybit_perp()["oi_usd"] == 5356868002.9
        okx = sources.fetch_okx_perp()
        assert okx["funding_rate"] == 0.0001 and okx["index_price"] == 77661.3
    finally:
        sources._http.get = vrai
    print("  ✓ champs de Bybit et d'OKX lus, ramenés au même contrat")


def test_une_plateforme_muette_n_efface_pas_les_autres():
    from terminal.fx import Display
    from terminal.panels import perp as panneau

    hub = MarketHub(collect_news=False, keep_journal=False)
    bybit = {"funding_rate": 0.00007, "mark_price": 100.1, "index_price": 100.0,
             "oi_usd": 5e9}

    def panne():
        raise ConnectionError("injoignable")

    vrais = (sources.fetch_bybit_perp, sources.fetch_okx_perp,
             sources.fetch_perp_snapshot)
    sources.fetch_bybit_perp = lambda: bybit
    sources.fetch_okx_perp = panne
    sources.fetch_perp_snapshot = lambda symbol: (_ for _ in ()).throw(
        ConnectionError("injoignable"))
    try:
        lignes = hub.perp_compare()
    finally:
        (sources.fetch_bybit_perp, sources.fetch_okx_perp,
         sources.fetch_perp_snapshot) = vrais
    assert set(lignes) == {"Bybit"}, lignes
    assert set(hub.failing_sources()) >= {"perp_okx"}

    texte = str(panneau._compare({"Binance": {**bybit, "oi_usd": 3e9},
                                  "Bybit": bybit}))
    assert "BIN" in texte and "BYB" in texte and "+0.0070 %" in texte
    assert "(+0.100)" in texte, "prime attendue"
    assert "OI total 8.00 Md$" in texte
    assert "4.00 Md€" in str(panneau._compare(
        {"Binance": {**bybit, "oi_usd": 3e9}, "Bybit": bybit},
        Display(0.5, "€")))
    assert panneau._compare({}) == ""
    print("  ✓ comparaison partielle plutôt que rien, panne nommée au bandeau")


def test_une_position_longue():
    etat = analysis.position_state("long", 0.5, 100.0, 110.0, stop=95.0)
    assert etat["value"] == 55.0 and etat["pnl"] == 5.0
    assert abs(etat["pnl_pct"] - 10) < 1e-9
    #: Le stop est 13,6 % sous le cours ; le toucher perd 7,50 d'ici.
    assert abs(etat["stop_pct"] - 15 / 110 * 100) < 1e-9
    assert etat["stop_loss"] == -7.5
    #: 5 de gain pour 2,50 de risque pris à l'entrée.
    assert etat["r"] == 2.0
    print("  ✓ résultat, distance au stop et multiple du risque")


def test_une_position_courte_gagne_a_la_baisse():
    etat = analysis.position_state("short", 2.0, 100.0, 90.0, stop=105.0)
    assert etat["pnl"] == 20.0 and abs(etat["pnl_pct"] - 10) < 1e-9
    assert etat["stop_loss"] == -30.0 and etat["r"] == 2.0
    print("  ✓ le sens est respecté partout, stop compris")


def test_un_stop_incoherent_ne_fait_pas_de_multiple():
    """Un stop du mauvais côté n'est pas un risque : pas de R inventé."""
    etat = analysis.position_state("long", 1.0, 100.0, 110.0, stop=105.0)
    assert etat["r"] is None and etat["stop_pct"] > 0

    #: Un stop franchi se lit comme tel.
    etat = analysis.position_state("long", 1.0, 100.0, 90.0, stop=95.0)
    assert etat["stop_pct"] < 0

    for manque in (("long", None, 100, 110), ("long", 1, None, 110),
                   ("long", 1, 100, None), ("long", 0, 100, 110)):
        assert analysis.position_state(*manque) is None, manque
    print("  ✓ champs vides sans calcul, stop incohérent sans multiple")


def test_la_taille_suit_le_risque_et_la_volatilite():
    taille = analysis.position_size(10_000, 1, 2_000, 80_000)
    #: 100 $ risqués, stop à 2 000 $ : 0,05 BTC, soit 4 000 $.
    assert taille["risk"] == 100 and abs(taille["quantity"] - 0.05) < 1e-12
    assert abs(taille["value"] - 4_000) < 1e-9
    assert abs(taille["leverage"] - 0.4) < 1e-12

    #: Un marché deux fois plus nerveux : deux fois moins de bitcoins.
    nerveux = analysis.position_size(10_000, 1, 4_000, 80_000)
    assert abs(nerveux["quantity"] - taille["quantity"] / 2) < 1e-12

    assert analysis.position_size(0, 1, 2_000, 80_000) is None
    assert analysis.position_size(10_000, 1, None, 80_000) is None
    print("  ✓ risque constant, taille inverse à la distance du stop")


def test_le_panneau_position_rend_ce_qu_il_sait():
    from terminal.fx import DOLLARS, Display
    from terminal.panels import position as panneau

    assert "saisir" in str(panneau.render_state(None, DOLLARS))
    assert "saisir" in str(panneau.render_size(None, "long", DOLLARS))
    etat = analysis.position_state("long", 0.5, 100.0, 110.0, stop=95.0)
    rendu = str(panneau.render_state(etat, Display(0.5, "€")))
    assert "+2.50 €" in rendu and "+10.00 %" in rendu and "+2.00 R" in rendu
    taille = analysis.position_size(10_000, 1, 2_000, 80_000)
    assert "78,000.00 $" in str(panneau.render_size(taille, "long", DOLLARS))
    assert "82,000.00 $" in str(panneau.render_size(taille, "short", DOLLARS))
    print("  ✓ stop proposé du bon côté, montants dans la devise")


def test_l_etat_du_jour_d_une_regle():
    stats = analysis.rule_stats(rampe_puis_krach(), fee_pct=0.0)
    rangs = {row["rule"]: row for row in stats["rows"]}
    assert rangs["hold"]["invested"] is True
    #: Après le krach, sous sa MA 200 : hors marché, depuis la bougie du krach.
    assert rangs["ma200"]["invested"] is False
    assert rangs["ma200"]["since"] == pd.Timestamp("2020-01-01") + pd.Timedelta(days=320)
    print("  ✓ investie ou hors marché, depuis la bonne bougie")


def test_les_rendements_mensuels():
    from terminal.panels import analysis as panneau

    df = pd.DataFrame({
        "time": pd.to_datetime(["2024-11-01", "2024-12-01", "2025-01-01"]),
        "open": [100.0, 110.0, 100.0], "close": [110.0, 99.0, 150.0]})
    stats = analysis.monthly_returns(df)
    assert abs(stats["grid"][2024][11] - 10) < 1e-9
    assert abs(stats["grid"][2024][12] + 10) < 1e-9
    #: Les mois se composent : +10 % puis -10 % ne font pas zéro.
    assert abs(stats["years"][2024] + 1) < 1e-9
    assert stats["months"][1]["up"] == 100 and 3 not in stats["months"]

    demo = df.copy()
    demo.attrs["demo"] = True
    assert analysis.monthly_returns(demo) is None

    rendu = str(panneau.render_months(stats))
    assert "2025" in rendu and "+50" in rendu and "déc" in rendu
    #: La réserve est écrite : deux années, deux points par mois.
    assert "2 années" in rendu and "pas une saisonnalité" in rendu
    assert "100 % de mois haussiers" in rendu
    assert "historique insuffisant" in str(panneau.render_months(None))
    print("  ✓ mois composés en années, moyenne par mois, carte rendue")


def test_quand_le_bitcoin_bouge():
    """Le profil horaire range chaque bougie à l'heure et au jour de son
    ouverture dans le fuseau donné — le jeudi 12 novembre 2026, 14 h UTC,
    est 21 h à Phnom Penh —, et en tire amplitude, rendement et part de
    hausses ; la carte dit tout cela en points de base, ou son absence."""
    from datetime import timedelta, timezone
    from terminal.panels import analysis as panneau

    temps = pd.to_datetime(["2026-11-12 14:00", "2026-11-19 14:00",
                            "2026-11-12 15:00", "2026-11-15 02:00"])
    bougies = pd.DataFrame({
        "time": temps,
        "open": [100.0, 100.0, 100.0, 100.0],
        "high": [101.0, 102.0, 100.5, 100.2],
        "low": [99.0, 99.0, 99.5, 99.9],
        "close": [100.5, 99.5, 100.2, 100.0],
    })
    phnom_penh = timezone(timedelta(hours=7))
    profil = analysis.hourly_profile(bougies, phnom_penh)
    jeudi_21h = profil["grid"][3][21]
    assert jeudi_21h["n"] == 2, "deux jeudis à 21 h"
    assert jeudi_21h["range"] == 2.5, "amplitudes de 2 % et 3 %"
    assert abs(jeudi_21h["ret"]) < 1e-9 and jeudi_21h["up"] == 50.0
    assert profil["grid"][3][22]["n"] == 1
    assert abs(profil["grid"][6][9]["range"] - 0.3) < 1e-9, "dimanche, 2 h UTC = 9 h"
    assert profil["hours"][21]["n"] == 2 and profil["days"][3]["n"] == 3
    assert profil["bars"] == 4 and profil["offset"] == 7 * 3600
    assert analysis.WEEKDAYS[3] == "jeu."

    demo = bougies.copy()
    demo.attrs["demo"] = True
    assert analysis.hourly_profile(demo, phnom_penh) is None
    assert analysis.hourly_profile(bougies.iloc[0:0], phnom_penh) is None

    carte = str(panneau.render_hours(profil))
    assert "250" in carte, "2,5 % s'écrit 250 points de base"
    assert "UTC+7" in carte and "jeu." in carte and "sens" in carte
    assert "insuffisant" in str(panneau.render_hours(None))
    print("  ✓ heures et jours dans le fuseau, amplitude en points de base, "
          "absence dite")


def test_la_volatilite_d_un_intervalle():
    etat = analysis.timeframe_summary(marche())
    assert 0 < etat["atr_pct"] < 5
    sans = marche().drop(columns=["high", "low"])
    assert analysis.timeframe_summary(sans)["atr_pct"] is None
    print("  ✓ ATR en % du cours, absent sans extrêmes")


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



def test_le_panneau_position_dit_ce_qui_manque():
    """Chaque empêchement a son message, et un stop franchi se dit."""
    from terminal.fx import DOLLARS
    from terminal.panels import position as panneau

    gap = panneau.size_gap
    assert gap(10_000, 1, 2, 2_000.0, 74_000.0) is None
    assert "capital et un risque" in gap(None, 1, 2, 2_000.0, 74_000.0)
    assert "capital et un risque" in gap(10_000, 0, 2, 2_000.0, 74_000.0)
    assert "100 %" in gap(10_000, 150, 2, 2_000.0, 74_000.0)
    assert "multiple d'ATR" in gap(10_000, 1, None, 2_000.0, 74_000.0)
    assert "multiple d'ATR" in gap(10_000, 1, 0, 2_000.0, 74_000.0)
    assert "cours indisponible" in gap(10_000, 1, 2, 2_000.0, None)
    assert "ATR journalier indisponible" in gap(10_000, 1, 2, None, 74_000.0)
    assert "ATR journalier" in str(panneau.render_size(
        None, "long", DOLLARS, gap(10_000, 1, 2, None, 74_000.0)))
    assert "cours indisponible" in str(panneau.render_state(
        None, DOLLARS, "cours indisponible : la position ne se suit pas"))

    # Un multiple qui pousserait le stop d'un long sous zéro ne propose rien.
    geante = analysis.position_size(10_000, 1, 80_000.0, 74_000.0)
    assert "sous zéro" in str(panneau.render_size(geante, "long", DOLLARS))
    assert "154,000.00 $" in str(panneau.render_size(geante, "short", DOLLARS))

    # Un short dont le stop est sous le cours : il aurait déjà joué.
    franchi = analysis.position_state("short", 0.5, 70_000.0, 73_600.0,
                                      stop=65_000.0)
    rendu = str(panneau.render_state(franchi, DOLLARS))
    assert "stop franchi" in rendu and "jusqu'au stop" not in rendu
    assert "de 11.68 %" in rendu, rendu
    sain = analysis.position_state("short", 0.5, 70_000.0, 73_600.0,
                                   stop=80_000.0)
    rendu = str(panneau.render_state(sain, DOLLARS))
    assert "jusqu'au stop" in rendu and "stop franchi" not in rendu
    print("  ✓ position : chaque empêchement dit, stop franchi nommé")


def test_niveaux_cles():
    """Veille, semaine et mois passés, ouvertures en cours — en UTC, la
    semaine partant du lundi ; un niveau partagé n'en fait qu'un, et une
    bougie du jour absente n'invente pas d'ouverture."""
    from btcterm.analysis import key_levels

    jours = pd.date_range("2026-09-01", "2026-11-04", freq="D")
    n = len(jours)
    bougies = pd.DataFrame({
        "time": jours,
        "open": [1000.0 + i for i in range(n)],
        "high": [1100.0 + i for i in range(n)],
        "low": [900.0 + i for i in range(n)],
        "close": [1000.5 + i for i in range(n)],
    })
    # Un creux le samedi 31 octobre, dans la semaine passée comme dans le
    # mois passé : il est le plus bas des deux.
    bougies.loc[bougies["time"] == "2026-10-31", "low"] = 500.0
    rang = {d: i for i, d in enumerate(jours)}
    maintenant = pd.Timestamp("2026-11-04 10:00", tz="UTC")   # un mercredi
    niveaux = {niveau["label"]: niveau for niveau in key_levels(bougies, maintenant)}

    # La veille : le mardi 3 novembre.
    assert niveaux["H J-1"]["price"] == 1100.0 + rang[pd.Timestamp("2026-11-03")]
    assert niveaux["B J-1"]["price"] == 900.0 + rang[pd.Timestamp("2026-11-03")]
    assert niveaux["O J"]["price"] == 1000.0 + rang[pd.Timestamp("2026-11-04")]
    # La semaine passée : du lundi 26 octobre au dimanche 1er novembre.
    assert niveaux["H S-1"]["price"] == 1100.0 + rang[pd.Timestamp("2026-11-01")]
    assert niveaux["O S"]["price"] == 1000.0 + rang[pd.Timestamp("2026-11-02")]
    # Le creux du 31 octobre est le plus bas de la semaine ET du mois
    # passés : une seule ligne, deux noms.
    assert niveaux["B S-1/M-1"]["price"] == 500.0
    assert "B S-1" not in niveaux and "B M-1" not in niveaux
    # Le mois passé : octobre.
    assert niveaux["H M-1"]["price"] == 1100.0 + rang[pd.Timestamp("2026-10-31")]
    assert niveaux["O M"]["price"] == 1000.0 + rang[pd.Timestamp("2026-11-01")]

    # Binance muet ce matin : pas de bougie du jour, pas d'ouverture du jour,
    # mais la veille reste juste.
    sans_jour = key_levels(bougies[bougies["time"] < "2026-11-04"], maintenant)
    assert "O J" not in {n["label"] for n in sans_jour}
    assert any(n["label"] == "H J-1" for n in sans_jour)
    assert key_levels(bougies.iloc[0:0], maintenant) == []
    print("  ✓ veille, semaine du lundi, mois ; niveau partagé fusionné ; "
          "pas d'ouverture inventée")


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    print(f"\nBilan des signaux — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le bilan mesure sans regarder le futur, et contre une référence.\n")
