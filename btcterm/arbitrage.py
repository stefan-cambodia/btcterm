"""
Moteur d'arbitrage — socle commun du terminal.

Compare en permanence les carnets de plusieurs plateformes et retient les
paires où acheter sur l'une pour revendre sur l'autre resterait rentable
une fois les frais déduits.

Extrait de `arbitrage/main.py` pour que le panneau du terminal et la TUI
partagent le même calcul.

Ce module observe : il ne passe aucun ordre. Une opportunité affichée peut
avoir disparu avant qu'on puisse l'exécuter, et les frais de transfert
entre plateformes ne sont pas comptés.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from .exchanges import OrderBook

__all__ = ["DEFAULT_FEES", "ArbitrageOpportunity", "ArbitrageEngine"]

# Frais maker par plateforme, en fraction (0.001 = 0,10 %).
DEFAULT_FEES: dict[str, float] = {
    "Binance": 0.001,
    "Kraken": 0.0026,
    "Bybit": 0.001,
    "OKX": 0.001,
    "Coinbase": 0.006,
}


@dataclass
class ArbitrageOpportunity:
    buy_exchange: str
    sell_exchange: str
    buy_price: float
    sell_price: float
    gross_profit_pct: float
    net_profit_pct: float
    buy_fee: float
    sell_fee: float
    min_profit_pct: float = 0.1
    timestamp: float = field(default_factory=time.time)

    @property
    def is_profitable(self) -> bool:
        return self.net_profit_pct > self.min_profit_pct


class ArbitrageEngine:
    """Balaie toutes les paires ordonnées de plateformes.

    Les paires sont ordonnées : acheter sur A pour vendre sur B n'est pas
    la même opération qu'acheter sur B pour vendre sur A.
    """

    def __init__(
        self,
        order_books: dict[str, OrderBook],
        fees: dict[str, float] | None = None,
        min_profit_pct: float = 0.1,
        max_age_ms: float = 5000,
        history_size: int = 100,
    ):
        self.order_books = order_books
        self.fees = fees if fees is not None else DEFAULT_FEES
        self.min_profit_pct = min_profit_pct
        self.max_age_ms = max_age_ms
        self.history_size = history_size

        self.opportunities: list[ArbitrageOpportunity] = []
        self.history: list[ArbitrageOpportunity] = []
        self.stats: defaultdict[str, int] = defaultdict(int)

    def scan(self) -> list[ArbitrageOpportunity]:
        """Recalcule toutes les paires, du plus rentable au moins rentable."""
        opportunities: list[ArbitrageOpportunity] = []
        exchanges = list(self.order_books)

        for buy_ex in exchanges:
            for sell_ex in exchanges:
                if buy_ex == sell_ex:
                    continue

                opportunity = self._evaluate(buy_ex, sell_ex)
                if opportunity is None:
                    continue

                if opportunity.is_profitable:
                    self.stats["total_opportunities"] += 1
                    self.history.append(opportunity)
                    if len(self.history) > self.history_size:
                        self.history.pop(0)

                opportunities.append(opportunity)

        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
        self.opportunities = opportunities
        return opportunities

    def _evaluate(self, buy_ex: str, sell_ex: str) -> ArbitrageOpportunity | None:
        buy_book = self.order_books[buy_ex]
        sell_book = self.order_books[sell_ex]

        if not (buy_book.connected and sell_book.connected):
            return None

        buy_price, sell_price = buy_book.best_ask, sell_book.best_bid
        if not (buy_price and sell_price):
            return None

        # Un carnet trop vieux ne dit plus rien du marché actuel.
        if buy_book.age_ms > self.max_age_ms or sell_book.age_ms > self.max_age_ms:
            return None

        if sell_price <= buy_price:
            return None

        gross_pct = (sell_price - buy_price) / buy_price * 100
        buy_fee = self.fees.get(buy_ex, 0.0) * 100
        sell_fee = self.fees.get(sell_ex, 0.0) * 100

        return ArbitrageOpportunity(
            buy_exchange=buy_ex,
            sell_exchange=sell_ex,
            buy_price=buy_price,
            sell_price=sell_price,
            gross_profit_pct=gross_pct,
            net_profit_pct=gross_pct - buy_fee - sell_fee,
            buy_fee=buy_fee,
            sell_fee=sell_fee,
            min_profit_pct=self.min_profit_pct,
        )
