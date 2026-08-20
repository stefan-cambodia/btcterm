"""
Panneaux du terminal.

Chaque module expose :

- `layout()` — les composants Dash du panneau
- `register(app, hub)` — ses callbacks, branchés sur le hub de données

Un panneau ne parle jamais au réseau directement : il demande au hub.

`PANELS` est la liste de référence : l'application enregistre les
callbacks en la parcourant, si bien qu'ajouter un module ici suffit à le
brancher. Un panneau déclaré et oublié n'est donc plus possible — c'est
exactement l'erreur qui a laissé le panneau ETF muet à sa création.
"""

from . import (alerts, arbitrage, calendar, dominance, etf, liquidations,
               macro, news, onchain, orderbook, perp, price)

PANELS = (price, orderbook, arbitrage, liquidations, etf, perp, news, calendar,
          alerts, macro, dominance, onchain)

__all__ = ["PANELS", "price", "orderbook", "arbitrage", "liquidations", "etf",
           "perp", "news", "calendar", "alerts", "macro", "dominance",
           "onchain"]
