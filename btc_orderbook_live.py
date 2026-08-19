"""
Carnet d'ordres BTC en temps reel -- Binance / Coinbase / Kraken
==================================================================

Affiche, pour chaque plateforme, une courbe de profondeur de marche
(depth chart) remplie : vert pour les achats (bids), rouge pour les
ventes (asks), avec le prix courant et le spread affiches en titre.

La connexion aux plateformes et la normalisation des carnets sont
assurees par `btcterm.exchanges` ; ce fichier ne contient plus que le
rendu.

Installation :
    pip install websockets matplotlib

Lancement :
    python btc_orderbook_live.py

Fermer la fenetre du graphique pour arreter le script.
"""

import matplotlib.pyplot as plt
import matplotlib.animation as animation

from btcterm.exchanges import (
    BinanceConnector,
    CoinbaseConnector,
    KrakenConnector,
    OrderBook,
    run_connectors_in_thread,
)

# ----------------------------------------------------------------------------
# Plateformes suivies
# ----------------------------------------------------------------------------

LABELS = {"binance": "Binance", "coinbase": "Coinbase", "kraken": "Kraken"}

BID_COLOR = "#1ec98a"
ASK_COLOR = "#e2574c"
BG_COLOR = "#16161a"
PANEL_COLOR = "#1f1f24"
TEXT_COLOR = "#e8e6e0"
MUTED_COLOR = "#8a8880"


def build_books_and_connectors():
    """Un carnet et un connecteur par plateforme, sur la paire BTC/USD."""
    books = {name: OrderBook(exchange=LABELS[name]) for name in LABELS}
    connectors = [
        BinanceConnector(books["binance"], symbol="btcusdt", depth=20),
        CoinbaseConnector(books["coinbase"], product="BTC-USD"),
        KrakenConnector(books["kraken"], pair="XBT/USD", depth=100),
    ]
    return books, connectors


# ----------------------------------------------------------------------------
# Affichage matplotlib -- 3 panneaux (un par plateforme), style sombre,
# courbes de profondeur remplies + prix courant et spread en titre.
# ----------------------------------------------------------------------------

def main():
    books, connectors = build_books_and_connectors()
    run_connectors_in_thread(connectors)

    fig, axes = plt.subplots(1, 3, figsize=(14, 5.5), facecolor=BG_COLOR)
    fig.suptitle("Carnet d'ordres BTC/USD en temps reel", color=TEXT_COLOR, fontsize=14)

    def style_axis(ax):
        ax.set_facecolor(PANEL_COLOR)
        for spine in ax.spines.values():
            spine.set_color("#3a3a40")
        ax.tick_params(colors=MUTED_COLOR, labelsize=8)
        ax.grid(alpha=0.12, color=MUTED_COLOR)

    def update(frame):
        for ax, (name, book) in zip(axes, books.items()):
            ax.clear()
            style_axis(ax)

            best_bid, best_ask = book.best_bid, book.best_ask
            if best_bid is None or best_ask is None:
                ax.text(0.5, 0.5, "connexion en cours...", ha="center", va="center",
                         transform=ax.transAxes, color=MUTED_COLOR, fontsize=9)
                ax.set_title(LABELS[name], color=TEXT_COLOR, loc="left", fontsize=11)
                continue

            bid_prices, bid_cum = book.cumulative_depth("bids")
            ask_prices, ask_cum = book.cumulative_depth("asks")
            mid = (best_bid + best_ask) / 2
            spread = best_ask - best_bid

            ax.fill_between(bid_prices, 0, bid_cum, step="post",
                              color=BID_COLOR, alpha=0.25)
            ax.plot(bid_prices, bid_cum, drawstyle="steps-post",
                     color=BID_COLOR, linewidth=1.6)

            ax.fill_between(ask_prices, 0, ask_cum, step="post",
                              color=ASK_COLOR, alpha=0.25)
            ax.plot(ask_prices, ask_cum, drawstyle="steps-post",
                     color=ASK_COLOR, linewidth=1.6)

            ax.axvline(mid, color=TEXT_COLOR, linewidth=0.8, linestyle=":", alpha=0.6)

            ax.set_title(
                f"{LABELS[name]}\n${mid:,.2f}   spread {spread:.2f}$",
                color=TEXT_COLOR, loc="left", fontsize=11
            )
            ax.set_xlabel("Prix (USD)", color=MUTED_COLOR, fontsize=8)
            if name == "binance":
                ax.set_ylabel("Volume cumule (BTC)", color=MUTED_COLOR, fontsize=8)

    ani = animation.FuncAnimation(fig, update, interval=1000, cache_frame_data=False)
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    plt.show()


if __name__ == "__main__":
    main()
