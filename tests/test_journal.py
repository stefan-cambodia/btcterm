#!/usr/bin/env python3
"""
Journal des données éphémères : événements, épisodes, rétention.

Le point délicat est l'épisode d'arbitrage : une opportunité est un état
qui dure, pas un événement, et la journaliser à chaque balayage
rempilerait la même paire dix fois par seconde. Le test déroule donc la
vie d'un épisode au temps simulé — ouverture, meilleur profit,
flottement toléré, clôture après la grâce — et vérifie qu'il n'en sort
qu'une ligne, la bonne.

Le reste protège les frontières : le rappel du fil de liquidations ne
doit jamais casser le flux, et construire un hub — ce que fait chaque
test du terminal — ne doit créer aucun fichier.

Aucun réseau n'est touché.

Lancement :
    python tests/test_journal.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from btcterm.arbitrage import ArbitrageOpportunity  # noqa: E402
from btcterm.hub import MarketHub  # noqa: E402
from btcterm.journal import DB_PATH, GRACE, Journal  # noqa: E402
from btcterm.liquidations import LiquidationFeed  # noqa: E402
from test_liquidations import message  # noqa: E402


def opportunity(buy="Kraken", sell="Binance", net=0.5,
                buy_price=50_000.0, sell_price=50_400.0):
    return ArbitrageOpportunity(
        buy_exchange=buy, sell_exchange=sell,
        buy_price=buy_price, sell_price=sell_price,
        gross_profit_pct=net + 0.36, net_profit_pct=net,
        buy_fee=0.0026, sell_fee=0.001,
    )


def test_base_paresseuse():
    """Ni la construction ni les lectures ne créent la base."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.db"
        journal = Journal(path)
        assert journal.liquidations_between(0, time.time()) == []
        assert journal.episodes_between(0, time.time()) == []
        journal.purge()
        assert not path.exists(), "une lecture a créé la base"
    print("  ✓ pas d'écriture, pas de fichier")


def test_liquidations_au_fil_de_l_eau():
    """Le rappel du fil écrit chaque événement ; ses pannes n'atteignent
    jamais le flux lui-même."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        feed = LiquidationFeed()
        feed.on_event = journal.record_liquidation

        feed._handle(message(side="SELL", price="64000", qty="0.5"))
        feed._handle(message(symbol="ETHUSDT", side="BUY",
                             price="3000", qty="2"))

        rows = journal.liquidations_between(time.time() - 60, time.time())
        assert len(rows) == 2, len(rows)
        assert rows[0]["side"] == "long" and rows[0]["notional"] == 32_000
        assert rows[1]["symbol"] == "ETHUSDT" and rows[1]["side"] == "short"

        # Un journal qui explose (base fermée) ne ferme pas le fil.
        journal.close()
        Path(journal.path).unlink()
        feed.on_event = lambda event: (_ for _ in ()).throw(OSError("disque"))
        feed._handle(message(side="SELL"))
        assert len(feed.latest(10)) == 3, "le rappel a cassé le flux"
    print("  ✓ deux événements journalisés, panne du rappel sans effet")


def test_vie_d_un_episode():
    """Ouverture, meilleur profit, flottement, clôture : une seule ligne."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        t0 = 1_000_000.0

        # Trois observations rentables, la meilleure au milieu — et une
        # paire jamais rentable qui ne doit laisser aucune trace.
        journal.observe([opportunity(net=0.2, sell_price=50_100.0),
                         opportunity(buy="OKX", sell="Bybit", net=0.05)], t0)
        journal.observe([opportunity(net=0.6, sell_price=50_400.0)], t0 + 1)
        # Flottement : la paire disparaît moins de GRACE secondes…
        journal.observe([], t0 + 2)
        journal.observe([opportunity(net=0.3, sell_price=50_200.0)],
                        t0 + GRACE - 1)
        # …puis pour de bon : la clôture n'arrive qu'une fois la grâce
        # écoulée, au balayage suivant.
        journal.observe([], t0 + GRACE + 5)
        assert journal.episodes_between(0, t0 + 10 * GRACE) == []
        journal.observe([], t0 + 2 * GRACE)

        rows = journal.episodes_between(0, t0 + 10 * GRACE)
        assert len(rows) == 1, [dict(r) for r in rows]
        episode = rows[0]
        assert episode["first_seen"] == t0
        assert episode["last_seen"] == t0 + GRACE - 1
        assert episode["samples"] == 3
        assert episode["best_net_pct"] == 0.6
        assert episode["sell_price"] == 50_400.0, "prix pris hors du meilleur"
        assert episode["buy_exchange"] == "Kraken"
    print("  ✓ un épisode, trois observations, le meilleur net retenu")


def test_flush_clot_la_seance():
    """L'arrêt du hub écrit les épisodes encore ouverts."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        t0 = 2_000_000.0
        journal.observe([opportunity(net=0.4)], t0)
        journal.flush()
        rows = journal.episodes_between(0, t0 + 1)
        assert len(rows) == 1 and rows[0]["samples"] == 1
        journal.flush()  # idempotent : plus rien à écrire
        assert len(journal.episodes_between(0, t0 + 1)) == 1
    print("  ✓ flush écrit l'épisode ouvert, une seule fois")


def test_retention():
    """La purge oublie l'ancien, garde le récent."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        feed = LiquidationFeed()
        feed.on_event = journal.record_liquidation
        feed._handle(message(when=time.time() - 40 * 86_400))
        feed._handle(message())
        journal.observe([opportunity()], time.time() - 40 * 86_400)
        journal.flush()

        journal.purge(days=30)
        assert len(journal.liquidations_between(0, time.time())) == 1
        assert journal.episodes_between(0, time.time()) == []
    print("  ✓ 30 jours de rétention, l'ancien purgé")


def test_cablage_du_hub():
    """Le hub branche le journal sur le fil — sans créer la base."""
    existait = DB_PATH.exists()
    hub = MarketHub(collect_news=False)
    assert hub.journal is not None
    assert hub.liquidations.on_event == hub.journal.record_liquidation
    assert DB_PATH.exists() == existait, "construire le hub a créé la base"

    sans = MarketHub(collect_news=False, keep_journal=False)
    assert sans.journal is None and sans.liquidations.on_event is None
    print("  ✓ journal branché par défaut, débrayable, jamais créé à vide")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nJournal des données éphémères — {len(tests)} vérifications\n"
          + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La séance se relit après coup.\n")
