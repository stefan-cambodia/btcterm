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

#: Frais **taker** par plateforme, au premier palier d'un compte qui
#: commence, en fraction (0.001 = 0,10 %). Taker, parce que le moteur
#: achète au meilleur vendeur et revend au meilleur acheteur : deux ordres
#: qui prennent la liquidité. Le tableau se disait « maker » et mêlait les
#: deux ; Kraken y figurait à 0,26 %, le taker de son ancien barème, quand
#: sa page officielle (kraken.com/pro/fees, lue le 12 novembre 2026) donne
#: 0,40 % maker et 0,80 % taker au premier palier, BTC/USDT relevant du
#: barème des cryptomonnaies et non de celui des stablecoins — un écart
#: passant par Kraken paraissait rentable de 0,54 point de trop. Binance,
#: Bybit et OKX : 0,10 % (page officielle de Bybit,
#: sources concordantes pour les deux autres). Coinbase ne publie plus son
#: barème hors d'un compte connecté ; les sources secondaires citent 0,60
#: à 0,90 % au premier palier : 0,60 %, non vérifiable. Un compte à fort
#: volume paie moins — le net affiché est celui du premier palier.
DEFAULT_FEES: dict[str, float] = {
    "Binance": 0.001,
    "Kraken": 0.008,
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

    def evaluate(self) -> list[ArbitrageOpportunity]:
        """Toutes les paires, de la plus rentable à la moins rentable — sans
        rien compter : c'est la lecture des panneaux, qui la refont autant de
        fois qu'il y a d'onglets ouverts et de trames par seconde."""
        opportunities: list[ArbitrageOpportunity] = []
        # Chaque carnet est lu une fois, pas une fois par paire : lire son
        # meilleur prix parcourt tout un côté, et vingt paires y revenaient
        # cent vingt fois par balayage.
        quotes = {name: self._quote(book)
                  for name, book in self.order_books.items()}

        for buy_ex, buy_quote in quotes.items():
            for sell_ex, sell_quote in quotes.items():
                if buy_ex == sell_ex or buy_quote is None or sell_quote is None:
                    continue

                opportunity = self._evaluate(buy_ex, sell_ex,
                                             buy_quote[1], sell_quote[0])
                if opportunity is not None:
                    opportunities.append(opportunity)

        opportunities.sort(key=lambda o: o.net_profit_pct, reverse=True)
        return opportunities

    def scan(self) -> list[ArbitrageOpportunity]:
        """Évalue toutes les paires et compte les rentables.

        Réservé au balayage du hub, une fois par seconde : le compteur
        « détectées » et l'historique se comptent en secondes de paire
        rentable. Appelé aussi par le panneau, ils avançaient de dix par
        seconde et par onglet ouvert, le pousseur rendant l'arbitrage à
        chaque trame.
        """
        opportunities = self.evaluate()
        for opportunity in opportunities:
            if opportunity.is_profitable:
                self.stats["total_opportunities"] += 1
                self.history.append(opportunity)
                if len(self.history) > self.history_size:
                    self.history.pop(0)
        self.opportunities = opportunities
        return opportunities

    def _quote(self, book: OrderBook) -> tuple | None:
        """Le meilleur bid et le meilleur ask d'un carnet qui compte — l'un
        ou l'autre pouvant manquer —, ou `None` s'il ne compte pas."""
        if not book.connected:
            return None

        # Un carnet trop vieux ne dit plus rien du marché actuel.
        if book.age_ms > self.max_age_ms:
            return None

        bid, ask = book.quote()
        # Un carnet croisé porte un niveau fantôme : l'écart qu'il montre
        # n'existe pas. Le connecteur le resynchronise ; d'ici là, il ne
        # compte pas.
        if bid is not None and ask is not None and bid >= ask:
            return None
        return bid, ask

    def _evaluate(self, buy_ex: str, sell_ex: str, buy_price, sell_price
                  ) -> ArbitrageOpportunity | None:
        if not (buy_price and sell_price):
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
