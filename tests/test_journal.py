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


def test_instantanes_partiels_et_lecture():
    """Un instantané partiel s'écrit, les colonnes absentes restent NULL."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        t0 = 3_000_000.0
        journal.record_market_snapshot(t0, btc_dominance=56.2,
                                       stable_share=7.8,
                                       total_cap_usd=2.4e12,
                                       total_volume_usd=9.1e10,
                                       oi_usd=1.2e10)
        # CoinGecko en panne, Binance répond : l'OI seul s'écrit.
        journal.record_market_snapshot(t0 + 300, oi_usd=1.3e10)

        rows = journal.snapshots_between(0, t0 + 600)
        assert len(rows) == 2, len(rows)
        assert rows[0]["btc_dominance"] == 56.2
        assert rows[0]["oi_usd"] == 1.2e10
        assert rows[1]["btc_dominance"] is None, "l'absence devait rester NULL"
        assert rows[1]["oi_usd"] == 1.3e10
    print("  ✓ instantané complet puis partiel, NULL où la source a manqué")


def test_instantanes_retention_longue():
    """La purge de séance épargne les instantanés : eux seuls font
    l'historique que les API refusent, les effacer au bout d'un mois
    détruirait ce que leur journalisation devait bâtir."""
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        now = time.time()
        journal.record_alert(type("A", (), {
            "time": now - 40 * 86_400, "kind": "price", "message": "vieux"})())
        journal.record_market_snapshot(now - 40 * 86_400, btc_dominance=50.0)
        journal.record_market_snapshot(now - 500 * 86_400, btc_dominance=45.0)

        journal.purge(days=30)
        assert journal.alerts_between(0, now) == [], "l'alerte devait partir"
        rows = journal.snapshots_between(0, now)
        assert len(rows) == 1, "40 jours gardés, 500 jours purgés"
        assert rows[0]["btc_dominance"] == 50.0
    print("  ✓ l'instantané survit à la purge de séance, pas aux 400 jours")


def test_hub_ecrit_et_relit_les_instantanes():
    """Le hub compose l'instantané de ses deux sources, et le relit en
    DataFrame ; hors ligne, rien ne s'écrit — pas même la base."""
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        hub = MarketHub(collect_news=False)
        hub.journal = Journal(Path(tmp) / "journal.db")

        now = time.time()
        # Hors ligne : les trois accès rendent leur valeur vide.
        hub.market_global = lambda: {}
        hub.open_interest = lambda **kw: pd.DataFrame(
            columns=["time", "oi", "oi_usd"])
        hub.perp_snapshot = lambda: {}
        hub.record_market_snapshot(now=now)
        assert not hub.journal.path.exists(), "le vide a créé la base"
        assert hub.market_snapshots().empty

        # Les sources répondent : l'instantané se compose.
        hub.market_global = lambda: {
            "total_cap_usd": 2.4e12, "total_volume_usd": 9.1e10,
            "shares": {"BTC": 56.2, "ETH": 12.1, "USDT": 5.5, "USDC": 1.5},
        }
        hub.open_interest = lambda **kw: pd.DataFrame(
            {"time": [pd.Timestamp("2026-08-24")],
             "oi": [80_000.0], "oi_usd": [1.2e10]})
        hub.perp_snapshot = lambda: {"funding_rate": 0.0003}
        hub.record_market_snapshot(now=now)

        df = hub.market_snapshots()
        assert len(df) == 1
        assert df["btc_dominance"].iloc[0] == 56.2
        assert df["stable_share"].iloc[0] == 7.0, "USDT + USDC"
        assert df["oi_usd"].iloc[0] == 1.2e10
        assert df["funding_rate"].iloc[0] == 0.0003
    print("  ✓ instantané composé des trois sources, muet hors ligne")


def test_open_interest_prolonge_par_le_journal():
    """La série Binance (30 j) continue vers le passé sur le journal,
    rééchantillonné au pas de 4 h pour une couture invisible."""
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        hub = MarketHub(collect_news=False)
        hub.journal = Journal(Path(tmp) / "journal.db")

        base_start = time.time() - 30 * 86_400
        # Quarante jours d'instantanés au pas de 5 min seraient longs à
        # écrire un à un : une heure de pas suffit à prouver le
        # rééchantillonnage (4 h doivent en garder un sur quatre).
        for k in range(12 * 24):  # douze jours antérieurs à Binance
            hub.journal.record_market_snapshot(
                base_start - (k + 1) * 3600, oi_usd=1.0e10 + k * 1e6)

        base = pd.DataFrame({
            "time": pd.to_datetime(
                [base_start + k * 4 * 3600 for k in range(180)], unit="s"),
            "oi": [80_000.0] * 180,
            "oi_usd": [1.2e10] * 180,
        })
        hub.open_interest = lambda **kw: base

        merged = hub.open_interest_extended()
        assert len(merged) > len(base), "le journal devait prolonger"
        assert merged["time"].is_monotonic_increasing
        assert (merged["time"].iloc[0] < base["time"].iloc[0]), \
            "la série devait remonter avant Binance"
        # La partie journalisée est rééchantillonnée : moins de points
        # que d'instantanés écrits.
        assert len(merged) - len(base) < 12 * 24
        assert merged["oi_usd"].notna().all()

        # Sans historique local, la série de Binance ressort telle quelle.
        hub.journal = Journal(Path(tmp) / "vide.db")
        assert hub.open_interest_extended() is base
    print("  ✓ open interest prolongé et rééchantillonné, couture triée")


def test_migration_d_une_base_anterieure():
    """Une base créée avant la colonne funding_rate s'élargit à
    l'ouverture — ALTER TABLE, l'historique accumulé jamais perdu."""
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.db"
        vieille = sqlite3.connect(path)
        vieille.execute("""
            CREATE TABLE market_snapshots (
                ts REAL NOT NULL, btc_dominance REAL, stable_share REAL,
                total_cap_usd REAL, total_volume_usd REAL, oi_usd REAL)""")
        vieille.execute("INSERT INTO market_snapshots VALUES "
                        "(1000.0, 58.0, 9.5, 2.4e12, 9e10, 1.1e10)")
        vieille.commit()
        vieille.close()

        journal = Journal(path)
        journal.record_market_snapshot(2000.0, btc_dominance=59.0,
                                       funding_rate=0.0001)
        rows = journal.snapshots_between(0, 3000)
        assert len(rows) == 2
        assert rows[0]["btc_dominance"] == 58.0, "l'ancienne ligne a survécu"
        assert rows[0]["funding_rate"] is None, "colonne neuve à NULL"
        assert rows[1]["funding_rate"] == 0.0001
    print("  ✓ base antérieure élargie, anciennes lignes intactes")


def test_financement_prolonge_par_le_journal():
    """Comme l'open interest : la fenêtre Binance continue vers le passé
    sur les instantanés, rééchantillonnés sur la grille des règlements
    (8 h, étiquette à droite) — le dernier relevé avant l'échéance."""
    import pandas as pd

    with tempfile.TemporaryDirectory() as tmp:
        hub = MarketHub(collect_news=False)
        hub.journal = Journal(Path(tmp) / "journal.db")

        base_start = time.time() - 30 * 86_400
        # Six jours de relevés horaires antérieurs à la fenêtre Binance.
        for k in range(6 * 24):
            hub.journal.record_market_snapshot(
                base_start - (k + 1) * 3600, funding_rate=0.0001)

        base = pd.DataFrame({
            "time": pd.to_datetime(
                [base_start + k * 8 * 3600 for k in range(90)], unit="s"),
            "rate": [0.0002] * 90,
        })
        hub.funding_history = lambda limit=90: base

        merged = hub.funding_history_extended()
        assert len(merged) > len(base), "le journal devait prolonger"
        assert merged["time"].is_monotonic_increasing
        assert merged["time"].iloc[0] < base["time"].iloc[0]
        #: Rééchantillonné : ~18 règlements reconstitués pour 144 relevés.
        assert len(merged) - len(base) <= 19
        assert merged["rate"].notna().all()
        assert (merged["time"] < base["time"].iloc[0]).sum() \
            == len(merged) - len(base), "recouvrement à la couture"

        # Sans historique local, la série Binance ressort telle quelle.
        hub.journal = Journal(Path(tmp) / "vide.db")
        assert hub.funding_history_extended() is base
    print("  ✓ financement prolongé sur la grille des règlements, sans couture")


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


def _textes(node, found=None):
    """Tous les fragments de texte d'un arbre de composants Dash."""
    found = found if found is not None else []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, (list, tuple)):
        for child in node:
            _textes(child, found)
    elif hasattr(node, "children"):
        _textes(node.children, found)
    return found


def test_panneau_journal_rend_la_seance():
    """Le panneau JOURNAL relit ce que la CLI relit — alertes, épisodes,
    bilan des liquidations, profondeur des instantanés — et dit son
    absence quand le terminal tourne sans journal."""
    from types import SimpleNamespace

    from terminal.panels.journal import render

    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        now = time.time()
        journal.record_alert(type("A", (), {
            "time": now - 60, "kind": "trend", "message": "cours étiré"})())
        journal.observe([opportunity(net=0.4)], now - 30)
        journal.flush()
        feed = LiquidationFeed()
        feed.on_event = journal.record_liquidation
        feed._handle(message(side="SELL", price="64000", qty="0.5"))
        journal.record_market_snapshot(now - 7 * 86_400, btc_dominance=58.0)

        view, badges = render(SimpleNamespace(journal=journal), False)
        texte = " ".join(_textes(view)) + " " + " ".join(_textes(badges))
        assert "cours étiré" in texte, "l'alerte devait se relire"
        assert "Kraken → Binance" in texte, "l'épisode devait se relire"
        assert "32 k$" in texte, "le bilan des liquidations devait chiffrer"
        assert "instantanés depuis le" in texte, \
            "la profondeur de l'historique devait se dire"

        vide, _ = render(SimpleNamespace(journal=None), False)
        assert "no-journal" in " ".join(_textes(vide))
    print("  ✓ le panneau relit alertes, épisodes, bilan — et dit l'absence")


def test_liquidations_disent_leur_plateforme_et_base_anterieure():
    """La colonne `exchange` s'écrit ; une base créée sans elle s'élargit
    à l'ouverture, ses lignes intactes."""
    import sqlite3
    from btcterm.liquidations import BybitLiquidationConnector

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "journal.db"
        vieille = sqlite3.connect(path)
        vieille.execute("""
            CREATE TABLE liquidations (
                ts REAL NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
                price REAL NOT NULL, quantity REAL NOT NULL,
                notional REAL NOT NULL)""")
        vieille.execute("INSERT INTO liquidations VALUES "
                        "(1000.0, 'BTCUSDT', 'long', 60000.0, 1.0, 60000.0)")
        vieille.commit()
        vieille.close()

        journal = Journal(path)
        feed = LiquidationFeed()
        feed.on_event = journal.record_liquidation
        feed._handle(message(side="SELL", price="64000", qty="0.5"))
        BybitLiquidationConnector(feed)._handle(
            {"topic": "allLiquidation.ETHUSDT", "ts": 1,
             "data": [{"T": int(time.time() * 1000), "s": "ETHUSDT",
                       "S": "Sell", "v": "2", "p": "3000"}]})

        rows = journal.liquidations_between(0, time.time() + 1)
        assert [r["exchange"] for r in rows] == [None, "Binance", "Bybit"], \
            [r["exchange"] for r in rows]
        assert rows[0]["notional"] == 60000.0, "l'ancienne ligne a survécu"
        assert rows[2]["side"] == "short" and rows[2]["symbol"] == "ETHUSDT"
    print("  ✓ la plateforme se journalise ; base antérieure élargie")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nJournal des données éphémères — {len(tests)} vérifications\n"
          + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La séance se relit après coup.\n")
