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

from btcterm.alerts import (AlertEngine, COOLDOWN, DEFAULT_CONFIG,  # noqa: E402
                            SLOW_EVERY, normalize_config)
from btcterm.journal import Journal  # noqa: E402
from test_journal import opportunity  # noqa: E402

T0 = 1_000_000.0


class FakeHub:
    """Le strict nécessaire aux règles, tout étant réglable."""

    def __init__(self):
        self.price = 100_000.0
        self.funding = 0.0001
        self.totals = {"long": 0.0, "short": 0.0, "btc": 0.0, "count": 0}
        self.liquidations = SimpleNamespace(
            totals=lambda window=300: self.totals)

    def reference_price(self):
        return self.price

    def perp_snapshot(self):
        return {"funding_rate": self.funding}


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nMoteur d'alertes — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le terminal sait attirer l'attention.\n")
