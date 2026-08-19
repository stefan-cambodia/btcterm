"""
Bitcoin Liquidity Pool Monitor — Temps réel
Dépendances : pip install requests matplotlib
Lancement    : python btc_liquidity.py
"""

import time
import threading
from collections import deque
from datetime import datetime

import requests
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.animation as animation
from matplotlib.patches import FancyBboxPatch

# ── Configuration ────────────────────────────────────────────────────────────
SYMBOL        = "BTCUSDT"
REFRESH_MS    = 3000        # intervalle de rafraîchissement en ms
HISTORY_SIZE  = 60          # nombre de prix conservés
ORDER_LEVELS  = 10          # niveaux du carnet d'ordres affichés

BASE_URL      = "https://api.binance.com/api/v3"

# ── Stockage partagé (thread-safe via deque) ─────────────────────────────────
price_history  = deque(maxlen=HISTORY_SIZE)
time_history   = deque(maxlen=HISTORY_SIZE)
bids_data      = []   # [(price, qty), ...]
asks_data      = []
ticker_data    = {}
lock           = threading.Lock()


# ── Récupération des données ─────────────────────────────────────────────────
def fetch_data():
    try:
        ob = requests.get(f"{BASE_URL}/depth",
                          params={"symbol": SYMBOL, "limit": ORDER_LEVELS},
                          timeout=5).json()
        tk = requests.get(f"{BASE_URL}/ticker/24hr",
                          params={"symbol": SYMBOL},
                          timeout=5).json()

        bids = [(float(p), float(q)) for p, q in ob["bids"]]
        asks = [(float(p), float(q)) for p, q in ob["asks"]]
        price = float(tk["lastPrice"])

        with lock:
            bids_data.clear();  bids_data.extend(bids)
            asks_data.clear();  asks_data.extend(asks)
            price_history.append(price)
            time_history.append(datetime.now().strftime("%H:%M:%S"))
            ticker_data.update(tk)
    except Exception as e:
        print(f"[Erreur réseau] {e}")


def background_loop():
    while True:
        fetch_data()
        time.sleep(REFRESH_MS / 1000)


# ── Analyse de tendance ───────────────────────────────────────────────────────
def compute_trend(prices):
    if len(prices) < 10:
        return "Insuffisant", "gray", "◆"
    ref   = list(prices)[-10]
    last  = list(prices)[-1]
    delta = (last - ref) / ref * 100
    if delta >  0.05:  return f"Haussière  +{delta:.3f}%", "#16a34a", "▲"
    if delta < -0.05:  return f"Baissière  {delta:.3f}%",  "#dc2626", "▼"
    return f"Neutre  {delta:.3f}%", "#6b7280", "◆"


# ── Mise en page ──────────────────────────────────────────────────────────────
plt.style.use("dark_background")
fig = plt.figure(figsize=(14, 8), facecolor="#0f172a")
fig.canvas.manager.set_window_title("Bitcoin — Pools de liquidité")

gs = gridspec.GridSpec(3, 3, figure=fig,
                       hspace=0.55, wspace=0.35,
                       left=0.06, right=0.97,
                       top=0.91, bottom=0.07)

ax_price = fig.add_subplot(gs[0:2, :])   # graphique prix (2 lignes × 3 cols)
ax_bids  = fig.add_subplot(gs[2, 0])     # carnet achat
ax_asks  = fig.add_subplot(gs[2, 1])     # carnet vente
ax_info  = fig.add_subplot(gs[2, 2])     # métriques texte

for ax in (ax_price, ax_bids, ax_asks, ax_info):
    ax.set_facecolor("#1e293b")
    for spine in ax.spines.values():
        spine.set_edgecolor("#334155")

ax_info.axis("off")

fig.suptitle("BTC/USDT · Pools de liquidité temps réel · Binance",
             color="#94a3b8", fontsize=12, y=0.97)


# ── Fonction d'animation ──────────────────────────────────────────────────────
def animate(_frame):
    with lock:
        prices = list(price_history)
        times  = list(time_history)
        bids   = list(bids_data)
        asks   = list(asks_data)
        tk     = dict(ticker_data)

    if not prices or not bids or not asks:
        return

    price       = prices[-1]
    prev_close  = float(tk.get("prevClosePrice", price))
    pct_24h     = float(tk.get("priceChangePercent", 0))
    volume_24h  = float(tk.get("volume", 0))
    best_bid    = bids[0][0]
    best_ask    = asks[0][0]
    spread      = best_ask - best_bid
    spread_pct  = spread / best_ask * 100

    total_bid_qty = sum(q for _, q in bids)
    total_ask_qty = sum(q for _, q in asks)
    ratio = total_bid_qty / total_ask_qty if total_ask_qty else 1

    trend_label, trend_color, trend_arrow = compute_trend(prices)

    # ── Graphique prix ────────────────────────────────────────────────────────
    ax_price.clear()
    ax_price.set_facecolor("#1e293b")

    ax_price.plot(times, prices, color=trend_color, linewidth=2, zorder=3)
    ax_price.fill_between(range(len(prices)), prices,
                          min(prices) * 0.9999,
                          color=trend_color, alpha=0.12)

    if len(prices) > 1:
        ax_price.axhline(prices[0], color="#475569", linewidth=0.8,
                         linestyle="--", alpha=0.6)

    ax_price.set_xlim(0, max(HISTORY_SIZE - 1, len(prices) - 1))
    ax_price.set_xticks(range(0, len(times), max(1, len(times) // 8)))
    ax_price.set_xticklabels(
        [times[i] for i in range(0, len(times), max(1, len(times) // 8))],
        color="#64748b", fontsize=7, rotation=20)
    ax_price.yaxis.set_tick_params(colors="#64748b", labelsize=8)
    ax_price.set_ylabel("Prix (USDT)", color="#64748b", fontsize=8)

    sign = "+" if pct_24h >= 0 else ""
    ax_price.set_title(
        f"${price:,.2f}   {sign}{pct_24h:.2f}% 24h   {trend_arrow} {trend_label}",
        color=trend_color, fontsize=10, pad=6)

    for sp in ax_price.spines.values():
        sp.set_edgecolor("#334155")
    ax_price.set_facecolor("#1e293b")

    # ── Bids (achats) ─────────────────────────────────────────────────────────
    ax_bids.clear()
    ax_bids.set_facecolor("#1e293b")

    bid_prices = [b[0] for b in bids]
    bid_qtys   = [b[1] for b in bids]
    bars_b = ax_bids.barh(range(len(bids)), bid_qtys,
                          color="#16a34a", alpha=0.75, height=0.7)

    ax_bids.set_yticks(range(len(bids)))
    ax_bids.set_yticklabels([f"${p:,.0f}" for p in bid_prices],
                             color="#94a3b8", fontsize=7)
    ax_bids.xaxis.set_tick_params(colors="#64748b", labelsize=7)
    ax_bids.set_title("Bids (Achats)", color="#16a34a", fontsize=9)
    ax_bids.invert_yaxis()
    for sp in ax_bids.spines.values(): sp.set_edgecolor("#334155")

    # ── Asks (ventes) ─────────────────────────────────────────────────────────
    ax_asks.clear()
    ax_asks.set_facecolor("#1e293b")

    ask_prices = [a[0] for a in asks]
    ask_qtys   = [a[1] for a in asks]
    ax_asks.barh(range(len(asks)), ask_qtys,
                 color="#dc2626", alpha=0.75, height=0.7)

    ax_asks.set_yticks(range(len(asks)))
    ax_asks.set_yticklabels([f"${p:,.0f}" for p in ask_prices],
                             color="#94a3b8", fontsize=7)
    ax_asks.xaxis.set_tick_params(colors="#64748b", labelsize=7)
    ax_asks.set_title("Asks (Ventes)", color="#dc2626", fontsize=9)
    ax_asks.invert_yaxis()
    for sp in ax_asks.spines.values(): sp.set_edgecolor("#334155")

    # ── Panneau métriques ─────────────────────────────────────────────────────
    ax_info.clear()
    ax_info.axis("off")
    ax_info.set_facecolor("#1e293b")

    ratio_color = "#16a34a" if ratio > 1.1 else "#dc2626" if ratio < 0.9 else "#94a3b8"
    ratio_label_txt = "Pression acheteuse" if ratio > 1.1 \
                      else "Pression vendeuse" if ratio < 0.9 else "Équilibré"

    lines = [
        ("Spread",        f"${spread:.2f}  ({spread_pct:.4f}%)", "#94a3b8"),
        ("Bid/Ask ratio", f"{ratio:.3f}  — {ratio_label_txt}", ratio_color),
        ("Volume 24h",    f"{volume_24h:,.2f} BTC", "#94a3b8"),
        ("Meilleur bid",  f"${best_bid:,.2f}", "#16a34a"),
        ("Meilleur ask",  f"${best_ask:,.2f}", "#dc2626"),
        ("Tendance",      trend_label, trend_color),
        ("Heure",         datetime.now().strftime("%H:%M:%S"), "#64748b"),
    ]

    for i, (lbl, val, col) in enumerate(lines):
        y = 0.95 - i * 0.135
        ax_info.text(0.0, y, lbl, color="#64748b",
                     fontsize=7.5, transform=ax_info.transAxes, va="top")
        ax_info.text(0.0, y - 0.055, val, color=col,
                     fontsize=8.5, fontweight="bold",
                     transform=ax_info.transAxes, va="top")

    ax_info.set_title("Métriques", color="#94a3b8", fontsize=9)

    fig.canvas.draw_idle()


# ── Lancement ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Connexion à Binance...")
    fetch_data()   # premier appel synchrone pour pré-remplir

    t = threading.Thread(target=background_loop, daemon=True)
    t.start()

    ani = animation.FuncAnimation(fig, animate,
                                  interval=REFRESH_MS,
                                  cache_frame_data=False)
    plt.show()
