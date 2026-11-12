#!/usr/bin/env python3
"""
Le moteur d'arbitrage : ce que coûte un écart, et à qui.

Le moteur achète au meilleur vendeur d'une plateforme et revend au
meilleur acheteur d'une autre : deux ordres qui prennent la liquidité,
au tarif **taker**. Le tableau des frais se disait « maker » et mêlait
les deux ; Kraken y figurait à 0,26 % quand sa page officielle donne
0,80 % au premier palier (lue le 12 novembre 2026) — un écart passant
par Kraken paraissait rentable de 0,54 point de trop.

Ce test vérifie le calcul du net sur deux carnets fabriqués, et garde
les frais vérifiés à la source : changer l'un d'eux doit être un geste
délibéré, daté, et non une régression.

Aucun réseau n'est touché.

Lancement :
    python tests/test_arbitrage.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.arbitrage import DEFAULT_FEES, ArbitrageEngine  # noqa: E402
from btcterm.exchanges import OrderBook  # noqa: E402


def carnet(nom: str, bid: float, ask: float) -> OrderBook:
    book = OrderBook(nom)
    book.replace({bid: 1.0}, {ask: 1.0})
    book.connected = True
    return book


def test_le_net_retire_les_deux_frais_taker():
    """Acheter sur Binance à 100 000, revendre sur Kraken à 101 000 :
    1 % brut, moins 0,10 % chez Binance et 0,80 % chez Kraken."""
    moteur = ArbitrageEngine({
        "Binance": carnet("Binance", 99_990.0, 100_000.0),
        "Kraken": carnet("Kraken", 101_000.0, 101_010.0),
    })
    (ecart,) = [o for o in moteur.evaluate()
                if (o.buy_exchange, o.sell_exchange) == ("Binance", "Kraken")]
    assert abs(ecart.gross_profit_pct - 1.0) < 1e-9
    assert abs(ecart.buy_fee - 0.10) < 1e-9 and abs(ecart.sell_fee - 0.80) < 1e-9
    assert abs(ecart.net_profit_pct - 0.10) < 1e-9, ecart.net_profit_pct
    # À 0,26 %, l'ancien taux de Kraken, le même écart rapportait 0,64 %.
    assert ecart.is_profitable is False, "0,10 % net n'atteint pas le seuil"
    print("  ✓ 1 % brut, 0,10 % net : les deux frais taker retirés")


def test_les_frais_verifies_a_la_source():
    """Premier palier d'un compte qui commence, taker, vérifiés le
    12 novembre 2026 — sauf Coinbase, qui ne publie plus son barème hors
    d'un compte connecté (0,60 à 0,90 % selon les sources secondaires)."""
    assert DEFAULT_FEES == {"Binance": 0.001, "Kraken": 0.008,
                            "Bybit": 0.001, "OKX": 0.001, "Coinbase": 0.006}
    print("  ✓ Binance, Bybit, OKX 0,10 % ; Kraken 0,80 % ; Coinbase 0,60 %")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nArbitrage — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le net d'un écart est celui qu'on paierait.\n")
