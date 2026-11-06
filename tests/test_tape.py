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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nRuban — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le ruban dit ce qui s'exécute, et qui l'a voulu.\n")
