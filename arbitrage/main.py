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
from datetime import datetime
from pathlib import Path

# Le socle vit à la racine du dépôt, un niveau au-dessus de ce fichier.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.arbitrage import DEFAULT_FEES, ArbitrageEngine  # noqa: E402
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

# Les frais viennent du socle ; seule la couleur d'affichage est propre
# à cette interface.
COLORS = {
    "Binance": "yellow", "Kraken": "blue", "Bybit": "magenta",
    "OKX": "cyan", "Coinbase": "green",
}
EXCHANGES = {
    name: {"fee": DEFAULT_FEES[name], "color": COLORS[name]} for name in COLORS
}

MIN_PROFIT_PCT = 0.1   # % minimum pour afficher une opportunité
ORDER_BOOK_DEPTH = 8   # niveaux affichés dans l'order book

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
    engine = ArbitrageEngine(order_books, min_profit_pct=MIN_PROFIT_PCT)
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
