"""
Fil des liquidations forcées — Binance Futures.

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
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

from .exchanges import ExchangeConnector

__all__ = ["Liquidation", "LiquidationFeed"]

BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws/!forceOrder@arr"


@dataclass(frozen=True)
class Liquidation:
    """Une position fermée de force."""

    time: float
    symbol: str
    side: str          #: `long` ou `short` — la position qui a sauté
    price: float
    quantity: float

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
    (btcterm/journal.py).

    L'état de connexion est publié ici plutôt que dans un carnet — ce
    flux n'en alimente aucun — d'où la redéfinition des deux marqueurs.
    """

    name = "Liquidations"

    #: Nombre d'événements conservés. À raison de quelques dizaines par
    #: minute en marché agité, cela couvre le dernier quart d'heure.
    MAX_EVENTS = 500

    def __init__(self, maxlen: int = MAX_EVENTS, **kwargs):
        super().__init__(book=None, **kwargs)
        self.events: deque[Liquidation] = deque(maxlen=maxlen)
        self.connected = False
        self.error: Optional[str] = None
        self._lock = threading.Lock()
        #: Rappel appelé à chaque événement retenu — même convention que
        #: les collectes de newsdb : c'est l'appelant qui décide quoi en
        #: faire (le hub y branche le journal). Une erreur du rappel ne
        #: doit jamais fermer le flux.
        self.on_event: Optional[Callable[[Liquidation], None]] = None

    # ── État de connexion ───────────────────────────────────

    def _mark_connected(self) -> None:
        self.connected = True
        self.error = None

    def _mark_disconnected(self, exc: Exception) -> None:
        self.connected = False
        self.error = str(exc)[:50]

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

    def last_event_age(self) -> Optional[float]:
        """Secondes écoulées depuis la dernière liquidation reçue."""
        with self._lock:
            if not self.events:
                return None
            return time.time() - self.events[-1].time

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

        if event.price <= 0 or event.quantity <= 0:
            return
        with self._lock:
            self.events.append(event)
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                # Le journal peut échouer (disque plein, base verrouillée) ;
                # le fil, lui, doit continuer à nourrir le panneau.
                pass
