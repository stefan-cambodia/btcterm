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

Un carnet nourri par deltas ne se corrige jamais seul : une suppression
manquée laisse un **niveau fantôme** — un ask à 78 880 $ quand le marché
est à 80 600 $ — que `best_ask` remonte à chaque lecture, et le moteur
d'arbitrage y voit pendant des heures un écart rentable qui n'existe
pas. Un tel carnet est *croisé* (son meilleur bid dépasse son meilleur
ask), ce qui se détecte : après chaque mise à jour, `_check_book`
laisse passer un croisement fugace, mais un croisement qui tient
`CROSSED_GRACE` secondes lève `BookDesync`, que la boucle de reconnexion
traite comme une panne — resouscription, snapshot neuf, carnet propre.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, Optional

import websockets

__all__ = [
    "MAX_WS_SIZE",
    "OrderBook",
    "BookDesync",
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
log = logging.getLogger("btcterm.exchanges")

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
            # Un carnet entier compte mille niveaux, et le panneau en lit
            # dix-sept dix fois par seconde : on ne trie pas tout pour ça.
            pick = heapq.nlargest if side == "bids" else heapq.nsmallest
            return [(price, levels[price]) for price in pick(depth, levels)]

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

    def quote(self) -> tuple[Optional[float], Optional[float]]:
        """Meilleur bid et meilleur ask, lus ensemble sous un seul verrou.

        Chacun parcourt tout son côté du carnet : qui a besoin des deux, et
        de savoir le carnet croisé, les lit ici une fois plutôt que trois.
        """
        with self._lock:
            return (max(self.bids) if self.bids else None,
                    min(self.asks) if self.asks else None)

    @property
    def mid(self) -> Optional[float]:
        bid, ask = self.best_bid, self.best_ask
        return (bid + ask) / 2 if bid and ask else None

    def depth_within(self, pct: float) -> Optional[tuple[float, float]]:
        """Le volume offert à moins de `pct` % du prix médian : (achats,
        ventes), en bitcoins. `None` pour un carnet vide ou croisé, qui ne
        dit pas le marché."""
        with self._lock:
            if not self.bids or not self.asks:
                return None
            bid, ask = max(self.bids), min(self.asks)
            if bid >= ask:
                return None
            mid = (bid + ask) / 2
            low, high = mid * (1 - pct / 100), mid * (1 + pct / 100)
            return (sum(q for p, q in self.bids.items() if p >= low),
                    sum(q for p, q in self.asks.items() if p <= high))

    @property
    def crossed(self) -> bool:
        """Vrai si le meilleur bid atteint le meilleur ask.

        Un marché ne cote jamais ainsi : un carnet croisé porte un
        niveau fantôme — une suppression manquée dans un flux de
        deltas — et ne dit plus le marché.
        """
        with self._lock:
            return bool(self.bids and self.asks
                        and max(self.bids) >= min(self.asks))

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

class BookDesync(RuntimeError):
    """Le carnet ne reflète plus le flux : à resynchroniser."""


class ExchangeConnector:
    """Base commune : boucle de connexion, reconnexion, état exposé.

    Les sous-classes n'implémentent que `_stream()`, qui doit lire le
    flux et alimenter `self.book`. Toute exception qui en sort est
    traitée comme une déconnexion et déclenche une nouvelle tentative.

    `book` est optionnel : un flux qui n'alimente pas de carnet — celui
    des liquidations, par exemple — passe `None` et redéfinit
    `_mark_connected` / `_mark_disconnected` pour publier son état
    ailleurs. La boucle de reconnexion, elle, reste commune.
    """

    name = "?"

    def __init__(
        self,
        book: Optional[OrderBook] = None,
        max_retries: Optional[int] = None,
        max_backoff: float = 30.0,
    ):
        self.book = book
        self.max_retries = max_retries
        self.max_backoff = max_backoff
        self._running = True
        self._crossed_since: Optional[float] = None

    #: Durée pendant laquelle un carnet croisé est toléré avant
    #: resynchronisation : les deux côtés d'une mise à jour peuvent
    #: arriver dans deux messages, et se croiser entre les deux.
    CROSSED_GRACE = 2.0

    #: Une connexion qui a tenu au moins ce temps avant de tomber n'est
    #: pas la panne précédente qui se répète : le backoff repart de son
    #: premier palier. Sans cela, chaque coupure d'une longue séance —
    #: le réveil après une veille, typiquement — attendait le plafond.
    STABLE_AFTER = 60.0

    def stop(self) -> None:
        self._running = False

    def _check_book(self) -> None:
        """Après une mise à jour : un carnet croisé qui persiste vaut panne.

        Lève `BookDesync`, que `_connect_with_retry` traite comme toute
        autre panne — le carnet porte l'erreur le temps du backoff, la
        resouscription apporte un snapshot neuf. Sans cela, un niveau
        fantôme survivrait jusqu'au prochain redémarrage : le journal
        en a montré un de plus de neuf heures.
        """
        if self.book is None or not self.book.crossed:
            self._crossed_since = None
            return
        now = time.monotonic()
        if self._crossed_since is None:
            self._crossed_since = now
        elif now - self._crossed_since >= self.CROSSED_GRACE:
            self._crossed_since = None
            raise BookDesync("carnet croisé, resynchronisation")

    async def run(self) -> None:
        await self._connect_with_retry(self._stream)

    async def _connect_with_retry(self, coro_factory: Callable[[], Awaitable[None]]) -> None:
        retries = 0
        while self._running and (self.max_retries is None or retries < self.max_retries):
            started = time.monotonic()
            try:
                await coro_factory()
                retries = 0  # la connexion a tenu : on repart de zéro
            except Exception as exc:  # noqa: BLE001 — toute panne vaut reconnexion
                if time.monotonic() - started >= self.STABLE_AFTER:
                    retries = 0  # une connexion qui a tenu : pas la même panne
                if isinstance(exc, BookDesync):
                    # Une resynchronisation n'est pas une panne réseau :
                    # elle dit qu'un carnet a menti, et le journal du
                    # service doit pouvoir le compter.
                    log.warning("%s : %s (bid %s, ask %s)", self.name, exc,
                                self.book.best_bid, self.book.best_ask)
                self._mark_disconnected(exc)
                retries += 1
                await asyncio.sleep(min(2 ** retries, self.max_backoff))

    def _mark_connected(self) -> None:
        if self.book is not None:
            self.book.connected = True
            self.book.error = None

    def _mark_disconnected(self, exc: Exception) -> None:
        if self.book is not None:
            self.book.connected = False
            self.book.error = str(exc)[:50]

    def _connect(self, url: str):
        """Contexte de connexion partagé (ping applicatif + taille de message)."""
        return websockets.connect(url, ping_interval=20, max_size=MAX_WS_SIZE)

    async def _stream(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class BinanceConnector(ExchangeConnector):
    """Le carnet entier : un snapshot REST, tenu à jour par le flux des deltas.

    Le connecteur lisait `@depth20`, vingt niveaux rejoués à chaque
    message : sur le marché le plus fourni, vingt niveaux font 0,004 % du
    prix — le carnet le plus mince du terminal, là où il devait être le
    plus profond, et un trait au ras de zéro dans la profondeur comparée.

    Le flux `@depth` ne donne que des changements ; la plateforme prescrit
    de les accorder à un snapshot pris à côté :

    1. ouvrir le flux et garder ses messages ;
    2. lire le snapshot (`/api/v3/depth`), qui porte son numéro
       (`lastUpdateId`) ;
    3. écarter les messages qu'il contient déjà (`u` ≤ ce numéro), puis
       appliquer les autres, chacun commençant (`U`) là où le précédent
       finit (`u`) — un trou vaut resynchronisation.

    Le carnet local garde `depth` niveaux par côté, quand le marché en a
    davantage : un niveau lointain que personne ne modifie ne sera jamais
    redit, et un côté que le cours a traversé s'amincirait. Le snapshot
    est donc **relu de temps en temps** (`REFRESH`), sans fermer le flux —
    les messages continuent de s'appliquer pendant la lecture, et ceux qui
    la suivent sont rejoués par-dessus. Une relecture qui échoue ne
    retire rien à un carnet qui marche : elle sera retentée.
    """

    name = "Binance"

    #: Période de relecture du snapshot, en secondes.
    REFRESH = 600.0
    #: Délai avant de relire un snapshot arrivé en retard sur le flux, et
    #: nombre de retards tolérés avant de tout reprendre.
    RETRY = 1.0
    STALE_MAX = 5

    def __init__(self, book: OrderBook, symbol: str = "btcusdt",
                 depth: int = 1000, speed: str = "100ms",
                 fetch: Optional[Callable[[], dict]] = None, **kwargs):
        super().__init__(book, **kwargs)
        self.url = f"wss://stream.binance.com:9443/ws/{symbol}@depth@{speed}"
        self.snapshot_url = ("https://api.binance.com/api/v3/depth"
                             f"?symbol={symbol.upper()}&limit={depth}")
        book.max_levels = depth
        self._fetch = fetch or self._fetch_snapshot
        self._last: Optional[int] = None   # `u` du dernier message appliqué

    def _fetch_snapshot(self) -> dict:
        import requests
        response = requests.get(self.snapshot_url, timeout=10)
        response.raise_for_status()
        return response.json()

    async def _stream(self) -> None:
        self._last = None
        async with self._connect(self.url) as ws:
            reading: Optional[asyncio.Task] = None
            pending: list[dict] = []
            refresh_at = 0.0
            stale = 0
            try:
                async for raw in ws:
                    if not self._running:
                        break
                    event = json.loads(raw)
                    if "u" not in event:
                        continue
                    # Le snapshot se demande après le premier message : pris
                    # avant, il pourrait s'arrêter sous lui, et laisser un trou.
                    if reading is None and time.monotonic() >= refresh_at:
                        reading = asyncio.create_task(
                            asyncio.to_thread(self._fetch))
                        pending = []
                    if reading is not None:
                        pending.append(event)
                    if self._last is not None:
                        self._apply(event)
                    if reading is not None and reading.done():
                        first = self._last is None
                        try:
                            snapshot = reading.result()
                        except Exception:
                            if first:
                                raise       # pas de carnet du tout : panne
                            snapshot = None  # le carnet tient : on retentera
                        reading = None
                        refresh_at = time.monotonic() + self.REFRESH
                        # Le REST sert parfois un carnet plus vieux que le
                        # flux : s'il s'arrête avant le premier message
                        # gardé, il laisserait un trou. Il est relu — tout
                        # de suite s'il n'y a pas encore de carnet.
                        if (snapshot is not None and pending and
                                int(snapshot["lastUpdateId"]) + 1 < pending[0]["U"]):
                            snapshot = None
                            stale += 1
                            if first and stale >= self.STALE_MAX:
                                raise BookDesync(
                                    "snapshot toujours en retard sur le flux")
                            refresh_at = time.monotonic() + self.RETRY
                        if snapshot is not None:
                            stale = 0
                            self._load(snapshot, pending)
                            if first:
                                self._mark_connected()
                        pending = []
            finally:
                if reading is not None:
                    reading.cancel()

    def _load(self, snapshot: dict, pending: Iterable[dict]) -> None:
        """Pose le snapshot, et rejoue par-dessus les messages qui le suivent."""
        last = int(snapshot["lastUpdateId"])
        self.book.replace(
            {float(p): float(q) for p, q in snapshot.get("bids", [])},
            {float(p): float(q) for p, q in snapshot.get("asks", [])},
        )
        self._last = last
        for event in pending:
            if event["u"] <= last:
                continue            # déjà dans le snapshot
            self._apply(event)

    def _apply(self, event: dict) -> None:
        """Un message de deltas, qui doit suivre le précédent sans trou."""
        if event["U"] > self._last + 1:
            raise BookDesync(
                f"message perdu ({self._last + 1} attendu, {event['U']} reçu), "
                "resynchronisation")
        if event["u"] <= self._last:
            return                  # redite d'un message déjà appliqué
        self.book.apply(
            [(float(p), float(q)) for p, q in event.get("b", [])],
            [(float(p), float(q)) for p, q in event.get("a", [])],
        )
        self._last = event["u"]
        self._check_book()


class KrakenConnector(ExchangeConnector):
    """Snapshot (`as`/`bs`) puis mises à jour incrémentales (`a`/`b`).

    Kraken répartit parfois les deux côtés d'une mise à jour sur plusieurs
    éléments du message ; on balaie donc tous les dictionnaires utiles
    plutôt que le seul `data[1]`.

    Le flux décrit une **fenêtre** de `depth` niveaux par côté, pas le
    carnet entier : un niveau qu'une insertion pousse hors de la fenêtre
    en sort sans message, et Kraken ne dira plus rien de lui — pas même
    sa disparition. Un carnet qui en garderait davantage accumulerait
    donc des fantômes, que le prix finirait par croiser : le journal du
    service en comptait cinquante-quatre resynchronisations en une
    matinée. Le carnet est donc borné à la fenêtre.
    """

    name = "Kraken"

    def __init__(self, book: OrderBook, pair: str = "XBT/USD",
                 depth: int = 100, **kwargs):
        super().__init__(book, **kwargs)
        # Pas plus de niveaux que la fenêtre : `apply` élague ce qu'une
        # insertion en chasse, comme Kraken le fait de son côté.
        book.max_levels = depth
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
                self._handle(json.loads(raw))

    def _handle(self, data) -> None:
        # Les messages utiles sont des listes ; les messages système
        # (heartbeat, statut d'abonnement…) sont des dicts.
        if not isinstance(data, list):
            return

        parts = [p for p in data[1:-2] if isinstance(p, dict)]
        if not parts:
            return

        if any("as" in p or "bs" in p for p in parts):
            bids, asks = {}, {}
            for part in parts:
                bids.update({float(p): float(v) for p, v, *_ in part.get("bs", [])})
                asks.update({float(p): float(v) for p, v, *_ in part.get("as", [])})
            self.book.replace(bids, asks)
            self._check_book()
            return

        bid_updates, ask_updates = [], []
        for part in parts:
            bid_updates += [(float(p), float(v)) for p, v, *_ in part.get("b", [])]
            ask_updates += [(float(p), float(v)) for p, v, *_ in part.get("a", [])]
        if bid_updates or ask_updates:
            self.book.apply(bid_updates, ask_updates)
            self._check_book()


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
                self._handle(json.loads(raw))

    def _handle(self, data: dict) -> None:
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
            self._check_book()


class CoinbaseAdvancedConnector(ExchangeConnector):
    """Flux Advanced Trade : un snapshot à l'abonnement, puis des mises à jour.

    Nécessaire pour les paires en USDT, absentes du flux public Exchange.
    Les deux formes ont le même format — une liste de niveaux — et ne se
    distinguent que par le `type` de l'événement. Le connecteur les
    appliquait toutes comme des mises à jour : correct à la première
    connexion, sur un carnet vide, mais à chaque reconnexion le snapshot
    neuf se posait **par-dessus** l'ancien carnet, dont les niveaux
    disparus entre-temps ne seraient plus jamais supprimés. C'est ainsi
    qu'un ask à 78 880 $ a tenu neuf heures dans le carnet pendant que
    le marché cotait 80 600 $. Le snapshot remplace, désormais.
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
                self._handle(json.loads(raw))

    def _handle(self, data: dict) -> None:
        for event in data.get("events", []):
            bid_updates, ask_updates = [], []
            for update in event.get("updates", []):
                entry = (float(update["price_level"]), float(update["new_quantity"]))
                if update["side"] == "bid":
                    bid_updates.append(entry)
                else:
                    ask_updates.append(entry)
            if event.get("type") == "snapshot":
                self.book.replace(dict(bid_updates), dict(ask_updates))
            elif bid_updates or ask_updates:
                self.book.apply(bid_updates, ask_updates)
            self._check_book()


class BybitConnector(ExchangeConnector):
    """Snapshot initial puis deltas sur le canal `orderbook.N`."""

    name = "Bybit"

    def __init__(self, book: OrderBook, symbol: str = "BTCUSDT",
                 depth: int = 50, **kwargs):
        super().__init__(book, **kwargs)
        self.url = "wss://stream.bybit.com/v5/public/spot"
        self.subscription = {"op": "subscribe", "args": [f"orderbook.{depth}.{symbol}"]}
        # Le flux tient une fenêtre de `depth` niveaux : le carnet garde la
        # même, ni plus (des fantômes) ni moins (un côté qui s'amincit
        # quand le cours le traverse).
        book.max_levels = depth

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(json.dumps(self.subscription))
            self._mark_connected()

            async for raw in ws:
                if not self._running:
                    break
                self._handle(json.loads(raw))

    def _handle(self, data: dict) -> None:
        if not data.get("topic", "").startswith("orderbook"):
            return

        payload = data.get("data", {})
        bids = [(float(p), float(q)) for p, q in payload.get("b", [])]
        asks = [(float(p), float(q)) for p, q in payload.get("a", [])]

        if data.get("type") == "snapshot":
            self.book.replace(dict(bids), dict(asks))
        else:
            self.book.apply(bids, asks)
        self._check_book()


class OKXConnector(ExchangeConnector):
    """Canal `books` : snapshot de 400 niveaux par côté, puis deltas.

    Le connecteur lisait `books5`, cinq niveaux rejoués à chaque message :
    assez pour l'arbitrage, trop peu pour le reste — un carnet de cinq
    lignes là où les autres en montrent dix-sept, cinq points dans la
    profondeur comparée, presque rien dans les murs. `books` donne le
    carnet entier de la fenêtre, tenu à jour toutes les cent millisecondes.

    Chaque delta porte le numéro du message qui le précède (`prevSeqId`) :
    un numéro qui ne suit pas dit un message perdu, donc un carnet faux,
    et vaut resynchronisation — la resouscription apporte un snapshot neuf.

    Le port 443, et non le 8443 d'origine : la plateforme annonçait à
    chaque connexion, le 11 novembre 2026, que ce dernier « sera bientôt
    retiré ».
    """

    name = "OKX"

    def __init__(self, book: OrderBook, inst_id: str = "BTC-USDT",
                 depth: int = 400, **kwargs):
        super().__init__(book, **kwargs)
        self.url = "wss://ws.okx.com/ws/v5/public"
        self.subscription = {
            "op": "subscribe",
            "args": [{"channel": "books", "instId": inst_id}],
        }
        # Le flux décrit une fenêtre de `depth` niveaux : le carnet s'y
        # borne, comme celui de Kraken à la sienne — un niveau sorti de la
        # fenêtre n'est plus mis à jour, et y resterait en fantôme.
        book.max_levels = depth
        self._seq: Optional[int] = None

    async def _stream(self) -> None:
        async with self._connect(self.url) as ws:
            await ws.send(json.dumps(self.subscription))
            self._mark_connected()
            self._seq = None

            async for raw in ws:
                if not self._running:
                    break
                self._handle(json.loads(raw))

    def _handle(self, data: dict) -> None:
        """Un message du canal : snapshot, delta, ou autre chose à ignorer
        (acquittement, avis de la plateforme)."""
        action = data.get("action")
        for item in data.get("data", []):
            bids = [(float(p), float(q)) for p, q, *_ in item.get("bids", [])]
            asks = [(float(p), float(q)) for p, q, *_ in item.get("asks", [])]
            seq, prev = item.get("seqId"), item.get("prevSeqId")
            if action == "snapshot" or self._seq is None:
                if action != "snapshot":
                    raise BookDesync("delta reçu avant le snapshot")
                self.book.replace(dict(bids), dict(asks))
            else:
                if prev is not None and prev != self._seq:
                    raise BookDesync(
                        f"message perdu ({self._seq} attendu, {prev} reçu), "
                        "resynchronisation")
                self.book.apply(bids, asks)
            if seq is not None:
                self._seq = seq
        if action:
            self._check_book()


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
