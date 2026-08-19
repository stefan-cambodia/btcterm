"""
BTC Real-Time Order Book & Arbitrage Monitor
============================================
Surveille les order books de plusieurs exchanges en temps réel
et détecte les opportunités d'arbitrage BTC/USDT.

Exchanges supportés : Binance, Kraken, Coinbase, Bybit, OKX
"""

import asyncio
import json
import time
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import websockets
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────

EXCHANGES = {
    "Binance":  {"fee": 0.001, "color": "yellow"},
    "Kraken":   {"fee": 0.0026, "color": "blue"},
    "Bybit":    {"fee": 0.001, "color": "magenta"},
    "OKX":      {"fee": 0.001, "color": "cyan"},
    "Coinbase": {"fee": 0.006, "color": "green"},
}

MIN_PROFIT_PCT = 0.1   # % minimum pour afficher une opportunité
ORDER_BOOK_DEPTH = 8   # niveaux affichés dans l'order book

# ─────────────────────────────────────────────
#  MODÈLES DE DONNÉES
# ─────────────────────────────────────────────

@dataclass
class OrderBook:
    exchange: str
    bids: list[tuple[float, float]] = field(default_factory=list)
    asks: list[tuple[float, float]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    connected: bool = False
    error: Optional[str] = None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_pct(self) -> Optional[float]:
        if self.best_bid and self.best_ask and self.best_bid > 0:
            return (self.best_ask - self.best_bid) / self.best_bid * 100
        return None

    @property
    def age_ms(self) -> float:
        return (time.time() - self.timestamp) * 1000


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
    timestamp: float = field(default_factory=time.time)

    @property
    def is_profitable(self) -> bool:
        return self.net_profit_pct > MIN_PROFIT_PCT


# ─────────────────────────────────────────────
#  CONNECTEURS WEBSOCKET
# ─────────────────────────────────────────────

class ExchangeConnector:
    """Classe de base pour les connecteurs WebSocket."""

    def __init__(self, order_books: dict[str, OrderBook]):
        self.order_books = order_books
        self._running = True

    def stop(self):
        self._running = False

    async def connect_with_retry(self, name: str, coro_factory, max_retries=10):
        retries = 0
        while self._running and retries < max_retries:
            try:
                await coro_factory()
                retries = 0
            except Exception as e:
                self.order_books[name].connected = False
                self.order_books[name].error = str(e)[:50]
                retries += 1
                await asyncio.sleep(min(2 ** retries, 30))


class BinanceConnector(ExchangeConnector):
    URL = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"

    async def run(self):
        await self.connect_with_retry("Binance", self._stream)

    async def _stream(self):
        async with websockets.connect(self.URL, ping_interval=20) as ws:
            self.order_books["Binance"].connected = True
            self.order_books["Binance"].error = None
            async for msg in ws:
                if not self._running:
                    break
                data = json.loads(msg)
                ob = self.order_books["Binance"]
                ob.bids = [(float(p), float(q)) for p, q in data["bids"][:ORDER_BOOK_DEPTH]]
                ob.asks = [(float(p), float(q)) for p, q in data["asks"][:ORDER_BOOK_DEPTH]]
                ob.timestamp = time.time()


class KrakenConnector(ExchangeConnector):
    URL = "wss://ws.kraken.com"

    async def run(self):
        await self.connect_with_retry("Kraken", self._stream)

    async def _stream(self):
        async with websockets.connect(self.URL, ping_interval=20) as ws:
            sub = {"event": "subscribe", "pair": ["XBT/USDT"], "subscription": {"name": "book", "depth": 25}}
            await ws.send(json.dumps(sub))
            self.order_books["Kraken"].connected = True
            self.order_books["Kraken"].error = None

            bids_map: dict[str, str] = {}
            asks_map: dict[str, str] = {}

            async for msg in ws:
                if not self._running:
                    break
                data = json.loads(msg)
                if not isinstance(data, list):
                    continue

                payload = data[1] if len(data) > 1 else {}

                # Snapshot initial
                if "bs" in payload:
                    bids_map = {p: q for p, q, *_ in payload["bs"]}
                if "as" in payload:
                    asks_map = {p: q for p, q, *_ in payload["as"]}

                # Mises à jour delta
                if "b" in payload:
                    for entry in payload["b"]:
                        p, q = entry[0], entry[1]
                        if float(q) == 0:
                            bids_map.pop(p, None)
                        else:
                            bids_map[p] = q
                if "a" in payload:
                    for entry in payload["a"]:
                        p, q = entry[0], entry[1]
                        if float(q) == 0:
                            asks_map.pop(p, None)
                        else:
                            asks_map[p] = q

                ob = self.order_books["Kraken"]
                ob.bids = sorted([(float(p), float(q)) for p, q in bids_map.items()], reverse=True)[:ORDER_BOOK_DEPTH]
                ob.asks = sorted([(float(p), float(q)) for p, q in asks_map.items()])[:ORDER_BOOK_DEPTH]
                ob.timestamp = time.time()


class BybitConnector(ExchangeConnector):
    URL = "wss://stream.bybit.com/v5/public/spot"

    async def run(self):
        await self.connect_with_retry("Bybit", self._stream)

    async def _stream(self):
        async with websockets.connect(self.URL, ping_interval=20) as ws:
            sub = {"op": "subscribe", "args": [f"orderbook.{ORDER_BOOK_DEPTH*2}.BTCUSDT"]}
            await ws.send(json.dumps(sub))
            self.order_books["Bybit"].connected = True
            self.order_books["Bybit"].error = None

            async for msg in ws:
                if not self._running:
                    break
                data = json.loads(msg)
                if data.get("topic", "").startswith("orderbook"):
                    d = data.get("data", {})
                    ob = self.order_books["Bybit"]
                    if data.get("type") == "snapshot":
                        ob.bids = [(float(p), float(q)) for p, q in d.get("b", [])[:ORDER_BOOK_DEPTH]]
                        ob.asks = [(float(p), float(q)) for p, q in d.get("a", [])[:ORDER_BOOK_DEPTH]]
                    ob.timestamp = time.time()


class OKXConnector(ExchangeConnector):
    URL = "wss://ws.okx.com:8443/ws/v5/public"

    async def run(self):
        await self.connect_with_retry("OKX", self._stream)

    async def _stream(self):
        async with websockets.connect(self.URL, ping_interval=20) as ws:
            sub = {"op": "subscribe", "args": [{"channel": "books5", "instId": "BTC-USDT"}]}
            await ws.send(json.dumps(sub))
            self.order_books["OKX"].connected = True
            self.order_books["OKX"].error = None

            async for msg in ws:
                if not self._running:
                    break
                data = json.loads(msg)
                for item in data.get("data", []):
                    ob = self.order_books["OKX"]
                    ob.bids = [(float(p), float(q)) for p, q, *_ in item.get("bids", [])[:ORDER_BOOK_DEPTH]]
                    ob.asks = [(float(p), float(q)) for p, q, *_ in item.get("asks", [])[:ORDER_BOOK_DEPTH]]
                    ob.timestamp = time.time()


class CoinbaseConnector(ExchangeConnector):
    URL = "wss://advanced-trade-ws.coinbase.com"

    async def run(self):
        await self.connect_with_retry("Coinbase", self._stream)

    async def _stream(self):
        async with websockets.connect(self.URL, ping_interval=20) as ws:
            sub = {"type": "subscribe", "product_ids": ["BTC-USDT"], "channel": "level2"}
            await ws.send(json.dumps(sub))
            self.order_books["Coinbase"].connected = True
            self.order_books["Coinbase"].error = None

            bids_map: dict[float, float] = {}
            asks_map: dict[float, float] = {}

            async for msg in ws:
                if not self._running:
                    break
                data = json.loads(msg)
                events = data.get("events", [])
                for event in events:
                    for update in event.get("updates", []):
                        price = float(update["price_level"])
                        qty = float(update["new_quantity"])
                        side = update["side"]
                        if side == "bid":
                            if qty == 0:
                                bids_map.pop(price, None)
                            else:
                                bids_map[price] = qty
                        else:
                            if qty == 0:
                                asks_map.pop(price, None)
                            else:
                                asks_map[price] = qty

                ob = self.order_books["Coinbase"]
                ob.bids = sorted(bids_map.items(), reverse=True)[:ORDER_BOOK_DEPTH]
                ob.asks = sorted(asks_map.items())[:ORDER_BOOK_DEPTH]
                ob.timestamp = time.time()


# ─────────────────────────────────────────────
#  MOTEUR D'ARBITRAGE
# ─────────────────────────────────────────────

class ArbitrageEngine:
    def __init__(self, order_books: dict[str, OrderBook]):
        self.order_books = order_books
        self.opportunities: list[ArbitrageOpportunity] = []
        self.history: list[ArbitrageOpportunity] = []
        self.stats = defaultdict(int)

    def scan(self) -> list[ArbitrageOpportunity]:
        opportunities = []
        exchanges = list(self.order_books.keys())

        for i, buy_ex in enumerate(exchanges):
            for sell_ex in exchanges:
                if buy_ex == sell_ex:
                    continue
                buy_ob = self.order_books[buy_ex]
                sell_ob = self.order_books[sell_ex]

                if not (buy_ob.connected and sell_ob.connected):
                    continue
                if not (buy_ob.best_ask and sell_ob.best_bid):
                    continue
                if buy_ob.age_ms > 5000 or sell_ob.age_ms > 5000:
                    continue  # données trop vieilles

                buy_price = buy_ob.best_ask
                sell_price = sell_ob.best_bid

                if sell_price <= buy_price:
                    continue

                gross_pct = (sell_price - buy_price) / buy_price * 100
                buy_fee = EXCHANGES[buy_ex]["fee"] * 100
                sell_fee = EXCHANGES[sell_ex]["fee"] * 100
                net_pct = gross_pct - buy_fee - sell_fee

                opp = ArbitrageOpportunity(
                    buy_exchange=buy_ex,
                    sell_exchange=sell_ex,
                    buy_price=buy_price,
                    sell_price=sell_price,
                    gross_profit_pct=gross_pct,
                    net_profit_pct=net_pct,
                    buy_fee=buy_fee,
                    sell_fee=sell_fee,
                )

                if opp.is_profitable:
                    self.stats["total_opportunities"] += 1
                    self.history.append(opp)
                    if len(self.history) > 100:
                        self.history.pop(0)

                opportunities.append(opp)

        opportunities.sort(key=lambda x: x.net_profit_pct, reverse=True)
        self.opportunities = opportunities
        return opportunities


# ─────────────────────────────────────────────
#  INTERFACE RICH (AFFICHAGE TERMINAL)
# ─────────────────────────────────────────────

class Dashboard:
    def __init__(self, order_books: dict[str, OrderBook], engine: ArbitrageEngine):
        self.order_books = order_books
        self.engine = engine
        self.start_time = time.time()

    def _header(self) -> Panel:
        uptime = int(time.time() - self.start_time)
        h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
        connected = sum(1 for ob in self.order_books.values() if ob.connected)
        opp_count = self.engine.stats["total_opportunities"]
        now = datetime.now().strftime("%H:%M:%S")

        txt = Text()
        txt.append("⚡ BTC ARBITRAGE MONITOR ", style="bold white")
        txt.append(f"  {now}  ", style="dim")
        txt.append(f"🟢 {connected}/{len(self.order_books)} exchanges  ", style="green")
        txt.append(f"⏱ {h:02d}:{m:02d}:{s:02d}  ", style="cyan")
        txt.append(f"💡 {opp_count} opportunités détectées", style="yellow")
        return Panel(txt, style="bold", box=box.HEAVY)

    def _order_book_table(self, exchange: str) -> Panel:
        ob = self.order_books[exchange]
        cfg = EXCHANGES[exchange]
        color = cfg["color"]

        t = Table(box=box.SIMPLE, show_header=True, header_style=f"bold {color}", min_width=32)
        t.add_column("Prix BID", style="green", justify="right")
        t.add_column("Qté", justify="right", style="dim")
        t.add_column("Prix ASK", style="red", justify="right")
        t.add_column("Qté", justify="right", style="dim")

        bids = ob.bids[:ORDER_BOOK_DEPTH]
        asks = ob.asks[:ORDER_BOOK_DEPTH]
        max_rows = max(len(bids), len(asks))

        for i in range(max_rows):
            bid_price = f"{bids[i][0]:,.2f}" if i < len(bids) else ""
            bid_qty = f"{bids[i][1]:.4f}" if i < len(bids) else ""
            ask_price = f"{asks[i][0]:,.2f}" if i < len(asks) else ""
            ask_qty = f"{asks[i][1]:.4f}" if i < len(asks) else ""
            t.add_row(bid_price, bid_qty, ask_price, ask_qty)

        # Status
        if ob.connected:
            age = f"{ob.age_ms:.0f}ms"
            spread_txt = f"Spread: {ob.spread_pct:.4f}%" if ob.spread_pct else "—"
            status = f"[green]●[/green] {age}  {spread_txt}"
        elif ob.error:
            status = f"[red]✗ {ob.error[:35]}[/red]"
        else:
            status = "[yellow]⟳ Connexion...[/yellow]"

        title = f"[{color}]{exchange}[/{color}]  {status}"
        return Panel(t, title=title, box=box.ROUNDED, border_style=color if ob.connected else "red")

    def _arbitrage_table(self) -> Panel:
        opportunities = self.engine.opportunities

        t = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold white",
            min_width=80,
        )
        t.add_column("Acheter sur", style="cyan", min_width=10)
        t.add_column("Prix Achat", style="green", justify="right")
        t.add_column("Vendre sur", style="magenta", min_width=10)
        t.add_column("Prix Vente", style="red", justify="right")
        t.add_column("Brut %", justify="right")
        t.add_column("Net %", justify="right", min_width=8)
        t.add_column("Signal", justify="center")

        if not opportunities:
            t.add_row("—", "—", "—", "—", "—", "—", "[dim]Scan en cours...[/dim]")
        else:
            for opp in opportunities[:12]:
                net_style = "bold green" if opp.net_profit_pct > 0 else "red"
                signal = "🔥 GO" if opp.net_profit_pct > MIN_PROFIT_PCT else ("⚠ Faible" if opp.net_profit_pct > 0 else "✗")
                t.add_row(
                    opp.buy_exchange,
                    f"{opp.buy_price:,.2f}",
                    opp.sell_exchange,
                    f"{opp.sell_price:,.2f}",
                    f"{opp.gross_profit_pct:.4f}%",
                    f"[{net_style}]{opp.net_profit_pct:.4f}%[/{net_style}]",
                    signal,
                )

        history_count = len(self.engine.history)
        title = f"[bold white]⚡ Opportunités d'Arbitrage BTC/USDT[/bold white]  [dim](seuil: >{MIN_PROFIT_PCT}%  •  historique: {history_count})[/dim]"
        return Panel(t, title=title, box=box.HEAVY, border_style="yellow")

    def _history_panel(self) -> Panel:
        hist = self.engine.history[-5:][::-1]
        t = Table(box=box.SIMPLE, show_header=False, min_width=50)
        t.add_column("info")
        if not hist:
            t.add_row("[dim]Aucune opportunité profitable encore...[/dim]")
        for opp in hist:
            ts = datetime.fromtimestamp(opp.timestamp).strftime("%H:%M:%S")
            t.add_row(
                f"[dim]{ts}[/dim] [cyan]{opp.buy_exchange}[/cyan]→[magenta]{opp.sell_exchange}[/magenta] "
                f"[bold green]+{opp.net_profit_pct:.4f}%[/bold green]"
            )
        return Panel(t, title="[bold]📋 Historique récent[/bold]", box=box.ROUNDED, border_style="dim")

    def render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self._header(), size=3),
            Layout(name="main"),
            Layout(self._arbitrage_table(), size=16),
            Layout(self._history_panel(), size=8),
        )

        ob_panels = [self._order_book_table(ex) for ex in self.order_books]
        layout["main"].split_row(*[Layout(p) for p in ob_panels])

        return layout


# ─────────────────────────────────────────────
#  ORCHESTRATEUR PRINCIPAL
# ─────────────────────────────────────────────

async def main():
    console.print(Panel.fit(
        "[bold yellow]⚡ BTC ARBITRAGE MONITOR[/bold yellow]\n"
        "[dim]Connexion aux exchanges en cours...[/dim]",
        box=box.HEAVY
    ))
    await asyncio.sleep(1)

    # Initialisation des order books
    order_books = {name: OrderBook(exchange=name) for name in EXCHANGES}
    engine = ArbitrageEngine(order_books)
    dashboard = Dashboard(order_books, engine)

    # Connecteurs
    connectors = {
        "Binance":  BinanceConnector(order_books),
        "Kraken":   KrakenConnector(order_books),
        "Bybit":    BybitConnector(order_books),
        "OKX":      OKXConnector(order_books),
        "Coinbase": CoinbaseConnector(order_books),
    }

    async def scan_loop():
        while True:
            engine.scan()
            await asyncio.sleep(0.2)

    async def display_loop():
        with Live(dashboard.render(), refresh_per_second=4, screen=True) as live:
            while True:
                live.update(dashboard.render())
                await asyncio.sleep(0.25)

    # Lancement de toutes les tâches
    tasks = [asyncio.create_task(c.run()) for c in connectors.values()]
    tasks.append(asyncio.create_task(scan_loop()))
    tasks.append(asyncio.create_task(display_loop()))

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow]Arrêt du monitor...[/yellow]")
        for c in connectors.values():
            c.stop()
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAu revoir !")
