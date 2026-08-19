"""
BTC Real-Time Order Book & Arbitrage Monitor
============================================
Surveille les order books de plusieurs exchanges en temps réel
et détecte les opportunités d'arbitrage BTC/USDT.

Exchanges supportés : Binance, Kraken, Coinbase, Bybit, OKX

Les connexions WebSocket et la normalisation des carnets viennent de
`btcterm.exchanges` ; ce fichier ne contient que le moteur d'arbitrage
et son affichage.
"""

import asyncio
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# Le socle vit à la racine du dépôt, un niveau au-dessus de ce fichier.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.exchanges import (  # noqa: E402
    BinanceConnector,
    BybitConnector,
    CoinbaseAdvancedConnector,
    KrakenConnector,
    OKXConnector,
    OrderBook,
)

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
#  MODÈLE D'OPPORTUNITÉ
# ─────────────────────────────────────────────

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
#  CONNECTEURS
# ─────────────────────────────────────────────

def build_connectors(order_books: dict[str, OrderBook]) -> dict:
    """Un connecteur par plateforme, tous sur la paire BTC/USDT.

    Coinbase passe par le flux Advanced Trade : son flux public
    historique ne cote pas l'USDT.
    """
    return {
        "Binance":  BinanceConnector(order_books["Binance"], symbol="btcusdt", depth=20),
        "Kraken":   KrakenConnector(order_books["Kraken"], pair="XBT/USDT", depth=25),
        "Bybit":    BybitConnector(order_books["Bybit"], symbol="BTCUSDT", depth=50),
        "OKX":      OKXConnector(order_books["OKX"], inst_id="BTC-USDT"),
        "Coinbase": CoinbaseAdvancedConnector(order_books["Coinbase"], product="BTC-USDT"),
    }


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

        bids = ob.top("bids", ORDER_BOOK_DEPTH)
        asks = ob.top("asks", ORDER_BOOK_DEPTH)
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
    connectors = build_connectors(order_books)

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
