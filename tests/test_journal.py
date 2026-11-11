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
from btcterm.journal import (  # noqa: E402
    DB_PATH, GRACE, Journal, rebase_universe, resume)
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


def test_interruptions_de_seance():
    """Un trou entre deux instantanés dit que la machine dormait.

    Le journal les nomme, `market_snapshots` y pose une ligne de
    rupture, et la dominance comme les séries prolongées s'y rompent
    au lieu de tirer un trait par-dessus sept heures de sommeil.
    """
    import math
    import time
    import pandas as pd
    from btcterm.hub import MarketHub
    from terminal.charts import build_dominance_chart
    from terminal.lwc import serialize_perp
    from terminal.panels import journal as panneau

    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        now = time.time()
        # Cinq minutes de cadence, puis sept heures de veille, puis reprise.
        marks = [now - 8 * 3600, now - 8 * 3600 + 300, now - 8 * 3600 + 600,
                 now - 3600, now - 3300, now - 3000]
        for ts in marks:
            journal.record_market_snapshot(ts, btc_dominance=58.0,
                                           stable_share=9.0,
                                           total_cap_usd=2.7e12,
                                           total_volume_usd=9e10,
                                           oi_usd=8.4e9, funding_rate=6e-5)
        trous = journal.interruptions_between(0, now, gap=900)
        assert trous == [(marks[2], marks[3])], trous
        assert journal.interruptions_between(0, now, gap=8 * 3600) == []

        hub = MarketHub(keep_journal=False)
        hub.journal = journal
        assert hub.market_interruptions(days=1) == [(marks[2], marks[3])]
        df = hub.market_snapshots()
        assert len(df) == 7 and df["gap"].sum() == 1, df
        rupture = df[df["gap"]].iloc[0]
        assert math.isnan(rupture["btc_dominance"])
        assert df.loc[2, "time"] < rupture["time"] < df.loc[4, "time"]

        # La courbe de dominance passe par un NaN : le trait s'y rompt.
        fig = build_dominance_chart({"BTC": 58.0, "USDT": 6.0}, history=df)
        y = list(fig.data[1].y)
        assert len(y) == 7 and sum(1 for v in y if v != v) == 1, y

        # Les séries prolongées : une tranche vide devient un point blanc.
        oi = pd.DataFrame({"time": pd.to_datetime([now - 8 * 3600, now - 3600], unit="s"),
                           "oi_usd": [8.4e9, float("nan")]})
        paquet = serialize_perp(pd.DataFrame(columns=["time", "rate"]), oi)
        assert paquet["oi"][0]["value"] == 8.4e9
        assert paquet["oi"][1] == {"time": int(now - 3600)}, paquet["oi"]

        # Le panneau journal le dit, en durée lisible.
        _, badges = panneau.render(hub, expanded=False)
        texte = str(badges)
        assert "1 interruption (6 h 50)" in texte, texte
        assert panneau._duree(35 * 60) == "35 min"
        journal.close()
    print("  ✓ interruptions nommées, courbes rompues, badge posé")


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
        # Hors ligne : tous les accès rendent leur valeur vide — Deribit
        # compris, que l'instantané lit aussi pour le marché des options.
        from btcterm import options as opt
        hub.market_global = lambda: {}
        hub.open_interest = lambda **kw: pd.DataFrame(
            columns=["time", "oi", "oi_usd"])
        hub.perp_snapshot = lambda: {}
        hub.option_chain = lambda: opt.option_chain([])
        hub.futures_curve = lambda: opt.futures_curve([])
        hub.dvol = lambda: pd.DataFrame(
            columns=["time", "open", "high", "low", "close"])
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
        hub.dvol = lambda: pd.DataFrame(
            {"time": pd.to_datetime(["2026-11-05", "2026-11-06"]),
             "open": [42.0, 40.0], "high": [43.0, 41.0],
             "low": [40.0, 39.5], "close": [42.3, 40.7]})
        courbe = opt.futures_curve(
            [{"instrument_name": "BTC-29JAN27", "mark_price": 76_300,
              "estimated_delivery_price": 75_000}],
            pd.Timestamp("2026-11-06", tz="UTC"))
        hub.futures_curve = lambda: courbe
        hub.record_market_snapshot(now=now)

        df = hub.market_snapshots()
        assert len(df) == 1
        assert df["btc_dominance"].iloc[0] == 56.2
        assert df["stable_share"].iloc[0] == 7.0, "USDT + USDC"
        assert df["oi_usd"].iloc[0] == 1.2e10
        assert df["funding_rate"].iloc[0] == 0.0003
        # Le marché des options : DVOL et la base à trois mois s'écrivent ;
        # sans chaîne d'options, skew et put/call restent NULL.
        assert df["dvol"].iloc[0] == 40.7
        assert abs(df["basis90"].iloc[0]
                   - courbe["annualized"].iloc[0] * 100) < 1e-9
        assert pd.isna(df["rr25"].iloc[0]) and pd.isna(df["pc_oi"].iloc[0])
    print("  ✓ instantané composé de ses sources, options comprises, "
          "muet hors ligne")


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
                                       funding_rate=0.0001, rr25=-4.7)
        rows = journal.snapshots_between(0, 3000)
        assert len(rows) == 2
        assert rows[0]["btc_dominance"] == 58.0, "l'ancienne ligne a survécu"
        assert rows[0]["funding_rate"] is None, "colonne neuve à NULL"
        assert rows[0]["rr25"] is None
        assert rows[1]["funding_rate"] == 0.0001 and rows[1]["rr25"] == -4.7

        # Une base dont les colonnes ne sont pas dans l'ordre du code — un
        # ALTER TABLE d'une version intermédiaire : l'écriture nomme ses
        # colonnes, chaque valeur va à la sienne.
        path = Path(tmp) / "desordre.db"
        autre = sqlite3.connect(path)
        autre.execute("""
            CREATE TABLE market_snapshots (
                ts REAL NOT NULL, btc_dominance REAL, stable_share REAL,
                total_cap_usd REAL, total_volume_usd REAL, oi_usd REAL,
                rr25 REAL, funding_rate REAL)""")
        autre.commit()
        autre.close()
        journal = Journal(path)
        journal.record_market_snapshot(3000.0, funding_rate=0.0002, rr25=-5.1,
                                       dvol=41.0)
        rang = journal.snapshots_between(0, 4000)[0]
        assert (rang["funding_rate"], rang["rr25"], rang["dvol"]) == (
            0.0002, -5.1, 41.0), dict(rang)
    print("  ✓ base antérieure élargie, anciennes lignes intactes, "
          "colonnes nommées")


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


def test_univers_de_capitalisation_tenu():
    """Un actif que la source compte par intermittence ne fait plus sauter
    la dominance : retiré à la collecte, et rebasé à la lecture pour les
    lignes écrites avant.

    Constaté au journal le 11 novembre 2026 : quatre-vingt-sept sauts de
    0,4 à 0,7 point en deux mois et demi, le bitcoin immobile.
    """
    from btcterm import sources

    # ── À la collecte : l'actif sort du total, les parts se recalculent.
    brut = {"total_market_cap": {"usd": 2.5e12}, "total_volume": {"usd": 8e10},
            "market_cap_change_percentage_24h_usd": -1.0, "updated_at": 1,
            "market_cap_percentage": {"btc": 59.4, "eth": 10.0, "usdt": 7.0,
                                      "figr_heloc": 1.0}}
    avec = sources.market_global(brut)
    assert avec["excluded"] == 1.0 and "FIGR_HELOC" not in avec["shares"]
    assert abs(avec["total_cap_usd"] - 2.475e12) < 1
    assert abs(avec["shares"]["BTC"] - 60.0) < 1e-9, avec["shares"]
    del brut["market_cap_percentage"]["figr_heloc"]
    brut["total_market_cap"]["usd"] = 2.475e12
    brut["market_cap_percentage"]["btc"] = 60.0
    sans = sources.market_global(brut)
    assert sans["excluded"] == 0 and sans["shares"]["BTC"] == 60.0
    assert abs(sans["total_cap_usd"] - avec["total_cap_usd"]) < 1, \
        "avec ou sans l'actif, le même univers"

    # ── À la lecture : une séance d'avant, l'actif entrant puis sortant.
    def ligne(ts, btc_cap, autres, actif=0.0, tenu=None):
        total = btc_cap + autres + actif
        return {"ts": ts, "btc_dominance": 100 * btc_cap / total,
                "stable_share": 100 * 0.25e12 / total,
                "total_cap_usd": total, "cap_excluded": tenu, "oi_usd": 1.0}

    btc, autres, actif = 1.5e12, 1.0e12, 0.028e12
    seance = [ligne(300 * k, btc * (1 + 0.0004 * k), autres * (1 + 0.0005 * k),
                    actif if 10 <= k < 20 else 0.0) for k in range(30)]
    assert max(abs(b["btc_dominance"] - a["btc_dominance"])
               for a, b in zip(seance, seance[1:])) > 0.6, "le saut est là"
    rebasee = rebase_universe(seance)
    assert max(abs(b["btc_dominance"] - a["btc_dominance"])
               for a, b in zip(rebasee, rebasee[1:])) < 0.02, "et n'y est plus"
    assert rebasee[5] == seance[5], "une ligne sans l'actif ne bouge pas"
    juste = ligne(300 * 15, btc * 1.006, autres * 1.0075)
    for champ in ("btc_dominance", "stable_share", "total_cap_usd"):
        assert abs(rebasee[15][champ] / juste[champ] - 1) < 2e-4, champ
    assert rebasee[15]["oi_usd"] == 1.0, "le reste de la ligne est intact"
    assert seance[15]["btc_dominance"] < 59.5, "l'entrée n'est pas modifiée"

    # Une séance qui finit avec l'actif, suivie de lignes tenues : la
    # couture est un pas sortant, et tout ce qui la précède est rebasé.
    couture = ([ligne(300 * k, btc, autres, actif) for k in range(6)]
               + [ligne(300 * k, btc, autres, tenu=1.1) for k in range(6, 9)])
    rebasee = rebase_universe(couture)
    assert all(abs(r["btc_dominance"] - 60.0) < 1e-6 for r in rebasee), \
        [round(r["btc_dominance"], 3) for r in rebasee]
    # Entre deux lignes tenues, plus rien ne se corrige — même un marché
    # qui décroche pour de bon.
    krach = [ligne(0, btc, autres, tenu=0.0),
             ligne(300, btc * 0.97, autres * 0.90, tenu=0.0)]
    assert rebase_universe(krach) == krach

    # Ce que le marché fait vraiment n'est pas un changement d'univers :
    # les autres capitalisations qui perdent 1 % de plus que le bitcoin,
    # ou 3 % de plus après une nuit de sommeil dans le même sens qu'un
    # pas déjà vu, restent ce qu'elles sont.
    marche = [ligne(0, btc, autres), ligne(300, btc * 0.99, autres * 0.98)]
    assert rebase_universe(marche) == marche
    nuit = (seance[:25]
            + [ligne(300 * 24 + 30_000, btc * 1.01, autres * 0.98)])
    assert rebase_universe(nuit)[-1] == nuit[-1]
    assert rebase_universe([]) == []

    # ── Dans le journal : la colonne s'écrit, s'ajoute à une base
    # ancienne, et la lecture rebasée ne rend que la fenêtre demandée.
    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        for row in couture:
            champs = {k: v for k, v in row.items() if k != "ts"}
            journal.record_market_snapshot(1_000_000 + row["ts"], **champs)
        lues = journal.snapshots_between(0, 2_000_000)
        assert lues[0]["cap_excluded"] is None and lues[-1]["cap_excluded"] == 1.1
        assert lues[0]["btc_dominance"] < 59.5, "la base garde ce qui fut écrit"
        fenetre = journal.snapshots_rebased(1_000_000 + 600, 2_000_000)
        assert len(fenetre) == len(couture) - 2
        assert all(abs(r["btc_dominance"] - 60.0) < 1e-6 for r in fenetre)

        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = journal
        frame = hub.market_snapshots(days=1e5)
        assert abs(frame["btc_dominance"].dropna() - 60.0).max() < 1e-6
        assert hub.market_cap_change(now=1_003_000) is None, \
            "moins d'une demi-journée de recul : rien à mesurer"
        journal.close()
    print("  ✓ l'actif intermittent retiré à la collecte, rebasé à la lecture")


def test_variation_de_capitalisation_au_journal():
    """La variation sur 24 h se mesure au journal, même univers aux deux
    bouts — la source en donnait une fausse de plus de deux points."""
    with tempfile.TemporaryDirectory() as tmp:
        hub = MarketHub(collect_news=False, keep_journal=False)
        assert hub.market_cap_change() is None, "sans journal, rien"
        hub.journal = Journal(Path(tmp) / "journal.db")
        maintenant = time.time()
        assert hub.market_cap_change(now=maintenant) is None
        # Hier « avec » l'actif intermittent, ce matin « sans » : le
        # total annoncé a perdu 3 %, le marché 2 %.
        actif = 0.0255e12
        for ecart, total, tenu in ((-24 * 3600, 2.5e12 + actif, None),
                                   (-24 * 3600 + 300, 2.5e12 + actif, None),
                                   (-24 * 3600 + 600, 2.5e12, None),
                                   (-300, 2.45e12, 0.0), (0, 2.45e12, 0.0)):
            hub.journal.record_market_snapshot(
                maintenant + ecart, btc_dominance=100 * 1.5e12 / total,
                total_cap_usd=total, cap_excluded=tenu)
        variation, heures = hub.market_cap_change(now=maintenant)
        assert abs(variation + 2.0) < 0.02, variation
        assert abs(heures - 24) < 0.1
        hub.journal.close()
    print("  ✓ la variation sur 24 h vient du journal, univers rebasé")


def test_relecture_en_ligne_de_commande():
    """La commande relit ce que le journal tient — gros ordres et
    interruptions compris — et date ses heures au-delà d'un jour."""
    from types import SimpleNamespace as N

    with tempfile.TemporaryDirectory() as tmp:
        journal = Journal(Path(tmp) / "journal.db")
        assert resume(journal, 24, now=1e9)[0].startswith(
            "Couverture : aucun instantané"), "un journal vide le dit"
        assert not journal.path.exists(), "relire a créé la base"

        maintenant = time.time()
        # Deux heures de service, un trou de quatre heures, puis une heure
        # jusqu'à maintenant : un instantané toutes les cinq minutes.
        for debut, fin in ((-7 * 3600, -5 * 3600), (-3600, 0)):
            for ecart in range(debut, fin + 1, 300):
                journal.record_market_snapshot(
                    maintenant + ecart,
                    btc_dominance=58.0 if ecart < -4 * 3600 else 60.0)
        # Un instantané ancien, hors fenêtre : il date l'historique mais
        # ne doit pas entrer dans la dominance de la séance.
        journal.record_market_snapshot(maintenant - 40 * 86400,
                                       btc_dominance=50.0)
        for cote, montant, ecart in (("buy", 300_000, -1800),
                                     ("sell", 100_000, -1200),
                                     ("buy", 150_000, -30 * 3600)):
            journal.record_trade(N(time=maintenant + ecart, exchange="Binance",
                                   side=cote, price=1.0, quantity=montant,
                                   notional=float(montant)))
        journal.record_alert(N(time=maintenant - 600, kind="price",
                               message="seuil franchi"))
        for k in range(4):
            journal.record_book_depth(maintenant - 300 * (3 - k), [
                ("Binance", 100.0 + 10 * k, 100.0), ("OKX", 50.0, 50.0)])
        journal.record_alert(N(time=maintenant - 30 * 3600, kind="news",
                               message="d'hier"))

        jour = "\n".join(resume(journal, 8, now=maintenant))
        assert "Couverture : 3 h 00 sur 8 h — 2 interruptions" in jour, jour
        assert "la plus longue de 4 h 00" in jour, jour
        assert ("Gros ordres : 2 — achats 1 · 300,000 $ · ventes 1 · "
                "100,000 $ · solde +200,000 $") in jour, jour
        assert "le plus gros : achat Binance 300,000 $" in jour
        assert ("Liquidité : 330 BTC à portée du prix au dernier relevé"
                in jour and "moyenne 315, de 300 à 330 · Binance, OKX" in jour), jour
        assert "Alertes : 1" in jour and "d'hier" not in jour
        heure = time.strftime("%H:%M", time.localtime(maintenant - 600))
        assert f"  {heure}  [  price]  seuil franchi" in jour, jour
        assert "de 58.0 à 60.0 % sur la fenêtre" in jour, \
            "la dominance d'il y a quarante jours n'est pas celle de la séance"

        # Au-delà d'un jour, « 06:04 » ne désigne plus aucun matin.
        long = "\n".join(resume(journal, 48, now=maintenant))
        date = time.strftime("%d/%m %H:%M",
                             time.localtime(maintenant - 30 * 3600))
        assert f"  {date}  [   news]  d'hier" in long, long
        assert "Gros ordres : 3" in long
        journal.close()
    print("  ✓ couverture, gros ordres et dates : la commande suit le journal")


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
        # Dans la grille, la profondeur de l'historique passe au survol —
        # la ligne complète débordait de la cellule ; en plein écran, elle
        # s'écrit.
        assert "instantanés depuis le" in badges.title, \
            "la profondeur de l'historique devait se dire au survol"
        _, plein = render(SimpleNamespace(journal=journal), True)
        assert "instantanés depuis le" in " ".join(_textes(plein)), \
            "la profondeur de l'historique devait s'écrire en plein écran"

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
