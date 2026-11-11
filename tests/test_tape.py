#!/usr/bin/env python3
"""
Le ruban des échanges, sans réseau.

Vérifié ici :

- le côté du preneur se lit comme mesuré contre le carnet : Binance par
  `m` (vrai : vente agressive), Bybit par `S`, Coinbase à l'inverse de
  son `side`, qui est celui du teneur ; l'instantané d'abonnement de
  Coinbase n'entre pas au ruban, et un refus d'abonnement vaut panne ;
- le ruban ne garde que les gros ordres, du plus récent au plus ancien,
  et somme le flux par minute et par place, toutes tailles ;
- un lien dit son état et l'âge de son dernier échange ;
- le panneau dit le flux et les liens absents, et le silence d'un ruban
  vide.

Lancement :
    python tests/test_tape.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.tape import (BinanceTradeConnector, BybitTradeConnector,  # noqa: E402
                          CoinbaseTradeConnector, Trade, TradeTape)


def test_le_cote_du_preneur():
    from datetime import datetime, timezone

    ruban = TradeTape(record=0)
    ms = int(time.time() * 1000)
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.123456789Z")
    BinanceTradeConnector(ruban).handle(
        {"T": ms, "m": True, "p": "75000", "q": "2"})
    BinanceTradeConnector(ruban).handle(
        {"T": ms, "m": False, "p": "75000", "q": "1"})
    BybitTradeConnector(ruban).handle({"topic": "publicTrade.BTCUSDT", "data": [
        {"T": ms, "S": "Buy", "p": "75000", "v": "0.5"}]})
    coinbase = CoinbaseTradeConnector(ruban)
    coinbase.handle({"channel": "market_trades", "events": [
        {"type": "snapshot", "trades": [
            {"time": iso, "side": "BUY", "price": "75000", "size": "9"}]},
        {"type": "update", "trades": [
            {"time": iso, "side": "BUY", "price": "75000", "size": "3"}]}]})
    ordres = {(t.exchange, t.quantity): t.side for t in ruban.large(0)}
    assert ordres == {("Binance", 2.0): "sell", ("Binance", 1.0): "buy",
                      ("Bybit", 0.5): "buy", ("Coinbase", 3.0): "sell"}, ordres
    # Un refus d'abonnement se lit comme une panne, pas comme un silence.
    for connecteur, refus in ((BybitTradeConnector(ruban),
                               {"success": False, "ret_msg": "topic inconnu"}),
                              (coinbase, {"type": "error", "message": "refusé"})):
        try:
            connecteur.handle(refus)
        except RuntimeError:
            pass
        else:
            raise AssertionError("un refus d'abonnement doit lever")
    print("  ✓ Binance par m, Bybit par S, Coinbase à l'inverse ; instantané écarté")


def test_le_ruban_et_le_flux():
    maintenant = 1_800_000_000.0
    ruban = TradeTape(record=50_000)
    for i, (place, cote, quantite) in enumerate((
            ("Binance", "buy", 2.0), ("Binance", "sell", 0.1),
            ("Coinbase", "buy", 20.0), ("Bybit", "sell", 1.0))):
        ruban.record(Trade(time=maintenant - 30 + i, exchange=place, side=cote,
                           price=75_000.0, quantity=quantite))
    # Un ordre d'il y a deux heures : hors du ruban et du flux.
    ruban.record(Trade(time=maintenant - 7200, exchange="Binance", side="buy",
                       price=75_000.0, quantity=100.0))
    gros = ruban.large(50_000, now=maintenant)
    assert [t.quantity for t in gros] == [1.0, 20.0, 2.0], "petits exclus, récent d'abord"
    assert [t.quantity for t in ruban.large(1_000_000, now=maintenant)] == [20.0]
    assert len(ruban.large(50_000, limit=2, now=maintenant)) == 2
    flux = ruban.flow(300, now=maintenant)
    assert flux["buy"] == 75_000 * 22 and flux["sell"] == 75_000 * 1.1
    assert flux["by_exchange"]["Binance"] == {"buy": 150_000.0, "sell": 7_500.0}
    # Les minutes trop anciennes ont été oubliées en route.
    assert min(ruban._minutes) >= int((maintenant - 7200 - ruban.keep) // 60)
    ruban.record(Trade(time=maintenant, exchange="X", side="buy", price=0,
                       quantity=1))
    assert "X" not in ruban.flow(60, now=maintenant)["by_exchange"], \
        "un échange sans prix ne compte pas"
    print("  ✓ gros ordres seuls, récent d'abord ; flux par minute et par place")


def test_l_etat_des_liens():
    ruban = TradeTape()
    ruban.attach("Binance")
    assert ruban.links["Binance"] == (False, None)
    assert ruban.last_event_age("Binance") is None
    ruban.mark("Binance", True)
    assert ruban.last_event_age("Binance") < 1
    ruban.mark("Binance", False, "connexion refusée")
    assert ruban.links["Binance"] == (False, "connexion refusée")
    print("  ✓ lien attaché, ouvert, tombé — avec son erreur")


def test_le_panneau():
    from terminal.panels import tape as panneau

    ruban = TradeTape()
    ruban.attach("Coinbase")
    assert "aucun échange reçu" in str(panneau.badges(ruban))
    assert "sans Coinbase" in str(panneau.badges(ruban))
    assert "aucun ordre" in str(panneau.table([]))
    maintenant = time.time()
    ruban.record(Trade(maintenant, "Binance", "buy", 75_000.0, 30.0))
    ruban.record(Trade(maintenant, "Binance", "sell", 75_000.0, 10.0))
    assert "75 % achats" in str(panneau.badges(ruban))
    assert "achats 2.25 M$" in str(panneau.flow_line(ruban))
    ligne = str(panneau.table(ruban.large(250_000)))
    assert "ACHAT" in ligne and "30.000 BTC" in ligne and "'fontWeight': '700'" in ligne
    print("  ✓ flux et liens absents au titre, silence dit, million en gras")



def test_la_prime_coinbase():
    """Prix moyens pondérés par place, et leur écart : Coinbase en dollars
    contre Binance en USDT ; rien tant qu'une des deux manque."""
    maintenant = 1_800_000_000.0
    ruban = TradeTape()
    assert ruban.coinbase_premium(now=maintenant) is None
    ruban.record(Trade(maintenant - 10, "Binance", "buy", 75_000.0, 1.0))
    ruban.record(Trade(maintenant - 5, "Binance", "sell", 75_300.0, 2.0))
    assert ruban.coinbase_premium(now=maintenant) is None, "Coinbase manque"
    ruban.record(Trade(maintenant - 3, "Coinbase", "buy", 75_200.0, 1.0))
    assert ruban.vwap("Binance", 300, now=maintenant) == 75_200.0
    assert ruban.coinbase_premium(now=maintenant) == 0.0
    ruban.record(Trade(maintenant - 2, "Coinbase", "sell", 75_500.0, 1.0))
    attendu = 75_350.0 / 75_200.0 - 1
    assert abs(ruban.coinbase_premium(now=maintenant) - attendu) < 1e-12
    # Hors de la fenêtre : rien.
    assert ruban.vwap("Coinbase", 60, now=maintenant + 3600) is None

    from terminal.panels import tape as panneau
    vif = TradeTape()
    vif.record(Trade(time.time(), "Binance", "buy", 75_000.0, 2.0))
    vif.record(Trade(time.time(), "Coinbase", "buy", 74_925.0, 2.0))
    assert "prime CBS -0.10 %" in str(panneau.badges(vif))
    print("  ✓ prix moyens pondérés, prime Coinbase, absente sans les deux places")


def test_les_gros_ordres_vont_au_journal_et_en_reviennent():
    """Chaque gros ordre vivant part au journal ; le hub les rend au ruban
    au démarrage, sans les réécrire ; le panneau JOURNAL en fait le bilan."""
    import tempfile
    from types import SimpleNamespace
    from btcterm.hub import MarketHub
    from btcterm.journal import Journal
    from terminal.panels.journal import render

    with tempfile.TemporaryDirectory() as dossier:
        journal = Journal(Path(dossier) / "journal.db")
        ruban = TradeTape()
        ruban.on_large = journal.record_trade
        maintenant = time.time()
        ruban.record(Trade(maintenant - 60, "Binance", "sell", 75_000.0, 4.0))
        ruban.record(Trade(maintenant - 30, "Coinbase", "buy", 75_000.0, 2.0))
        ruban.record(Trade(maintenant - 20, "Bybit", "buy", 75_000.0, 0.1))   # petit
        lignes = journal.trades_between(0, maintenant)
        assert [(r["exchange"], r["side"]) for r in lignes] == [
            ("Binance", "sell"), ("Coinbase", "buy")], "seuls les gros partent"

        # Un rappel qui lève n'arrête pas le flux.
        casse = TradeTape()
        casse.on_large = lambda t: 1 / 0
        casse.record(Trade(maintenant, "Binance", "buy", 75_000.0, 5.0))
        assert len(casse.large(0, now=maintenant)) == 1

        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = journal
        hub.tape.on_large = journal.record_trade
        assert hub._warm_tape() == 2
        assert len(hub.tape.large(0)) == 2
        assert len(journal.trades_between(0, maintenant + 1)) == 2, \
            "la relecture ne doit rien réécrire"

        vue, _ = render(SimpleNamespace(journal=journal), True)
        texte = str(vue)
        assert "gros ordres" in texte and "achats 1 · 150 k$" in texte, texte[-600:]
        assert "ventes 1 · 300 k$" in texte and "−150 k$" in texte
        journal.purge(days=0)
        assert journal.trades_between(0, maintenant + 1) == []
        journal.close()
    print("  ✓ au journal, rendus au démarrage sans doublon, bilan du jour, purgés")

def test_le_ruban_dans_le_temps():
    """Le journal rend le solde horaire des gros ordres, et le panneau le
    trace avec la prime Coinbase — rompue sur les interruptions."""
    import tempfile
    import time
    from pathlib import Path
    from types import SimpleNamespace as N

    import pandas as pd

    from btcterm.hub import MarketHub
    from btcterm.journal import Journal
    from terminal.charts import build_tape_history
    from terminal.panels import tape as panneau

    with tempfile.TemporaryDirectory() as tmp:
        hub = MarketHub(collect_news=False, keep_journal=False)
        assert hub.tape_flow_history().empty, "sans journal, rien"
        hub.journal = Journal(Path(tmp) / "journal.db")
        assert hub.tape_flow_history().empty
        heure = (int(time.time()) // 3600) * 3600 - 2 * 3600
        for ecart, cote, montant in ((60, "buy", 500_000), (900, "buy", 250_000),
                                     (1800, "sell", 300_000),
                                     (3700, "sell", 400_000)):
            hub.journal.record_trade(N(time=heure + ecart, exchange="Binance",
                                       side=cote, price=1.0, quantity=montant,
                                       notional=float(montant)))
        flux = hub.tape_flow_history(days=1)
        assert list(flux.columns) == ["time", "buy", "sell", "net"]
        assert flux["net"].tolist() == [450_000.0, -400_000.0], flux
        assert flux["buy"].tolist() == [750_000.0, 0.0]
        hub.journal.close()

    t0 = pd.Timestamp("2026-11-11 00:00")
    instants = pd.DataFrame({
        "time": [t0, t0 + pd.Timedelta(minutes=5),
                 t0 + pd.Timedelta(minutes=5, seconds=1),
                 t0 + pd.Timedelta(hours=3)],
        "cb_premium": [-0.10, -0.12, float("nan"), -0.05],
        "gap": [False, False, True, False]})
    figure = build_tape_history(flux, instants)
    barres, prime = figure["data"]
    assert barres["type"] == "bar" and barres["y"] == [0.45, -0.4]
    assert len(set(barres["marker"]["color"])) == 2, "achats et ventes, deux couleurs"
    assert prime["yaxis"] == "y2" and prime["y"] == [-0.1, -0.12, None, -0.05]
    # Rien à tracer : le graphique le dit au lieu de rester blanc.
    vide = build_tape_history(flux.iloc[0:0], instants.iloc[0:0])
    assert vide["data"] == [] and "journalisés" in str(vide["layout"])
    # Un seul relevé de prime ne fait pas une ligne ; les barres restent.
    assert len(build_tape_history(flux, instants.iloc[:1])["data"]) == 1

    faux = N(tape_flow_history=lambda days: flux,
             market_snapshots=lambda days: instants)
    _, legende = panneau.render_history(faux)
    texte = str(legende)
    assert "24 h" in texte and "+50 k$" in texte, texte
    assert "7 j" in str(panneau.render_history(faux, expanded=True)[1])
    print("  ✓ solde horaire des gros ordres et prime Coinbase, dans le temps")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nRuban — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le ruban dit ce qui s'exécute, et qui l'a voulu.\n")
