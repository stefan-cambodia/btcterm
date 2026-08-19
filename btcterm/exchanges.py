"""
Connecteurs WebSocket et carnet d'ordres — socle commun du terminal.

Ce module normalise les flux de profondeur de plusieurs plateformes vers
une structure unique, `OrderBook`, de sorte qu'un même carnet puisse
alimenter plusieurs panneaux (profondeur, arbitrage, liquidité) sans
ouvrir une connexion par panneau.

Il fusionne les deux implémentations qui coexistaient dans
`btc_orderbook_live.py` et `arbitrage/main.py` :

- de la première, les garde-fous indispensables face aux carnets complets
  (`max_levels`, `MAX_WS_SIZE`) et le parsing Kraken tolérant aux
  messages dont les deltas sont répartis sur plusieurs éléments ;
- de la seconde, la classe de base avec reconnexion à backoff
  exponentiel et l'état `connected` / `error` exposé à l'interface.

Les paires ne sont pas identiques partout (BTC/USD sur le flux public
Coinbase, BTC/USDT sur l'Advanced Trade) : chaque connecteur prend donc
son produit en paramètre plutôt que de le coder en dur.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Optional

import websockets

__all__ = [
    "MAX_WS_SIZE",
    "OrderBook",
    "ExchangeConnector",
    "BinanceConnector",
    "KrakenConnector",
    "CoinbaseConnector",
    "CoinbaseAdvancedConnector",
    "BybitConnector",
    "OKXConnector",
    "run_connectors",
    "run_connectors_in_thread",
]

# Le snapshot complet de Coinbase dépasse souvent la limite de message par
# défaut de `websockets` (1 Mo), ce qui referme la connexion aussitôt.
MAX_WS_SIZE = 20 * 1024 * 1024  # 20 Mo

# Nombre de niveaux conservés de chaque côté. Sans cette troncature, un
# carnet complet (plusieurs milliers de lignes chez Coinbase) fait gonfler
# mémoire et temps de rendu sans rien apporter : seuls les niveaux proches
# du marché sont exploitables.
DEFAULT_MAX_LEVELS = 100


# ─────────────────────────────────────────────────────────────
# Carnet d'ordres
# ─────────────────────────────────────────────────────────────

@dataclass
class OrderBook:
    """Carnet d'une plateforme, sûr à lire depuis un autre thread.

    Les niveaux sont stockés en `{prix: quantité}` : c'est la forme qui
    accepte aussi bien un snapshot complet qu'une mise à jour
    incrémentale. Les vues triées sont produites à la lecture.
    """

    exchange: str
    max_levels: int = DEFAULT_MAX_LEVELS
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    connected: bool = False
    error: Optional[str] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # ── Écriture ────────────────────────────────────────────

    def replace(self, bids: dict[float, float], asks: dict[float, float]) -> None:
        """Remplace entièrement le carnet (message de type snapshot)."""
        with self._lock:
            self.bids = self._trim(bids, descending=True)
            self.asks = self._trim(asks, descending=False)
            self.timestamp = time.time()

    def apply(
        self,
        bid_updates: Iterable[tuple[float, float]] = (),
        ask_updates: Iterable[tuple[float, float]] = (),
    ) -> None:
        """Applique des mises à jour incrémentales.

        Une quantité nulle supprime le niveau de prix, conformément à la
        convention commune à toutes les plateformes supportées.
        """
        with self._lock:
            for price, qty in bid_updates:
                if qty == 0:
                    self.bids.pop(price, None)
                else:
                    self.bids[price] = qty
            for price, qty in ask_updates:
                if qty == 0:
                    self.asks.pop(price, None)
                else:
                    self.asks[price] = qty
            self.bids = self._trim(self.bids, descending=True)
            self.asks = self._trim(self.asks, descending=False)
            self.timestamp = time.time()

    def touch(self) -> None:
        """Marque le carnet comme frais sans en modifier le contenu."""
        with self._lock:
            self.timestamp = time.time()

    def _trim(self, levels: dict[float, float], descending: bool) -> dict[float, float]:
        if len(levels) <= self.max_levels:
            return levels
        kept = sorted(levels, reverse=descending)[: self.max_levels]
        return {price: levels[price] for price in kept}

    # ── Lecture ─────────────────────────────────────────────

    def snapshot(self) -> tuple[dict[float, float], dict[float, float]]:
        """Copie cohérente des deux côtés, à lire hors verrou ensuite."""
        with self._lock:
            return dict(self.bids), dict(self.asks)

    def top(self, side: str, depth: int) -> list[tuple[float, float]]:
        """`depth` meilleurs niveaux d'un côté, triés du meilleur au pire."""
        with self._lock:
            levels = self.bids if side == "bids" else self.asks
            prices = sorted(levels, reverse=(side == "bids"))[:depth]
            return [(price, levels[price]) for price in prices]

    def cumulative_depth(self, side: str) -> tuple[list[float], list[float]]:
        """Courbe de profondeur cumulée, ordonnée par prix croissant."""
        descending = side == "bids"
        with self._lock:
            levels = dict(self.bids if descending else self.asks)

        prices, cumulated, running = [], [], 0.0
        for price in sorted(levels, reverse=descending):
            running += levels[price]
            prices.append(price)
            cumulated.append(running)
        if descending:
            prices.reverse()
            cumulated.reverse()
        return prices, cumulated

    @property
    def best_bid(self) -> Optional[float]:
        with self._lock:
            return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        with self._lock:
            return min(self.asks) if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        bid, ask = self.best_bid, self.best_ask
        return (bid + ask) / 2 if bid and ask else None

    @property
    def spread(self) -> Optional[float]:
        bid, ask = self.best_bid, self.best_ask
        return ask - bid if bid and ask else None

    @property
    def spread_pct(self) -> Optional[float]:
        bid, ask = self.best_bid, self.best_ask
        if bid and ask and bid > 0:
            return (ask - bid) / bid * 100
        return None

    @property
    def age_ms(self) -> float:
        """Âge de la dernière mise à jour, en millisecondes."""
        return (time.time() - self.timestamp) * 1000


# ─────────────────────────────────────────────────────────────
# Connecteurs
# ─────────────────────────────────────────────────────────────

class ExchangeConnector:
    """Base commune : boucle de connexion, reconnexion, état exposé.

    Les sous-classes n'implémentent que `_stream()`, qui doit lire le
    flux et alimenter `self.book`. Toute exception qui en sort est
    traitée comme une déconnexion et déclenche une nouvelle tentative.
    """

    name = "?"

    def __init__(
        self,
        book: OrderBook,
        max_retries: Optional[int] = None,
        max_backoff: float = 30.0,
    ):
        self.book = book
        self.max_retries = max_retries
        self.max_backoff = max_backoff
        self._running = True

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        await self._connect_with_retry(self._stream)

    async def _connect_with_retry(self, coro_factory: Callable[[], Awaitable[None]]) -> None:
        retries = 0
        while self._running and (self.max_retries is None or retries < self.max_retries):
            try:
                await coro_factory()
                retries = 0  # la connexion a tenu : on repart de zéro
            except Exception as exc:  # noqa: BLE001 — toute panne vaut reconnexion
                self.book.connected = False
                self.book.error = str(exc)[:50]
                retries += 1
                await asyncio.sleep(min(2 ** retries, self.max_backoff))

    def _mark_connected(self) -> None:
        self.book.connected = True
        self.book.error = None

    def _connect(self, url: str):
        """Contexte de connexion partagé (ping applicatif + taille de message)."""
        return websockets.connect(url, ping_interval=20, max_size=MAX_WS_SIZE)

    async def _stream(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class BinanceConnector(ExchangeConnector):
    """Snapshot complet des N meilleurs niveaux, poussé à intervalle fixe.

    Pas de delta à gérer : chaque message remplace le carnet.
    """

    name = "Binance"

    def __init__(self, book: OrderBook, symbol: str = "btcusdt",
                 depth: int = 20, speed: str = "100ms", **kwargs):
        super().__init__(book, **kwargs)
        self.url = f"wss://stream.binance.com:9443/ws/{symbol}@depth{depth}@{speed}"

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            self._mark_connected()
            async for raw in ws:
                if not self._running:
                    break
                data = json.loads(raw)
                self.book.replace(
                    {float(p): float(q) for p, q in data.get("bids", [])},
                    {float(p): float(q) for p, q in data.get("asks", [])},
                )


class KrakenConnector(ExchangeConnector):
    """Snapshot (`as`/`bs`) puis mises à jour incrémentales (`a`/`b`).

    Kraken répartit parfois les deux côtés d'une mise à jour sur plusieurs
    éléments du message ; on balaie donc tous les dictionnaires utiles
    plutôt que le seul `data[1]`.
    """

    name = "Kraken"

    def __init__(self, book: OrderBook, pair: str = "XBT/USD",
                 depth: int = 100, **kwargs):
        super().__init__(book, **kwargs)
        self.url = "wss://ws.kraken.com"
        self.subscription = {
            "event": "subscribe",
            "pair": [pair],
            "subscription": {"name": "book", "depth": depth},
        }

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(json.dumps(self.subscription))
            self._mark_connected()

            async for raw in ws:
                if not self._running:
                    break
                data = json.loads(raw)

                # Les messages utiles sont des listes ; les messages système
                # (heartbeat, statut d'abonnement…) sont des dicts.
                if not isinstance(data, list):
                    continue

                parts = [p for p in data[1:-2] if isinstance(p, dict)]
                if not parts:
                    continue

                if any("as" in p or "bs" in p for p in parts):
                    bids, asks = {}, {}
                    for part in parts:
                        bids.update({float(p): float(v) for p, v, *_ in part.get("bs", [])})
                        asks.update({float(p): float(v) for p, v, *_ in part.get("as", [])})
                    self.book.replace(bids, asks)
                    continue

                bid_updates, ask_updates = [], []
                for part in parts:
                    bid_updates += [(float(p), float(v)) for p, v, *_ in part.get("b", [])]
                    ask_updates += [(float(p), float(v)) for p, v, *_ in part.get("a", [])]
                if bid_updates or ask_updates:
                    self.book.apply(bid_updates, ask_updates)


class CoinbaseConnector(ExchangeConnector):
    """Flux public Exchange : snapshot complet puis `l2update`.

    Le snapshot initial peut compter plusieurs milliers de niveaux, d'où
    `MAX_WS_SIZE` et la troncature du carnet.
    """

    name = "Coinbase"

    def __init__(self, book: OrderBook, product: str = "BTC-USD", **kwargs):
        super().__init__(book, **kwargs)
        self.url = "wss://ws-feed.exchange.coinbase.com"
        self.subscription = {
            "type": "subscribe",
            "product_ids": [product],
            "channels": ["level2_batch"],
        }

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(json.dumps(self.subscription))
            self._mark_connected()

            async for raw in ws:
                if not self._running:
                    break
                data = json.loads(raw)
                kind = data.get("type")

                if kind == "snapshot":
                    self.book.replace(
                        {float(p): float(q) for p, q in data.get("bids", [])},
                        {float(p): float(q) for p, q in data.get("asks", [])},
                    )
                elif kind == "l2update":
                    bid_updates, ask_updates = [], []
                    for side, price, qty in data.get("changes", []):
                        entry = (float(price), float(qty))
                        (bid_updates if side == "buy" else ask_updates).append(entry)
                    self.book.apply(bid_updates, ask_updates)


class CoinbaseAdvancedConnector(ExchangeConnector):
    """Flux Advanced Trade : uniquement des mises à jour de niveaux.

    Nécessaire pour les paires en USDT, absentes du flux public Exchange.
    """

    name = "Coinbase"

    def __init__(self, book: OrderBook, product: str = "BTC-USDT", **kwargs):
        super().__init__(book, **kwargs)
        self.url = "wss://advanced-trade-ws.coinbase.com"
        self.subscription = {
            "type": "subscribe",
            "product_ids": [product],
            "channel": "level2",
        }

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(json.dumps(self.subscription))
            self._mark_connected()

            async for raw in ws:
                if not self._running:
                    break
                data = json.loads(raw)
                bid_updates, ask_updates = [], []
                for event in data.get("events", []):
                    for update in event.get("updates", []):
                        entry = (float(update["price_level"]), float(update["new_quantity"]))
                        if update["side"] == "bid":
                            bid_updates.append(entry)
                        else:
                            ask_updates.append(entry)
                if bid_updates or ask_updates:
                    self.book.apply(bid_updates, ask_updates)


class BybitConnector(ExchangeConnector):
    """Snapshot initial puis deltas sur le canal `orderbook.N`."""

    name = "Bybit"

    def __init__(self, book: OrderBook, symbol: str = "BTCUSDT",
                 depth: int = 50, **kwargs):
        super().__init__(book, **kwargs)
        self.url = "wss://stream.bybit.com/v5/public/spot"
        self.subscription = {"op": "subscribe", "args": [f"orderbook.{depth}.{symbol}"]}

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(json.dumps(self.subscription))
            self._mark_connected()

            async for raw in ws:
                if not self._running:
                    break
                data = json.loads(raw)
                if not data.get("topic", "").startswith("orderbook"):
                    continue

                payload = data.get("data", {})
                bids = [(float(p), float(q)) for p, q in payload.get("b", [])]
                asks = [(float(p), float(q)) for p, q in payload.get("a", [])]

                if data.get("type") == "snapshot":
                    self.book.replace(dict(bids), dict(asks))
                else:
                    self.book.apply(bids, asks)


class OKXConnector(ExchangeConnector):
    """Canal `books5` : snapshot des 5 meilleurs niveaux à chaque message."""

    name = "OKX"

    def __init__(self, book: OrderBook, inst_id: str = "BTC-USDT", **kwargs):
        super().__init__(book, **kwargs)
        self.url = "wss://ws.okx.com:8443/ws/v5/public"
        self.subscription = {
            "op": "subscribe",
            "args": [{"channel": "books5", "instId": inst_id}],
        }

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(json.dumps(self.subscription))
            self._mark_connected()

            async for raw in ws:
                if not self._running:
                    break
                data = json.loads(raw)
                for item in data.get("data", []):
                    self.book.replace(
                        {float(p): float(q) for p, q, *_ in item.get("bids", [])},
                        {float(p): float(q) for p, q, *_ in item.get("asks", [])},
                    )


# ─────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────

async def run_connectors(connectors: Iterable[ExchangeConnector]) -> None:
    """Fait tourner tous les connecteurs jusqu'à annulation."""
    await asyncio.gather(*(c.run() for c in connectors))


def run_connectors_in_thread(
    connectors: Iterable[ExchangeConnector],
) -> threading.Thread:
    """Lance les connecteurs dans un thread démon avec sa propre boucle.

    Destiné aux interfaces qui doivent garder le thread principal, comme
    le serveur Dash du terminal.
    """
    connectors = list(connectors)

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_connectors(connectors))

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread
