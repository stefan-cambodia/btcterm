#!/usr/bin/env python3
"""
Moteur d'alertes : seuils, fronts montants, cadences, journal.

Tout se joue au temps simulé et sur un hub factice : une alerte qui
sonne en rafale, ou qui ne sonne pas, ne se constate pas en regardant
l'écran au bon moment. Les points guettés :

- un seuil de prix sonne une fois, se désarme, et ne se réarme qu'après
  l'hystérésis — un cours qui oscille sur le seuil ne sonne pas en
  rafale ;
- les règles d'état sonnent sur le front montant, sous délai de garde ;
- les contrôles coûteux (financement, news) ne tournent qu'à leur
  cadence, et la première lecture des news arme sans sonner ;
- un jour de flux ETF sonne une fois, jamais celui présent au démarrage ;
- le réseau chargé — mempool, blocs lents — a un front par grandeur ;
- chaque sonnerie part au journal.

Aucun réseau n'est touché.

Lancement :
    python tests/test_alerts.py
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from btcterm.alerts import (AlertEngine, COOLDOWN, DEFAULT_CONFIG,  # noqa: E402
                            KLINE_LIMIT, SLOW_EVERY, normalize_config)
from btcterm.journal import Journal  # noqa: E402
from test_journal import opportunity  # noqa: E402

T0 = 1_000_000.0


def klines_frame(closes, demo: bool = False) -> pd.DataFrame:
    """Des chandeliers horaires façonnés à la main, pour les règles
    relatives — seule la clôture leur importe."""
    df = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=len(closes), freq="1h"),
        "close": [float(c) for c in closes],
    })
    if demo:
        df.attrs["demo"] = True
    return df


class FakeHub:
    """Le strict nécessaire aux règles, tout étant réglable."""

    def __init__(self):
        self.price = 100_000.0
        self.funding = 0.0001
        self.totals = {"long": 0.0, "short": 0.0, "btc": 0.0, "count": 0}
        self.liquidations = SimpleNamespace(
            totals=lambda window=300: self.totals)
        #: Chandeliers plats par défaut : écart nul, RSI muet (NaN),
        #: aucun signal — les règles relatives se taisent.
        self.frame = klines_frame([100_000.0] * KLINE_LIMIT)
        #: Historique d'instantanés vide par défaut : la règle de
        #: dominance se tait tant que le journal n'a rien accumulé.
        self.snapshots = pd.DataFrame(columns=["time", "btc_dominance"])
        #: Contexte vide par défaut : flux ETF et réseau se taisent.
        self.etf = pd.DataFrame(columns=["Date", "Total"])
        self.mempool = pd.DataFrame(columns=["time", "value"])
        self.chain = {}
        #: DVOL vide par défaut : la règle de volatilité se tait.
        self.vol = pd.DataFrame(columns=["time", "open", "high", "low", "close"])

    def dvol(self):
        return self.vol

    def etf_flows(self):
        return self.etf

    def chain_chart(self, name="hash-rate", timespan="1year"):
        return self.mempool

    def chain_stats(self):
        return self.chain

    def reference_price(self):
        return self.price

    def perp_snapshot(self):
        return {"funding_rate": self.funding}

    def klines(self, interval="1h", limit=KLINE_LIMIT):
        return self.frame

    def market_snapshots(self, days=400):
        return self.snapshots


def engine(news=(), **config):
    e = AlertEngine(fetch_news=lambda: list(news))
    e.configure({**DEFAULT_CONFIG, **config})
    return e


def test_reglages_non_fiables():
    """Le Store vient du localStorage : tout doit retomber sur ses pieds."""
    assert normalize_config(None) == DEFAULT_CONFIG
    assert normalize_config("n'importe quoi") == DEFAULT_CONFIG
    config = normalize_config({
        "news_score": 60, "liq_burst_musd": -3, "inconnue": 1,
        "price_levels": [{"level": 120_000, "dir": "above"},
                         {"level": 120_000, "dir": "above"},
                         {"level": "abc", "dir": "above"},
                         {"level": 90_000, "dir": "ailleurs"}],
    })
    assert config["news_score"] == 60
    assert config["liq_burst_musd"] == DEFAULT_CONFIG["liq_burst_musd"]
    assert "inconnue" not in config
    assert config["price_levels"] == [{"level": 120_000.0, "dir": "above"}]

    # La normalisation doit rendre une liste NEUVE : une copie
    # superficielle partagerait `price_levels` avec DEFAULT_CONFIG, et
    # le premier seuil posé muterait les défauts de tout le processus —
    # le bug qui a rendu la pose muette dans le navigateur.
    un = normalize_config(None)
    un["price_levels"].append({"level": 1.0, "dir": "above"})
    assert DEFAULT_CONFIG["price_levels"] == [], "défauts partagés mutés"
    assert normalize_config(None)["price_levels"] == []
    print("  ✓ clés inconnues écartées, seuils malformés purgés, "
          "défauts jamais partagés")


def test_seuil_de_prix_et_hysteresis():
    hub = FakeHub()
    e = engine(price_levels=[{"level": 101_000, "dir": "above"}])

    e.evaluate(hub, now=T0)
    assert not e.alerts, "sonné sous le seuil"
    hub.price = 101_500.0
    e.evaluate(hub, now=T0 + 1)
    e.evaluate(hub, now=T0 + 2)
    assert len(e.alerts) == 1, "doit sonner une fois, pas en rafale"
    assert "101,000" in e.alerts[0].message

    # Redescendre juste sous le seuil ne réarme pas (hystérésis 0,2 %)…
    hub.price = 100_900.0
    e.evaluate(hub, now=T0 + 3)
    hub.price = 101_100.0
    e.evaluate(hub, now=T0 + 4)
    assert len(e.alerts) == 1, "réarmé sans hystérésis"
    # …s'en écarter franchement, si.
    hub.price = 100_500.0
    e.evaluate(hub, now=T0 + 5)
    hub.price = 101_200.0
    e.evaluate(hub, now=T0 + 6)
    assert len(e.alerts) == 2, "pas réarmé après l'hystérésis"
    print("  ✓ une sonnerie par franchissement, réarmement par hystérésis")


def test_rafale_sous_delai_de_garde():
    hub = FakeHub()
    e = engine()

    hub.totals = {"long": 9e6, "short": 4e6, "btc": 0, "count": 40}
    e.evaluate(hub, now=T0)
    assert len([a for a in e.alerts if a.kind == "liq"]) == 1
    assert "13.0 M$" in e.alerts[-1].message

    e.evaluate(hub, now=T0 + 5)          # la condition dure : silence
    hub.totals = {"long": 0.0, "short": 0.0, "btc": 0, "count": 0}
    e.evaluate(hub, now=T0 + 10)         # elle retombe
    hub.totals = {"long": 15e6, "short": 0.0, "btc": 0, "count": 10}
    e.evaluate(hub, now=T0 + 15)         # et clignote sous la garde
    assert len([a for a in e.alerts if a.kind == "liq"]) == 1, \
        "a sonné en rafale sous le délai de garde"

    hub.totals = {"long": 0.0, "short": 0.0, "btc": 0, "count": 0}
    e.evaluate(hub, now=T0 + COOLDOWN)
    hub.totals = {"long": 15e6, "short": 0.0, "btc": 0, "count": 10}
    e.evaluate(hub, now=T0 + COOLDOWN + 5)
    assert len([a for a in e.alerts if a.kind == "liq"]) == 2
    print("  ✓ front montant, délai de garde, re-sonnerie après")


def test_financement_et_cadence_lente():
    hub = FakeHub()
    appels = []
    e = AlertEngine(fetch_news=lambda: appels.append(1) or [])
    hub.funding = 0.0008  # 0,08 % / 8 h ≥ 0,05

    e.evaluate(hub, now=T0)
    assert [a.kind for a in e.alerts] == ["funding"]
    assert "+0.0800 %" in e.alerts[0].message

    # Entre deux cadences lentes, ni financement ni news ne sont relus.
    e.evaluate(hub, now=T0 + 10)
    assert len(appels) == 1
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    assert len(appels) == 2
    print("  ✓ financement extrême détecté, contrôles coûteux à 60 s")


def test_news_a_fort_score():
    hub = FakeHub()
    articles = [{"title": "ETF record", "score": 92}]
    e = AlertEngine(fetch_news=lambda: list(articles))
    e.configure(DEFAULT_CONFIG)

    e.evaluate(hub, now=T0)
    assert not [a for a in e.alerts if a.kind == "news"], \
        "la première lecture doit armer sans sonner"

    articles.append({"title": "La SEC approuve", "score": 85})
    articles.append({"title": "billet d'humeur", "score": 20})
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    news = [a for a in e.alerts if a.kind == "news"]
    assert len(news) == 1 and "La SEC approuve" in news[0].message
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    assert len([a for a in e.alerts if a.kind == "news"]) == 1, \
        "un article déjà vu a resonné"
    print("  ✓ arme sans sonner, sonne l'article neuf, jamais deux fois")


def test_ecart_d_arbitrage():
    hub = FakeHub()
    e = engine()
    e.evaluate(hub, [opportunity(net=0.3)], now=T0)
    assert not e.alerts, "sonné sous le seuil"
    e.evaluate(hub, [opportunity(net=0.7)], now=T0 + 1)
    assert len(e.alerts) == 1 and "Kraken → Binance" in e.alerts[0].message
    print("  ✓ sonne au-delà du net configuré, pas en deçà")


def test_reglages_rsi_incoherents():
    """Un couple RSI inversé ou hors bornes retombe entier sur les
    défauts — sans quoi les deux règles sonneraient en permanence."""
    config = normalize_config({"rsi_overbought": 20, "rsi_oversold": 80})
    assert config["rsi_overbought"] == DEFAULT_CONFIG["rsi_overbought"]
    assert config["rsi_oversold"] == DEFAULT_CONFIG["rsi_oversold"]
    config = normalize_config({"rsi_overbought": 140})
    assert config["rsi_overbought"] == DEFAULT_CONFIG["rsi_overbought"]
    config = normalize_config({"rsi_overbought": 75, "rsi_oversold": 25})
    assert (config["rsi_overbought"], config["rsi_oversold"]) == (75, 25)
    print("  ✓ couple RSI incohérent remis aux défauts, cohérent gardé")


def test_ecart_ma200():
    """L'élastique tendu sonne au front montant, sur la bougie close."""
    hub = FakeHub()
    e = engine(ma200_gap_pct=10.0)

    # 15 % au-dessus d'une base plate — mais sur la bougie COURANTE
    # seulement : la close est encore sage, silence.
    hub.frame = klines_frame([100_000.0] * (KLINE_LIMIT - 1) + [115_000.0])
    e.evaluate(hub, now=T0)
    assert not e.alerts, "la bougie courante a fait sonner"

    # L'étirement atteint la bougie close : une sonnerie, pas deux.
    hub.frame = klines_frame(
        [100_000.0] * (KLINE_LIMIT - 2) + [115_000.0, 115_000.0])
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    trend = [a for a in e.alerts if a.kind == "trend"]
    assert len(trend) == 1, "l'état qui dure a resonné"
    assert "MA 200" in trend[0].message and "+" in trend[0].message
    print("  ✓ écart MA 200 : bougie close seulement, front montant")


def test_ecart_ma200_muet_sur_la_demo():
    """Hors ligne, la série de démonstration ne fait sonner personne."""
    hub = FakeHub()
    e = engine(ma200_gap_pct=1.0)
    hub.frame = klines_frame(
        [100_000.0] * (KLINE_LIMIT - 2) + [130_000.0, 130_000.0], demo=True)
    e.evaluate(hub, now=T0)
    assert not e.alerts, "la démo a sonné"
    print("  ✓ règles relatives muettes sur la série de démonstration")


def test_rsi_extreme():
    """Chaque borne du RSI a son front : surachat puis survente."""
    hub = FakeHub()
    e = engine(rsi_overbought=80, rsi_oversold=20)

    # Une base qui respire (le RSI de Wilder est NaN sans aucune perte),
    # puis vingt hausses de 1 % d'affilée : bougie close en surachat.
    base = [100_000.0 * (1 + 0.001 * (-1) ** k)
            for k in range(KLINE_LIMIT - 21)]
    montee = [100_000.0 * 1.01 ** k for k in range(20)]
    hub.frame = klines_frame(base + montee + [montee[-1]])
    e.evaluate(hub, now=T0)
    rsi = [a for a in e.alerts if a.kind == "rsi"]
    assert len(rsi) == 1 and "surachat" in rsi[0].message, rsi

    # Puis vingt baisses : survente, l'autre front.
    descente = [montee[-1] * 0.99 ** k for k in range(1, 21)]
    hub.frame = klines_frame(
        base[:-20] + montee + descente + [descente[-1]])
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    rsi = [a for a in e.alerts if a.kind == "rsi"]
    assert len(rsi) == 2 and "survente" in rsi[1].message, rsi
    print("  ✓ RSI extrême : surachat et survente, un front chacun")


def test_signal_fort_une_fois_par_bougie():
    """Un ±2 sonne à sa bougie, et jamais deux fois sur la même."""
    hub = FakeHub()
    e = engine()

    # Une longue glissade met le RSI en survente, puis la bougie close
    # rebondit : sortie de survente, signal +2 sur cette bougie.
    glissade = [100_000.0 * 0.995 ** k for k in range(40)]
    rebond = glissade[-1] * 1.03
    closes = ([100_000.0] * (KLINE_LIMIT - 42) + glissade
              + [rebond, rebond])
    hub.frame = klines_frame(closes)
    e.evaluate(hub, now=T0)
    signaux = [a for a in e.alerts if a.kind == "signal"]
    assert len(signaux) == 1, "le rebond devait signer un achat fort"
    assert "achat fort" in signaux[0].message

    # La même bougie relue à la cadence suivante ne resonne pas.
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    assert len([a for a in e.alerts if a.kind == "signal"]) == 1

    # Débrayé, le signal se tait même sur une bougie neuve.
    e.configure({**DEFAULT_CONFIG, "signal_strong": False})
    hub.frame = klines_frame(closes[1:] + [rebond])
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    assert len([a for a in e.alerts if a.kind == "signal"]) == 1
    print("  ✓ signal fort : une sonnerie par bougie, débrayable")


def dominance_series(now, heures: float, debut: float, fin: float):
    """Un historique d'instantanés au pas de l'heure, dominance glissant
    linéairement de `debut` à `fin` sur `heures` heures."""
    points = int(heures) + 1
    return pd.DataFrame({
        "time": pd.to_datetime(
            [now - (points - 1 - k) * 3600 for k in range(points)], unit="s"),
        "btc_dominance": [debut + (fin - debut) * k / (points - 1)
                          for k in range(points)],
    })


def test_glissement_de_dominance():
    """La rotation du marché sonne au front montant — et la règle se
    tait tant que l'historique ne couvre pas la fenêtre."""
    hub = FakeHub()
    e = engine(dominance_shift_pts=1.5)

    # Deux heures d'historique seulement : silence, quel que soit l'écart.
    hub.snapshots = dominance_series(T0, 2, 55.0, 60.0)
    e.evaluate(hub, now=T0)
    assert not e.alerts, "un historique trop court a fait sonner"

    # Vingt-cinq heures, +2 pt sur la série entière : une sonnerie, pas
    # deux. La référence est le point le plus proche de la fenêtre —
    # 23 h d'âge, d'où +1,8 pt affiché, pas les +2 de bout en bout.
    hub.snapshots = dominance_series(T0, 25, 57.0, 59.0)
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    sonneries = [a for a in e.alerts if a.kind == "dominance"]
    assert len(sonneries) == 1, sonneries
    assert "+1.8 pt" in sonneries[0].message, sonneries[0].message
    assert "59.0 %" in sonneries[0].message
    print("  ✓ glissement de dominance : fenêtre exigée, front montant")


def test_glissement_de_dominance_sous_le_seuil():
    hub = FakeHub()
    e = engine(dominance_shift_pts=1.5)
    hub.snapshots = dominance_series(T0, 25, 58.0, 59.0)  # +1 pt
    e.evaluate(hub, now=T0)
    assert not [a for a in e.alerts if a.kind == "dominance"], \
        "un point de glissement est sous le seuil de 1,5"
    print("  ✓ sous le seuil : silence")


def etf_frame(*days) -> pd.DataFrame:
    """Des jours de flux (date ISO, total en M$), comme farside les livre."""
    return pd.DataFrame({
        "Date": pd.to_datetime([d for d, _ in days]),
        "IBIT": [0.0] * len(days),
        "Total": [float(t) for _, t in days],
    })


def test_flux_etf_une_fois_par_jour():
    """Un jour de flux est un événement daté : une sonnerie par date, et
    le jour présent au démarrage est tenu pour vu."""
    hub = FakeHub()
    e = engine(etf_flow_musd=500)

    # Première lecture : une sortie massive déjà publiée ne rejoue pas.
    hub.etf = etf_frame(("2026-08-20", -900))
    e.evaluate(hub, now=T0)
    assert not e.alerts, "le jour présent au démarrage a sonné"

    # Un jour neuf apparaît sous le seuil, puis se remplit au fil de la
    # soirée : la sonnerie vient quand le total franchit le seuil.
    hub.etf = etf_frame(("2026-08-20", -900), ("2026-08-21", 120))
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    assert not e.alerts, "un jour sous le seuil a sonné"
    hub.etf = etf_frame(("2026-08-20", -900), ("2026-08-21", 612))
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    sonneries = [a for a in e.alerts if a.kind == "etf"]
    assert len(sonneries) == 1, sonneries
    assert "21/08" in sonneries[0].message and "+612 M$" in sonneries[0].message
    assert "entrée" in sonneries[0].message

    # Le même jour qui grossit encore ne resonne pas.
    hub.etf = etf_frame(("2026-08-20", -900), ("2026-08-21", 1_050))
    e.evaluate(hub, now=T0 + 3 * (SLOW_EVERY + 1))
    assert len([a for a in e.alerts if a.kind == "etf"]) == 1, "rejoué"

    # Le lendemain, une sortie : le sens est dit.
    hub.etf = etf_frame(("2026-08-21", 1_050), ("2026-08-22", -730))
    e.evaluate(hub, now=T0 + 4 * (SLOW_EVERY + 1))
    sonneries = [a for a in e.alerts if a.kind == "etf"]
    assert len(sonneries) == 2 and "sortie nette -730 M$" in sonneries[1].message
    assert "partiel" not in sonneries[1].message

    # Un jour qui franchit le seuil avant qu'IBIT ait publié : la sonnerie
    # le dit, le total final pouvant encore changer de sens.
    hub.etf = etf_frame(("2026-08-22", -730), ("2026-08-23", -640))
    hub.etf.attrs["pending"] = ["IBIT", "FBTC"]
    e.evaluate(hub, now=T0 + 5 * (SLOW_EVERY + 1))
    sonneries = [a for a in e.alerts if a.kind == "etf"]
    assert len(sonneries) == 3, sonneries
    assert sonneries[2].message.endswith("— partiel, IBIT, FBTC à venir"), \
        sonneries[2].message
    print("  ✓ flux ETF : démarrage muet, une sonnerie par jour, sens dit,"
          " jour partiel nommé")


def test_reseau_charge():
    """Mempool gonflé et blocs lents : un front chacun, sous délai de garde."""
    hub = FakeHub()
    e = engine(mempool_mb=150, block_minutes=12)

    hub.mempool = pd.DataFrame({"time": pd.to_datetime(["2026-08-20"]),
                                "value": [80e6]})
    hub.chain = {"minutes_between_blocks": 9.8}
    e.evaluate(hub, now=T0)
    assert not e.alerts, "un réseau calme a sonné"

    hub.mempool = pd.DataFrame({"time": pd.to_datetime(["2026-08-20"]),
                                "value": [230e6]})
    hub.chain = {"minutes_between_blocks": 13.4}
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    sonneries = [a for a in e.alerts if a.kind == "chain"]
    assert len(sonneries) == 2, sonneries
    assert "230 Mo" in sonneries[0].message
    assert "13.4 min" in sonneries[1].message

    # La condition dure : silence tant qu'elle ne retombe pas.
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    assert len([a for a in e.alerts if a.kind == "chain"]) == 2, "rafale"

    # Retombée puis remontée sous le délai de garde : toujours silence ;
    # après le délai, le front resonne.
    hub.chain = {"minutes_between_blocks": 9.9}
    e.evaluate(hub, now=T0 + 3 * (SLOW_EVERY + 1))
    hub.chain = {"minutes_between_blocks": 12.5}
    e.evaluate(hub, now=T0 + 4 * (SLOW_EVERY + 1))
    assert len([a for a in e.alerts if a.kind == "chain"]) == 2, "sous garde"
    hub.chain = {"minutes_between_blocks": 9.9}
    e.evaluate(hub, now=T0 + COOLDOWN + 5 * (SLOW_EVERY + 1))
    hub.chain = {"minutes_between_blocks": 12.5}
    e.evaluate(hub, now=T0 + COOLDOWN + 6 * (SLOW_EVERY + 1))
    assert len([a for a in e.alerts if a.kind == "chain"]) == 3
    print("  ✓ réseau chargé : un front par grandeur, délai de garde tenu")


def test_volatilite_implicite_en_hausse():
    """DVOL au-dessus de la clôture de la veille : un front par montée."""
    def dvol(veille, jour):
        return pd.DataFrame({"time": pd.to_datetime(["2026-02-04", "2026-02-05"]),
                             "open": [veille, veille], "high": [veille, jour],
                             "low": [veille, veille], "close": [veille, jour]})

    hub = FakeHub()
    e = engine(dvol_jump_pts=6)
    e.evaluate(hub, now=T0)
    assert not e.alerts, "DVOL absent a sonné"

    hub.vol = dvol(53.5, 57.0)
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    assert not e.alerts, "une hausse sous le seuil a sonné"

    hub.vol = dvol(53.5, 82.6)
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    sonneries = [a for a in e.alerts if a.kind == "vol"]
    assert len(sonneries) == 1, sonneries
    assert sonneries[0].message == ("volatilité implicite en hausse : DVOL "
                                    "82.6, +29.1 points depuis la clôture "
                                    "d'hier"), sonneries[0].message

    # La journée reste tendue : une seule sonnerie.
    e.evaluate(hub, now=T0 + 3 * (SLOW_EVERY + 1))
    assert len([a for a in e.alerts if a.kind == "vol"]) == 1, "rafale"

    # Une ligne de DVOL ne suffit pas à dire une veille : silence.
    hub.vol = dvol(53.5, 82.6).iloc[1:]
    e2 = engine(dvol_jump_pts=6)
    e2.evaluate(hub, now=T0)
    assert not e2.alerts
    print("  ✓ DVOL : un bond au-dessus de la veille sonne une fois")


def skew_series(now, valeurs_anciennes, valeurs_recentes, age_h=24.0):
    """Des instantanés au pas de cinq minutes : un groupe vieux de
    `age_h` heures, un groupe qui finit maintenant."""
    def groupe(fin, valeurs):
        return [(fin - (len(valeurs) - 1 - k) * 300, v)
                for k, v in enumerate(valeurs)]
    points = (groupe(now - age_h * 3600, valeurs_anciennes)
              + groupe(now, valeurs_recentes))
    return pd.DataFrame({
        "time": pd.to_datetime([t for t, _ in points], unit="s"),
        "btc_dominance": [59.0] * len(points),
        "rr25": [v for _, v in points],
    })


def test_deplacement_du_skew():
    """Le skew qui se déplace sur 24 h sonne une fois, dit son sens, et
    ne se laisse pas prendre par un instantané isolé."""
    def sonneries(e):
        return [a for a in e.alerts if a.kind == "skew"]

    hub = FakeHub()
    e = engine(skew_shift_pts=2.0)
    # L'historique des autres tests n'a pas de colonne rr25 : silence.
    e.evaluate(hub, now=T0)
    assert not e.alerts

    # Sous le seuil : −3,0 → −4,5.
    hub.snapshots = skew_series(T0, [-3.0] * 3, [-4.5] * 3)
    e.evaluate(hub, now=T0)
    assert not sonneries(e), "un écart sous le seuil a sonné"

    # Un instantané isolé à −6,0 entre deux lectures normales : la médiane
    # l'écarte — c'est ce que Deribit a rendu au retour d'une panne.
    e = engine(skew_shift_pts=2.0)
    hub.snapshots = skew_series(T0, [-3.0] * 3, [-3.1, -6.0, -3.2])
    e.evaluate(hub, now=T0)
    assert not sonneries(e), "une cotation isolée a sonné"

    # Les puts se renchérissent : −3,0 → −5,4, une sonnerie, pas deux.
    hub.snapshots = skew_series(T0, [-3.0] * 3, [-5.3, -5.4, -5.5])
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    hub.snapshots = skew_series(T0 + 2 * (SLOW_EVERY + 1),
                                [-3.0] * 3, [-5.3, -5.4, -5.5])
    e.evaluate(hub, now=T0 + 2 * (SLOW_EVERY + 1))
    assert len(sonneries(e)) == 1, sonneries(e)
    assert sonneries(e)[0].message == (
        "skew 25Δ -2.4 pt en 24 h (-5.4) : les puts se renchérissent")

    # Dans l'autre sens, les calls.
    e = engine(skew_shift_pts=2.0)
    hub.snapshots = skew_series(T0, [-4.0] * 3, [-1.5] * 3)
    e.evaluate(hub, now=T0)
    assert sonneries(e)[0].message.endswith("les calls se renchérissent")

    # Référence trop vieille (40 h, une longue veille) ou trop jeune
    # (10 h), présent trop mince (deux instantanés) ou trop ancien : silence.
    for serie, quand in (
            (skew_series(T0, [-3.0] * 3, [-6.0] * 3, age_h=40), T0),
            (skew_series(T0, [-3.0] * 3, [-6.0] * 3, age_h=10), T0),
            (skew_series(T0, [-3.0] * 3, [-6.0] * 2), T0),
            (skew_series(T0, [-3.0] * 3, [-6.0] * 3), T0 + 3600)):
        e = engine(skew_shift_pts=2.0)
        hub.snapshots = serie
        e.evaluate(hub, now=quand)
        assert not sonneries(e), "une fenêtre mal couverte a sonné"
    assert normalize_config({"skew_shift_pts": 3})["skew_shift_pts"] == 3.0
    assert normalize_config({"skew_shift_pts": -1})["skew_shift_pts"] == 2.0
    print("  ✓ skew : écart sur 24 h, médiane contre la cotation isolée, "
          "fenêtre exigée")


def test_sonneries_au_journal():
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        hub = FakeHub()
        e = AlertEngine(journal=journal, fetch_news=lambda: [])
        e.configure({**DEFAULT_CONFIG,
                     "price_levels": [{"level": 99_000, "dir": "below"}]})
        hub.price = 98_500.0
        e.evaluate(hub, now=T0)
        rows = journal.alerts_between(0, T0 + 1)
        assert len(rows) == 1 and rows[0]["kind"] == "price"
        assert "99,000" in rows[0]["message"]
    print("  ✓ chaque sonnerie laisse sa ligne dans le journal")


def test_les_sonneries_survivent_au_redemarrage():
    """Un service relancé retrouve la journée sonnée, sans rien réécrire."""
    import time
    from btcterm.alerts import Alert
    from btcterm.hub import MarketHub

    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        now = time.time()
        for age, kind, message in ((7200, "arb", "arbitrage A → B net +0.6 %"),
                                   (1800, "liq", "rafale 3.2 M$ en 5 min"),
                                   (90_000, "news", "d'avant-hier : hors fenêtre")):
            journal.record_alert(Alert(time=now - age, kind=kind, message=message))

        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = journal
        assert hub._warm_alerts() == 2
        recentes = hub.alerts.recent(5)
        assert [a.kind for a in recentes] == ["liq", "arb"], "la plus récente d'abord"
        assert hub.alerts.count_since(3600) == 1, "la cloche compte l'heure"
        # Rien n'a été réécrit : trois lignes, comme avant.
        assert len(journal.alerts_between(0, now + 1)) == 3
        # Sans journal : mémoire vide, sans erreur.
        assert MarketHub(collect_news=False, keep_journal=False)._warm_alerts() == 0
        journal.close()
    print("  ✓ la journée sonnée revient au démarrage, le journal reste intact")


def test_changement_de_tendance_journaliere():
    """La clôture franchit sa MA 200 : une sonnerie, datée, jamais rejouée."""
    from btcterm.alerts import Alert

    def serie(fin):
        """Trois cents jours plats à 100, puis deux clôtures et la bougie en cours."""
        df = klines_frame([100.0] * 300 + fin)
        df["time"] = pd.date_range("2026-01-01", periods=len(df), freq="D")
        return df

    class Hub:
        frame = None

        def klines(self, interval, limit=0):
            assert interval == "1d", interval
            return self.frame

    hub = Hub()
    e = AlertEngine()
    e.configure(DEFAULT_CONFIG)
    regle = e.config

    def flips():
        return [a for a in e.alerts if a.kind == "flip"]

    #: Le cours reste au même endroit : rien.
    hub.frame = serie([100.0, 100.0, 100.0])
    e._check_trend(hub, None, regle, T0)
    assert not flips()

    #: L'avant-dernière ligne franchit : une sonnerie, et une seule.
    hub.frame = serie([100.0, 120.0, 121.0])
    e._check_trend(hub, None, regle, T0)
    e._check_trend(hub, None, regle, T0 + 60)
    assert len(flips()) == 1, "la même clôture a resonné"
    assert "au-dessus" in flips()[0].message

    #: La bougie en cours seule ne décide de rien : elle flotte.
    autre = AlertEngine()
    autre.configure(DEFAULT_CONFIG)
    hub.frame = serie([100.0, 100.0, 120.0])
    autre._check_trend(hub, None, autre.config, T0)
    assert not [a for a in autre.alerts if a.kind == "flip"]

    #: Relue du journal au redémarrage, la sonnerie du jour ne rejoue pas.
    relu = AlertEngine()
    relu.configure(DEFAULT_CONFIG)
    relu.restore([Alert(time=T0, kind="flip", message=flips()[0].message)])
    hub.frame = serie([100.0, 120.0, 121.0])
    relu._check_trend(hub, None, relu.config, T0)
    assert len([a for a in relu.alerts if a.kind == "flip"]) == 1

    #: Ni sur la démonstration, ni sur un historique figé, ni décochée.
    for drapeau in ("demo", "stale"):
        muet = AlertEngine()
        muet.configure(DEFAULT_CONFIG)
        hub.frame = serie([100.0, 120.0, 121.0])
        hub.frame.attrs[drapeau] = True
        muet._check_trend(hub, None, muet.config, T0)
        assert not muet.alerts, drapeau
    eteint = AlertEngine()
    eteint.configure({**DEFAULT_CONFIG, "trend_flip": False})
    hub.frame = serie([100.0, 120.0, 121.0])
    eteint._check_trend(hub, None, eteint.config, T0)
    assert not eteint.alerts
    print("  ✓ une sonnerie par franchissement, sur bougie close seulement")


def test_echeance_macro_annoncee():
    """Une sonnerie trente minutes avant, une seule, jamais après coup."""
    from btcterm import macrocal
    from btcterm.alerts import Alert

    echeance = macrocal.EVENTS[len(macrocal.EVENTS) // 2]
    heure = echeance.when_utc.timestamp()

    def echeances(moteur):
        return [a for a in moteur.alerts if a.kind == "event"]

    def moteur(**reglages):
        e = AlertEngine()
        e.configure({**DEFAULT_CONFIG, **reglages})
        return e

    #: Une heure avant : trop tôt.
    e = moteur()
    e._check_event(None, None, e.config, heure - 3600)
    assert not echeances(e)

    #: Vingt minutes avant : une sonnerie, et une seule malgré les tours.
    e._check_event(None, None, e.config, heure - 1200)
    e._check_event(None, None, e.config, heure - 1140)
    assert len(echeances(e)) == 1
    assert echeance.kind in echeances(e)[0].message
    assert "dans 20 min" in echeances(e)[0].message

    #: Une fois passée, rien.
    tard = moteur()
    tard._check_event(None, None, tard.config, heure + 60)
    assert not echeances(tard)

    #: Relue au redémarrage, elle ne rejoue pas.
    relu = moteur()
    relu.restore([Alert(time=heure - 1200, kind="event",
                        message=echeances(e)[0].message)])
    relu._check_event(None, None, relu.config, heure - 600)
    assert len(echeances(relu)) == 1

    #: Décochée, silence.
    eteint = moteur(event_soon=False)
    eteint._check_event(None, None, eteint.config, heure - 1200)
    assert not eteint.alerts
    print("  ✓ annoncée une fois, avant, et pas rejouée au redémarrage")


def test_carnets_creux():
    """La liquidité loin sous sa moyenne sonne une fois ; ni une journée
    trop courte, ni un relevé isolé, ni une plateforme qui se tait ne
    font un carnet creux."""
    def sonneries(e):
        return [a for a in e.alerts if a.kind == "depth"]

    def seance(now, volumes, sans_okx=()):
        """Un relevé toutes les cinq minutes, le dernier à `now`."""
        lignes = []
        for k, volume in enumerate(volumes):
            moment = pd.Timestamp(now - (len(volumes) - 1 - k) * 300, unit="s")
            lignes.append((moment, "Binance", 0.4 * volume, 0.4 * volume))
            if k not in sans_okx:
                lignes.append((moment, "OKX", 0.1 * volume, 0.1 * volume))
        return pd.DataFrame(lignes, columns=["time", "exchange", "bid", "ask"])

    hub = FakeHub()
    hub.DEPTH_BAND = 0.1
    serie = {"df": pd.DataFrame(columns=["time", "exchange", "bid", "ask"])}
    hub.book_depth_history = lambda days=1: serie["df"]

    e = engine(liquidity_drop_pct=40.0)
    e.evaluate(hub, now=T0)
    assert not e.alerts, "sans relevé, silence"

    # Une journée pleine, puis trois relevés à moitié : −49 % sous la moyenne.
    e = engine(liquidity_drop_pct=40.0)
    serie["df"] = seance(T0, [300.0] * 100 + [150.0] * 3)
    e.evaluate(hub, now=T0)
    assert [a.message for a in sonneries(e)] == [
        "carnets à 49 % sous leur moyenne de 24 h : 150 BTC à ±0.1 % du "
        "prix, contre 296"], [a.message for a in sonneries(e)]
    # Tant que l'état dure, pas de seconde sonnerie.
    e.evaluate(hub, now=T0 + SLOW_EVERY + 1)
    assert len(sonneries(e)) == 1

    for nom, df, quand in (
            ("moins de six heures de relevés",
             seance(T0, [300.0] * 40 + [150.0] * 3), T0),
            ("un relevé isolé", seance(T0, [300.0] * 100 + [300.0, 100.0, 300.0]), T0),
            ("sous le seuil", seance(T0, [300.0] * 100 + [200.0] * 3), T0),
            ("des relevés vieux d'une heure",
             seance(T0, [300.0] * 100 + [150.0] * 3), T0 + 3600)):
        e = engine(liquidity_drop_pct=40.0)
        serie["df"] = df
        e.evaluate(hub, now=quand)
        assert not sonneries(e), nom

    # OKX se tait sur un cinquième de la journée : il sort de la somme,
    # et son absence ne creuse rien.
    e = engine(liquidity_drop_pct=15.0)
    serie["df"] = seance(T0, [300.0] * 103, sans_okx=range(80, 103))
    e.evaluate(hub, now=T0)
    assert not sonneries(e), "une plateforme absente n'est pas un carnet creux"

    assert normalize_config({"liquidity_drop_pct": 25})["liquidity_drop_pct"] == 25.0
    assert normalize_config({"liquidity_drop_pct": 0})["liquidity_drop_pct"] == 40.0
    print("  ✓ carnets creux : moyenne d'une journée, médiane de trois relevés")


def test_sources_en_defaut():
    """Un lien coupé ou une source en panne qui dure sonne une fois, puis
    au rétablissement ; un lien muet ne sonne pas, un réveil remet les
    compteurs à zéro, et la règle se débraye."""
    from btcterm.alerts import SOURCE_DOWN_AFTER

    def sonneries(e):
        return [a.message for a in e.alerts if a.kind == "source"]

    etat = {"Carnet Kraken": ("ok", ""), "Liquidations Binance": ("muet", ""),
            "options": ("ok", "")}
    reveil = {"oui": False}
    hub = FakeHub()
    hub.woke_within = lambda seconds: reveil["oui"]

    def statut(now=None):
        lignes = lambda noms: [
            {"name": nom, "state": etat[nom][0], "detail": etat[nom][1],
             **({"since": etat[nom][2]} if len(etat[nom]) > 2 else {})}
            for nom in noms]
        return {"Flux temps réel": lignes(["Carnet Kraken", "Liquidations Binance"]),
                "Sources REST": lignes(["options"])}

    hub.sources_status = statut
    pas = SLOW_EVERY + 1

    e = engine(sources_down=True)
    e.evaluate(hub, now=T0)
    assert not sonneries(e), "tout répond, ou se tait sans être coupé"

    # Kraken se coupe : rien avant le quart d'heure, une sonnerie après.
    etat["Carnet Kraken"] = ("coupé", "timed out during opening handshake")
    e.evaluate(hub, now=T0 + pas)
    e.evaluate(hub, now=T0 + pas + SOURCE_DOWN_AFTER - 30)
    assert not sonneries(e)
    e.evaluate(hub, now=T0 + pas + SOURCE_DOWN_AFTER + pas)
    assert sonneries(e) == [
        "Carnet Kraken : en défaut depuis 16 min — timed out during "
        "opening handshake"], sonneries(e)
    e.evaluate(hub, now=T0 + 3 * SOURCE_DOWN_AFTER)
    assert len(sonneries(e)) == 1, "un épisode, une sonnerie"
    # Rétabli : cela se dit aussi, une fois.
    etat["Carnet Kraken"] = ("ok", "")
    e.evaluate(hub, now=T0 + 4 * SOURCE_DOWN_AFTER)
    assert sonneries(e)[-1] == "Carnet Kraken : rétabli après 59 min"
    e.evaluate(hub, now=T0 + 5 * SOURCE_DOWN_AFTER)
    assert len(sonneries(e)) == 2

    # Une source REST dit depuis quand elle est en panne : on la croit.
    e = engine(sources_down=True)
    etat["options"] = ("panne", "503 Service Unavailable", T0 - 1200)
    e.evaluate(hub, now=T0)
    assert sonneries(e) == ["options : en défaut depuis 20 min — 503 Service "
                            "Unavailable"], sonneries(e)
    etat["options"] = ("ok", "")

    # Une coupure brève ne sonne ni à l'aller ni au retour.
    e = engine(sources_down=True)
    etat["Carnet Kraken"] = ("coupé", "")
    e.evaluate(hub, now=T0)
    etat["Carnet Kraken"] = ("ok", "")
    e.evaluate(hub, now=T0 + 300)
    assert not sonneries(e)

    # Un réveil : tout est coupé à cet instant, et le compteur repart.
    e = engine(sources_down=True)
    etat["Carnet Kraken"] = ("coupé", "")
    e.evaluate(hub, now=T0)
    reveil["oui"] = True
    e.evaluate(hub, now=T0 + 8 * 3600)
    reveil["oui"] = False
    e.evaluate(hub, now=T0 + 8 * 3600 + pas)
    assert not sonneries(e), "huit heures de sommeil ne sont pas une panne"
    e.evaluate(hub, now=T0 + 8 * 3600 + pas + SOURCE_DOWN_AFTER + pas)
    assert len(sonneries(e)) == 1, "mais une coupure qui dure après le réveil, si"

    # Débrayée — c'est le défaut —, elle se tait.
    assert DEFAULT_CONFIG["sources_down"] is False
    e = engine()
    e.evaluate(hub, now=T0)
    e.evaluate(hub, now=T0 + 2 * SOURCE_DOWN_AFTER)
    assert not sonneries(e)
    assert normalize_config({"sources_down": True})["sources_down"] is True
    assert normalize_config({"sources_down": "oui"})["sources_down"] is False
    print("  ✓ sources : le défaut qui dure sonne, le rétablissement aussi")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nMoteur d'alertes — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le terminal sait attirer l'attention.\n")
