"""
Fil des liquidations forcées — Binance Futures, et Bybit.

Quand une position à effet de levier ne couvre plus sa marge, la
plateforme la ferme au marché : c'est une liquidation. Elles arrivent par
rafales, et ces rafales expliquent une partie des mèches qu'on voit sur
le graphique du cours — d'où l'intérêt de les regarder à côté du carnet
plutôt que dans un onglet de navigateur.

Le flux `!forceOrder@arr` diffuse celles de **toutes** les paires, sans
clé. On les garde toutes : une cascade sur les altcoins précède souvent
celle du Bitcoin. Le côté est déduit du sens de l'ordre — une vente
forcée liquide un long, un achat forcé liquide un short.

Le flux est épisodique par nature : il peut rester silencieux plusieurs
minutes sans que rien n'aille mal.

Depuis certains pays, Binance ouvre ses flux WebSocket futures mais n'y
livre rien (voir btcterm/resolver.py) : le fil reste alors muet sans
qu'aucune erreur ne le dise. Une seconde source, Bybit, alimente donc le
**même** magasin d'événements par un connecteur à part
(`BybitLiquidationConnector`) : son canal `allLiquidation` est par paire,
sans joker, d'où une liste de paires plutôt que « toutes ». Chaque
événement dit d'où il vient (`exchange`), et le fil publie l'état de
chacun de ses liens — le panneau sait dire lequel manque, et lequel
tient sans rien livrer (`silent`) : un lien ouvert et muet ne se
distingue d'un marché calme que par la durée du silence.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .exchanges import ExchangeConnector

__all__ = ["Liquidation", "LiquidationFeed", "BybitLiquidationConnector",
           "BYBIT_SYMBOLS"]

BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"
BYBIT_LINEAR_WS = "wss://stream.bybit.com/v5/public/linear"

#: Paires suivies chez Bybit — le canal `allLiquidation` se souscrit par
#: paire. Les plus grosses capitalisations : c'est là que les cascades
#: se lisent, et une cascade sur les altcoins précède souvent celle du
#: Bitcoin.
BYBIT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                 "BNBUSDT", "ADAUSDT", "LINKUSDT", "SUIUSDT", "AVAXUSDT")


@dataclass(frozen=True)
class Liquidation:
    """Une position fermée de force."""

    time: float
    symbol: str
    side: str          #: `long` ou `short` — la position qui a sauté
    price: float
    quantity: float
    exchange: str = "Binance"   #: la plateforme qui a fermé la position

    @property
    def notional(self) -> float:
        """Taille de la position liquidée, en dollars."""
        return self.price * self.quantity


class LiquidationFeed(ExchangeConnector):
    """Fil temps réel, gardé en mémoire dans une fenêtre glissante.

    Le fil lui-même ne persiste rien : le panneau lit les dernières
    liquidations et leurs totaux, et ce qui sort de la fenêtre est
    oublié — l'indicateur de tension du moment. La persistance est
    l'affaire du rappel `on_event`, où le hub branche le journal
    (btcterm/journal.py) — et du chemin inverse, `restore`, par lequel
    le hub rend la fenêtre à un service qui redémarre.

    L'état de connexion est publié ici plutôt que dans un carnet — ce
    flux n'en alimente aucun — d'où la redéfinition des deux marqueurs.
    Il l'est par **lien** : le fil est lui-même le lien Binance, et les
    connecteurs secondaires (Bybit) déclarent le leur par `attach` et le
    tiennent à jour par `mark`. `connected` vaut dès qu'un lien tient,
    `error` rapporte le premier lien tombé.

    Un lien peut tenir sans rien livrer — c'est le cas de Binance depuis
    certains pays —, et rien dans l'état de connexion ne le dit. Le fil
    retient donc, par lien, l'heure du dernier événement reçu et celle
    de l'ouverture du lien : `silent` nomme ceux qui tiennent sans avoir
    rien livré depuis plus longtemps qu'un seuil.
    """

    name = "Liquidations"

    #: Nombre d'événements conservés. À raison de quelques dizaines par
    #: minute en marché agité, cela couvre le dernier quart d'heure.
    MAX_EVENTS = 500

    def __init__(self, maxlen: int = MAX_EVENTS, **kwargs):
        super().__init__(book=None, **kwargs)
        self.events: deque[Liquidation] = deque(maxlen=maxlen)
        #: État par lien : nom → (connecté, dernière erreur).
        self.links: dict[str, tuple[bool, Optional[str]]] = {
            "Binance": (False, None)}
        #: Heure du dernier événement reçu, par lien — nourrie par
        #: `record` comme par `restore` : un événement relu du journal
        #: dit aussi quand la plateforme a parlé pour la dernière fois.
        self.last_seen: dict[str, float] = {}
        #: Moment où chaque lien s'est ouvert, pour ceux qui tiennent :
        #: le silence d'un lien se compte depuis son ouverture quand il
        #: n'a encore rien livré, jamais depuis un événement d'avant.
        self.since: dict[str, float] = {}
        self._lock = threading.Lock()
        #: Rappel appelé à chaque événement retenu — même convention que
        #: les collectes de newsdb : c'est l'appelant qui décide quoi en
        #: faire (le hub y branche le journal). Une erreur du rappel ne
        #: doit jamais fermer le flux.
        self.on_event: Optional[Callable[[Liquidation], None]] = None

    # ── État de connexion ───────────────────────────────────

    def attach(self, name: str) -> None:
        """Déclare un lien secondaire, pas encore connecté."""
        self.links.setdefault(name, (False, None))

    def mark(self, name: str, connected: bool,
             error: Optional[str] = None) -> None:
        """Met à jour l'état d'un lien, et date son ouverture."""
        was_up = self.links.get(name, (False, None))[0]
        self.links[name] = (connected, None if connected else error)
        if connected and not was_up:
            self.since[name] = time.time()
        elif not connected:
            self.since.pop(name, None)

    @property
    def connected(self) -> bool:
        """Vrai dès qu'un lien tient."""
        return any(up for up, _ in self.links.values())

    @property
    def error(self) -> Optional[str]:
        """L'erreur du premier lien tombé, s'il y en a une."""
        for up, err in self.links.values():
            if not up and err:
                return err
        return None

    def missing(self) -> list[str]:
        """Les liens qui ne tiennent pas, dans l'ordre de déclaration."""
        return [name for name, (up, _) in self.links.items() if not up]

    def silent(self, threshold: float) -> list[tuple[str, float]]:
        """Les liens qui tiennent sans rien livrer depuis `threshold` secondes.

        Chaque entrée dit le lien et la durée de son silence, comptée
        depuis son dernier événement ou, s'il est plus récent, depuis
        l'ouverture du lien : un lien qui vient de se rouvrir n'est pas
        muet, même si son dernier événement date. Dans l'ordre de
        déclaration.
        """
        muets = []
        for name, (up, _) in self.links.items():
            if not up:
                continue
            age = self.last_event_age(name)
            if age is not None and age >= threshold:
                muets.append((name, age))
        return muets

    def _mark_connected(self) -> None:
        self.mark("Binance", True)

    def _mark_disconnected(self, exc: Exception) -> None:
        self.mark("Binance", False, str(exc)[:50])

    # ── Lecture ─────────────────────────────────────────────

    def latest(self, limit: int = 12) -> list[Liquidation]:
        """Les dernières liquidations, la plus récente d'abord."""
        with self._lock:
            return list(self.events)[-limit:][::-1]

    def totals(self, window: float = 3600) -> dict[str, float]:
        """Montants liquidés sur la fenêtre, par côté et pour le Bitcoin.

        `btc` ne compte que les paires dont le symbole commence par
        `BTC` : c'est ce qui distingue une cascade locale d'une cascade
        de marché.
        """
        limite = time.time() - window
        longs = shorts = btc = 0.0
        with self._lock:
            evenements = [e for e in self.events if e.time >= limite]

        for event in evenements:
            if event.side == "long":
                longs += event.notional
            else:
                shorts += event.notional
            if event.symbol.startswith("BTC"):
                btc += event.notional

        return {"long": longs, "short": shorts, "btc": btc,
                "count": len(evenements)}

    def last_event_age(self, link: Optional[str] = None) -> Optional[float]:
        """Secondes écoulées depuis la dernière liquidation reçue.

        Sans argument, pour le fil entier — la fenêtre d'événements,
        quelle qu'en soit la source. Avec un nom de lien, pour ce lien
        seul : depuis son dernier événement ou, s'il est plus récent,
        depuis son ouverture ; `None` tant qu'il n'a ni parlé ni ouvert.
        """
        now = time.time()
        if link is not None:
            repere = max(filter(None, (self.last_seen.get(link),
                                       self.since.get(link))), default=None)
            return None if repere is None else max(0.0, now - repere)
        with self._lock:
            if not self.events:
                return None
            return now - self.events[-1].time

    def _seen(self, event: Liquidation) -> None:
        """Date le dernier événement du lien dont il vient."""
        if event.time > self.last_seen.get(event.exchange, 0.0):
            self.last_seen[event.exchange] = event.time

    # ── Flux ────────────────────────────────────────────────

    async def _stream(self) -> None:
        async with self._connect(BINANCE_FUTURES_WS) as socket:
            self._mark_connected()
            async for raw in socket:
                message = json.loads(raw)
                # Binance documente un objet par événement, mais le nom
                # du flux (`@arr`) laisse attendre un tableau : les deux
                # formes sont acceptées plutôt que pariées.
                for event in message if isinstance(message, list) else [message]:
                    self._handle(event)

    def _handle(self, message: dict) -> None:
        order = message.get("o")
        if not order:
            return
        try:
            quantity = float(order.get("q", 0))
            # `ap` est le prix moyen d'exécution, `p` le prix limite de
            # l'ordre de liquidation : le premier est le vrai prix payé.
            price = float(order.get("ap") or order.get("p") or 0)
            event = Liquidation(
                time=float(order.get("T", message.get("E", 0))) / 1000,
                symbol=order.get("s", "?"),
                # Une vente forcée ferme un long, un achat forcé un short.
                side="long" if order.get("S") == "SELL" else "short",
                price=price,
                quantity=quantity,
            )
        except (TypeError, ValueError):
            return

        self.record(event)

    def record(self, event: Liquidation) -> None:
        """Retient un événement, d'où qu'il vienne, et le signale.

        C'est l'entrée commune des sources : le fil lui-même pour Binance,
        `BybitLiquidationConnector` pour Bybit. Un événement sans prix ou
        sans taille est ignoré.
        """
        if event.price <= 0 or event.quantity <= 0:
            return
        with self._lock:
            self.events.append(event)
            self._seen(event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                # Le journal peut échouer (disque plein, base verrouillée) ;
                # le fil, lui, doit continuer à nourrir le panneau.
                pass

    def restore(self, events: Iterable[Liquidation]) -> int:
        """Repeuple la fenêtre d'événements **déjà connus**, sans les resignaler.

        La fenêtre ne vit qu'en mémoire : redémarrer le service la vide,
        et le panneau repartait de zéro alors que le journal gardait les
        dernières heures sur disque — un panneau vide après un
        redémarrage se lit comme une panne, ce qu'il n'est pas.

        Contrairement à `record`, rien n'est signalé à `on_event` : ces
        événements viennent du journal, les réémettre les y écrirait une
        seconde fois. À appeler **avant** d'ouvrir les connexions, les
        événements devant rester en ordre chronologique dans la
        fenêtre ; les plus anciens sortent d'eux-mêmes si l'on en donne
        plus qu'elle n'en tient.
        """
        kept = 0
        with self._lock:
            for event in events:
                if event.price <= 0 or event.quantity <= 0:
                    continue
                self.events.append(event)
                self._seen(event)
                kept += 1
        return kept


class BybitLiquidationConnector(ExchangeConnector):
    """Seconde source du fil : le canal `allLiquidation` de Bybit.

    Un abonnement par paire, sur le WebSocket public des contrats
    linéaires ; chaque message porte un tableau d'événements. Le champ
    `S` y est le **côté de la position** liquidée — `Buy` pour un long,
    `Sell` pour un short —, à l'inverse de Binance qui donne le sens de
    l'ordre forcé. Les événements vont au magasin du fil (`record`), et
    l'état du lien au fil aussi (`mark`).
    """

    name = "Bybit"

    def __init__(self, feed: LiquidationFeed,
                 symbols: tuple[str, ...] = BYBIT_SYMBOLS, **kwargs):
        super().__init__(book=None, **kwargs)
        self.feed = feed
        self.feed.attach(self.name)
        self.subscription = {
            "op": "subscribe",
            "args": [f"allLiquidation.{symbol}" for symbol in symbols],
        }

    def _mark_connected(self) -> None:
        self.feed.mark(self.name, True)

    def _mark_disconnected(self, exc: Exception) -> None:
        self.feed.mark(self.name, False, str(exc)[:50])

    async def _stream(self) -> None:
        async with self._connect(BYBIT_LINEAR_WS) as socket:
            await socket.send(json.dumps(self.subscription))
            self._mark_connected()
            async for raw in socket:
                if not self._running:
                    break
                self._handle(json.loads(raw))

    def _handle(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        # Réponse à l'abonnement : un refus (paire inconnue…) vaut panne,
        # pour qu'il se lise dans le panneau plutôt que dans un silence.
        if "success" in message:
            if not message.get("success"):
                raise RuntimeError(message.get("ret_msg") or "abonnement refusé")
            return
        if not str(message.get("topic", "")).startswith("allLiquidation."):
            return
        for item in message.get("data") or []:
            try:
                event = Liquidation(
                    time=float(item.get("T", message.get("ts", 0))) / 1000,
                    symbol=item.get("s", "?"),
                    side="long" if item.get("S") == "Buy" else "short",
                    price=float(item.get("p") or 0),
                    quantity=float(item.get("v") or 0),
                    exchange=self.name,
                )
            except (TypeError, ValueError, AttributeError):
                continue
            self.feed.record(event)
