"""
Carnet d'ordres BTC en temps reel -- Binance / Coinbase / Kraken
==================================================================

Affiche, pour chaque plateforme, une courbe de profondeur de marche
(depth chart) remplie : vert pour les achats (bids), rouge pour les
ventes (asks), avec le prix courant et le spread affiches en titre.

Installation :
    pip install websockets matplotlib

Lancement :
    python btc_orderbook_live.py

Fermer la fenetre du graphique pour arreter le script.
"""

import asyncio
import json
import threading

import matplotlib.pyplot as plt
import matplotlib.animation as animation
import websockets

# ----------------------------------------------------------------------------
# Etat partage : un carnet d'ordres par plateforme
# ----------------------------------------------------------------------------

LOCK = threading.Lock()

BOOKS = {
    "binance": {"bids": {}, "asks": {}},
    "coinbase": {"bids": {}, "asks": {}},
    "kraken": {"bids": {}, "asks": {}},
}

LABELS = {"binance": "Binance", "coinbase": "Coinbase", "kraken": "Kraken"}

# Nombre de niveaux de prix conserves de chaque cote du carnet, autour du
# prix courant. Evite que Coinbase (qui renvoie un carnet complet, parfois
# des milliers de lignes) ne fasse exploser la memoire et le temps de calcul.
MAX_LEVELS = 100

# Limite de taille des messages websocket. Le snapshot complet de Coinbase
# depasse souvent la limite par defaut de la librairie (1 Mo) -> on l'augmente.
MAX_WS_SIZE = 20 * 1024 * 1024  # 20 Mo

BID_COLOR = "#1ec98a"
ASK_COLOR = "#e2574c"
BG_COLOR = "#16161a"
PANEL_COLOR = "#1f1f24"
TEXT_COLOR = "#e8e6e0"
MUTED_COLOR = "#8a8880"


def _trim(levels: dict, descending: bool, keep: int = MAX_LEVELS) -> dict:
    """Ne garde que les `keep` niveaux les plus proches du marche."""
    if len(levels) <= keep:
        return levels
    kept_prices = sorted(levels.keys(), reverse=descending)[:keep]
    return {p: levels[p] for p in kept_prices}


def set_book(exchange, bids, asks):
    """Remplace entierement le carnet d'une plateforme (snapshot complet)."""
    with LOCK:
        BOOKS[exchange]["bids"] = _trim(bids, descending=True)
        BOOKS[exchange]["asks"] = _trim(asks, descending=False)


def apply_updates(exchange, bid_updates, ask_updates):
    """Applique des mises a jour incrementales (prix -> nouvelle quantite).
    Une quantite de 0 supprime le niveau de prix."""
    with LOCK:
        book = BOOKS[exchange]
        for price, qty in bid_updates:
            if qty == 0:
                book["bids"].pop(price, None)
            else:
                book["bids"][price] = qty
        for price, qty in ask_updates:
            if qty == 0:
                book["asks"].pop(price, None)
            else:
                book["asks"][price] = qty
        book["bids"] = _trim(book["bids"], descending=True)
        book["asks"] = _trim(book["asks"], descending=False)


# ----------------------------------------------------------------------------
# Binance -- snapshot complet (20 niveaux) toutes les 100ms, pas de diff a gerer.
# ----------------------------------------------------------------------------

async def binance_loop():
    url = "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms"
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=MAX_WS_SIZE) as ws:
                async for raw in ws:
                    data = json.loads(raw)
                    bids = {float(p): float(q) for p, q in data.get("bids", [])}
                    asks = {float(p): float(q) for p, q in data.get("asks", [])}
                    set_book("binance", bids, asks)
        except Exception as e:
            print(f"[binance] reconnexion suite a : {e}")
            await asyncio.sleep(3)


# ----------------------------------------------------------------------------
# Coinbase -- snapshot initial (potentiellement tres volumineux) puis l2update.
# ----------------------------------------------------------------------------

async def coinbase_loop():
    url = "wss://ws-feed.exchange.coinbase.com"
    sub_msg = {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["level2_batch"],
    }
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=MAX_WS_SIZE) as ws:
                await ws.send(json.dumps(sub_msg))
                async for raw in ws:
                    data = json.loads(raw)
                    msg_type = data.get("type")

                    if msg_type == "snapshot":
                        bids = {float(p): float(q) for p, q in data.get("bids", [])}
                        asks = {float(p): float(q) for p, q in data.get("asks", [])}
                        set_book("coinbase", bids, asks)

                    elif msg_type == "l2update":
                        bid_updates, ask_updates = [], []
                        for side, price, qty in data.get("changes", []):
                            entry = (float(price), float(qty))
                            if side == "buy":
                                bid_updates.append(entry)
                            else:
                                ask_updates.append(entry)
                        apply_updates("coinbase", bid_updates, ask_updates)
        except Exception as e:
            print(f"[coinbase] reconnexion suite a : {e}")
            await asyncio.sleep(3)


# ----------------------------------------------------------------------------
# Kraken -- snapshot initial (as/bs) puis mises a jour (a/b).
# ----------------------------------------------------------------------------

async def kraken_loop():
    url = "wss://ws.kraken.com"
    sub_msg = {
        "event": "subscribe",
        "pair": ["XBT/USD"],
        "subscription": {"name": "book", "depth": 100},
    }
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=MAX_WS_SIZE) as ws:
                await ws.send(json.dumps(sub_msg))
                async for raw in ws:
                    data = json.loads(raw)

                    # Les messages utiles sont des listes ; les messages
                    # systeme (heartbeat, statut...) sont des dicts -> on ignore.
                    if not isinstance(data, list):
                        continue

                    payload = data[1]

                    # Snapshot initial
                    if "as" in payload or "bs" in payload:
                        bids = {float(p): float(v) for p, v, *_ in payload.get("bs", [])}
                        asks = {float(p): float(v) for p, v, *_ in payload.get("as", [])}
                        set_book("kraken", bids, asks)
                        continue

                    # Mises a jour incrementales (peuvent etre combinees
                    # dans data[1] et data[2] selon la version du flux)
                    bid_updates, ask_updates = [], []
                    for part in data[1:-2]:
                        if not isinstance(part, dict):
                            continue
                        for p, v, *_ in part.get("b", []):
                            bid_updates.append((float(p), float(v)))
                        for p, v, *_ in part.get("a", []):
                            ask_updates.append((float(p), float(v)))
                    if bid_updates or ask_updates:
                        apply_updates("kraken", bid_updates, ask_updates)
        except Exception as e:
            print(f"[kraken] reconnexion suite a : {e}")
            await asyncio.sleep(3)


# ----------------------------------------------------------------------------
# Lancement des 3 connexions websocket dans un thread dedie
# ----------------------------------------------------------------------------

def start_websocket_thread():
    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(
            asyncio.gather(
                binance_loop(),
                coinbase_loop(),
                kraken_loop(),
            )
        )

    t = threading.Thread(target=runner, daemon=True)
    t.start()


# ----------------------------------------------------------------------------
# Calcul de la profondeur cumulee
# ----------------------------------------------------------------------------

def cumulative_depth(levels: dict, descending: bool):
    """levels: {prix: quantite} -> (liste_prix, liste_volume_cumule) triee."""
    prices = sorted(levels.keys(), reverse=descending)
    cum = 0.0
    out_prices, out_cum = [], []
    for p in prices:
        cum += levels[p]
        out_prices.append(p)
        out_cum.append(cum)
    if descending:
        out_prices.reverse()
        out_cum.reverse()
    return out_prices, out_cum


# ----------------------------------------------------------------------------
# Affichage matplotlib -- 3 panneaux (un par plateforme), style sombre,
# courbes de profondeur remplies + prix courant et spread en titre.
# ----------------------------------------------------------------------------

def main():
    start_websocket_thread()

    fig, axes = plt.subplots(1, 3, figsize=(14, 5.5), facecolor=BG_COLOR)
    fig.suptitle("Carnet d'ordres BTC/USD en temps reel", color=TEXT_COLOR, fontsize=14)

    def style_axis(ax):
        ax.set_facecolor(PANEL_COLOR)
        for spine in ax.spines.values():
            spine.set_color("#3a3a40")
        ax.tick_params(colors=MUTED_COLOR, labelsize=8)
        ax.grid(alpha=0.12, color=MUTED_COLOR)

    def update(frame):
        with LOCK:
            snapshot = {ex: {"bids": dict(b["bids"]), "asks": dict(b["asks"])}
                        for ex, b in BOOKS.items()}

        for ax, (ex, book) in zip(axes, snapshot.items()):
            ax.clear()
            style_axis(ax)

            bids, asks = book["bids"], book["asks"]
            if not bids or not asks:
                ax.text(0.5, 0.5, "connexion en cours...", ha="center", va="center",
                         transform=ax.transAxes, color=MUTED_COLOR, fontsize=9)
                ax.set_title(LABELS[ex], color=TEXT_COLOR, loc="left", fontsize=11)
                continue

            bid_prices, bid_cum = cumulative_depth(bids, descending=True)
            ask_prices, ask_cum = cumulative_depth(asks, descending=False)

            best_bid = max(bids.keys())
            best_ask = min(asks.keys())
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
                f"{LABELS[ex]}\n${mid:,.2f}   spread {spread:.2f}$",
                color=TEXT_COLOR, loc="left", fontsize=11
            )
            ax.set_xlabel("Prix (USD)", color=MUTED_COLOR, fontsize=8)
            if ex == "binance":
                ax.set_ylabel("Volume cumule (BTC)", color=MUTED_COLOR, fontsize=8)

    ani = animation.FuncAnimation(fig, update, interval=1000)
    plt.tight_layout(rect=[0, 0, 1, 0.93])
    plt.show()


if __name__ == "__main__":
    main()
