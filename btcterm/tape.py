"""
Le ruban des échanges : ce qui s'exécute, et qui l'a voulu.

Le carnet montre des intentions — des ordres qui attendent et peuvent
disparaître. Le ruban (le « time & sales » des terminaux de marché) montre
ce qui s'est réellement exécuté, et surtout **qui a agressé** : un achat
au prix vendeur est un acheteur pressé, une vente au prix acheteur un
vendeur pressé. Les gros ordres agressifs disent où l'argent se déplace
maintenant ; la somme des agressions de chaque côté, minute par minute,
dit qui mène.

Trois places au comptant, chacune son flux WebSocket public, sans clé :
Binance (`btcusdt@aggTrade`), Coinbase (`market_trades`, BTC-USD) et
Bybit (`publicTrade.BTCUSDT`). Le sens du preneur ne se lit pas pareil
partout — mesuré le 6 novembre 2026 contre le meilleur bid/ask du même
instant, plutôt que supposé :

- Binance : `m` vrai, l'acheteur était teneur — une **vente** agressive
  (158 échanges au bid contre 30 à l'ask) ;
- Bybit : `S` est le côté du preneur (530 achats à l'ask contre 9 au bid) ;
- Coinbase : `side` est le côté du **teneur** — un « BUY » tombe au bid
  (180 contre 23 à l'ask) —, le preneur est donc de l'autre côté.

Le ruban garde une heure de gros ordres (`RECORD`, 100 000 $ et plus, le
plus petit seuil du panneau) et, pour toutes les tailles, la somme achetée
et vendue par minute, et la quantité échangée par place — de quoi tirer
un prix moyen pondéré, et la **prime Coinbase** : l'écart du bitcoin en
dollars sur Coinbase, la place des acheteurs américains, à celui de
Binance en USDT. Chaque gros ordre part au journal (`on_large`), qui le
rend au démarrage (`restore`). Le ruban tient aussi l'état de chaque lien,
comme le fil des liquidations.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Optional

from .exchanges import ExchangeConnector

__all__ = ["Trade", "TradeTape", "BinanceTradeConnector",
           "BybitTradeConnector", "CoinbaseTradeConnector", "RECORD"]

BINANCE_TRADES_WS = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"
BYBIT_SPOT_WS = "wss://stream.bybit.com/v5/public/spot"

#: En deçà, un échange compte dans le flux mais ne s'inscrit pas au ruban —
#: le plus petit seuil du panneau : à 50 000 $, le ruban gardait des ordres
#: qu'aucun seuil ne montrait.
RECORD = 100_000.0
#: Profondeur du ruban et du flux par minute.
KEEP = 3600.0


@dataclass(frozen=True)
class Trade:
    """Un échange exécuté ; `side` est le côté du preneur, celui qui a agressé."""

    time: float
    exchange: str
    side: str          #: `buy` ou `sell`
    price: float
    quantity: float

    @property
    def notional(self) -> float:
        return self.price * self.quantity


class TradeTape:
    """Les gros ordres de l'heure, le flux agressif par minute, l'état des liens."""

    def __init__(self, record: float = RECORD, keep: float = KEEP):
        self.record_min = record
        self.keep = keep
        self._lock = threading.Lock()
        self._large: deque[Trade] = deque(maxlen=5000)
        #: Appelé hors verrou pour chaque gros ordre vivant : le hub y branche
        #: le journal. Une exception n'y arrête jamais le flux.
        self.on_large: Optional[Callable[[Trade], None]] = None
        #: Minute (epoch // 60) → plateforme → {"buy": $, "sell": $,
        #: "qty": BTC échangés}.
        self._minutes: dict[int, dict[str, dict[str, float]]] = {}
        #: Plateforme → (connecté, erreur).
        self.links: dict[str, tuple[bool, Optional[str]]] = {}
        self._last: dict[str, float] = {}
        self._since: dict[str, float] = {}

    # ── Liens ───────────────────────────────────────────────

    def attach(self, name: str) -> None:
        self.links.setdefault(name, (False, None))

    def mark(self, name: str, connected: bool,
             error: Optional[str] = None) -> None:
        was_up = self.links.get(name, (False, None))[0]
        self.links[name] = (connected, None if connected else error)
        if connected and not was_up:
            self._since[name] = time.time()

    def last_event_age(self, name: str) -> Optional[float]:
        """Secondes depuis le dernier échange reçu de ce lien, ou depuis son
        ouverture si elle est plus récente."""
        moments = [m for m in (self._last.get(name), self._since.get(name))
                   if m is not None]
        return time.time() - max(moments) if moments else None

    # ── Échanges ────────────────────────────────────────────

    def record(self, trade: Trade) -> None:
        if trade.price <= 0 or trade.quantity <= 0:
            return
        minute = int(trade.time // 60)
        large = trade.notional >= self.record_min
        with self._lock:
            sides = (self._minutes.setdefault(minute, {})
                     .setdefault(trade.exchange,
                                 {"buy": 0.0, "sell": 0.0, "qty": 0.0}))
            sides[trade.side] += trade.notional
            sides["qty"] += trade.quantity
            if large:
                self._large.append(trade)
            self._last[trade.exchange] = time.time()
            horizon = int((trade.time - self.keep) // 60)
            for old in [m for m in self._minutes if m < horizon]:
                del self._minutes[old]
        if large and self.on_large is not None:
            try:
                self.on_large(trade)
            except Exception:
                pass

    def restore(self, trades: Iterable[Trade]) -> int:
        """Rend au ruban des gros ordres **déjà journalisés**, sans les
        réécrire : un redémarrage du service effaçait l'heure en mémoire.
        À appeler avant d'ouvrir les connexions, dans l'ordre chronologique.
        Le flux par minute, lui, ne se reconstruit pas — il compte toutes
        les tailles, et le journal ne garde que les grosses."""
        kept = 0
        with self._lock:
            for trade in trades:
                if trade.price > 0 and trade.quantity > 0:
                    self._large.append(trade)
                    kept += 1
        return kept

    def vwap(self, exchange: str, seconds: float,
             now: Optional[float] = None) -> Optional[float]:
        """Prix moyen pondéré par les volumes d'une place sur la fenêtre."""
        now = time.time() if now is None else now
        start = int((now - seconds) // 60)
        notional = quantity = 0.0
        with self._lock:
            for minute, exchanges in self._minutes.items():
                sides = exchanges.get(exchange)
                if minute >= start and sides:
                    notional += sides["buy"] + sides["sell"]
                    quantity += sides["qty"]
        return notional / quantity if quantity > 0 else None

    def coinbase_premium(self, seconds: float = 300,
                         now: Optional[float] = None) -> Optional[float]:
        """La prime Coinbase, en fraction : Coinbase (BTC-USD) contre
        Binance (BTC-USDT), prix moyens pondérés de la fenêtre.

        Positive, l'Amérique paie plus cher que le reste du monde — la
        lecture usuelle d'une demande américaine. Les deux places ne cotent
        pas la même monnaie : un USDT décoté de 0,05 % sur le dollar fait
        +0,05 % de prime à lui seul, d'où un écart qui ne se lit qu'au-delà
        de quelques centièmes."""
        coinbase = self.vwap("Coinbase", seconds, now)
        binance = self.vwap("Binance", seconds, now)
        if coinbase is None or binance is None:
            return None
        return coinbase / binance - 1

    def large(self, min_notional: float, limit: int = 30,
              now: Optional[float] = None) -> list[Trade]:
        """Les gros ordres de l'heure, du plus récent au plus ancien."""
        now = time.time() if now is None else now
        with self._lock:
            trades = [t for t in self._large
                      if t.notional >= min_notional and now - t.time <= self.keep]
        return sorted(trades, key=lambda t: t.time, reverse=True)[:limit]

    def flow(self, seconds: float, now: Optional[float] = None) -> dict:
        """Achats et ventes agressifs des `seconds` dernières secondes, par
        minute entière : `buy`, `sell` et, par plateforme, `by_exchange`."""
        now = time.time() if now is None else now
        start = int((now - seconds) // 60)
        total = {"buy": 0.0, "sell": 0.0, "by_exchange": {}}
        with self._lock:
            for minute, exchanges in self._minutes.items():
                if minute < start:
                    continue
                for name, sides in exchanges.items():
                    mine = total["by_exchange"].setdefault(
                        name, {"buy": 0.0, "sell": 0.0})
                    for side in ("buy", "sell"):
                        total[side] += sides[side]
                        mine[side] += sides[side]
        return total


# ─────────────────────────────────────────────────────────────
# Connecteurs
# ─────────────────────────────────────────────────────────────

class _TapeConnector(ExchangeConnector):
    """Un flux d'échanges : pas de carnet, l'état du lien va au ruban."""

    def __init__(self, tape: TradeTape, **kwargs):
        super().__init__(book=None, **kwargs)
        self.tape = tape
        self.tape.attach(self.name)

    def _mark_connected(self) -> None:
        self.tape.mark(self.name, True)

    def _mark_disconnected(self, exc: Exception) -> None:
        self.tape.mark(self.name, False, str(exc)[:50])


class BinanceTradeConnector(_TapeConnector):
    """Binance au comptant, échanges agrégés : `m` vrai, vente agressive."""

    name = "Binance"

    async def _stream(self) -> None:
        async with self._connect(BINANCE_TRADES_WS) as socket:
            self._mark_connected()
            async for raw in socket:
                if not self._running:
                    break
                self.handle(json.loads(raw))

    def handle(self, message: dict) -> None:
        try:
            self.tape.record(Trade(
                time=float(message["T"]) / 1000, exchange=self.name,
                side="sell" if message["m"] else "buy",
                price=float(message["p"]), quantity=float(message["q"])))
        except (KeyError, TypeError, ValueError):
            pass


class BybitTradeConnector(_TapeConnector):
    """Bybit au comptant : `S` est le côté du preneur."""

    name = "Bybit"

    async def _stream(self) -> None:
        async with self._connect(BYBIT_SPOT_WS) as socket:
            await socket.send(json.dumps({"op": "subscribe",
                                          "args": ["publicTrade.BTCUSDT"]}))
            self._mark_connected()
            async for raw in socket:
                if not self._running:
                    break
                self.handle(json.loads(raw))

    def handle(self, message: dict) -> None:
        if "success" in message and not message.get("success"):
            raise RuntimeError(message.get("ret_msg") or "abonnement refusé")
        if not str(message.get("topic", "")).startswith("publicTrade."):
            return
        for item in message.get("data") or []:
            try:
                self.tape.record(Trade(
                    time=float(item["T"]) / 1000, exchange=self.name,
                    side="buy" if item["S"] == "Buy" else "sell",
                    price=float(item["p"]), quantity=float(item["v"])))
            except (KeyError, TypeError, ValueError):
                continue


class CoinbaseTradeConnector(_TapeConnector):
    """Coinbase Advanced, BTC-USD : `side` est celui du **teneur** — le
    preneur est de l'autre côté. L'instantané d'abonnement, qui rejoue les
    derniers échanges, est écarté : le ruban ne dit que ce qui arrive."""

    name = "Coinbase"

    async def _stream(self) -> None:
        async with self._connect(COINBASE_WS) as socket:
            await socket.send(json.dumps({"type": "subscribe",
                                          "product_ids": ["BTC-USD"],
                                          "channel": "market_trades"}))
            self._mark_connected()
            async for raw in socket:
                if not self._running:
                    break
                self.handle(json.loads(raw))

    def handle(self, message: dict) -> None:
        if message.get("type") == "error":
            raise RuntimeError(message.get("message") or "abonnement refusé")
        if message.get("channel") != "market_trades":
            return
        for event in message.get("events") or []:
            if event.get("type") != "update":
                continue
            for item in event.get("trades") or []:
                try:
                    moment = datetime.fromisoformat(
                        item["time"].replace("Z", "+00:00")).timestamp()
                    self.tape.record(Trade(
                        time=moment, exchange=self.name,
                        side="sell" if item["side"] == "BUY" else "buy",
                        price=float(item["price"]), quantity=float(item["size"])))
                except (KeyError, TypeError, ValueError):
                    continue
