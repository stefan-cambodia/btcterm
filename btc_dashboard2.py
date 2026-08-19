#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════╗
║          BTC DASHBOARD — Ultimate Monitor                ║
║  Live data · MA Cross · RSI · CRSI · Volume · Signals   ║
╚══════════════════════════════════════════════════════════╝

Requirements:
    pip install dash plotly ccxt pandas numpy dash-bootstrap-components

Run:
    python btc_dashboard.py
Then open: http://127.0.0.1:8050
"""

import dash
from dash import dcc, html, Input, Output, State, callback_context
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import ccxt
import json
import os
from datetime import datetime

# ──────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────
EXCHANGE = ccxt.binance()
SYMBOL   = "BTC/USDT"

TIMEFRAME_MAP = {
    "1s":   ("1s",  100),
    "15m":  ("15m", 200),
    "30m":  ("30m", 200),
    "1h":   ("1h",  300),
    "6h":   ("6h",  300),
    "12h":  ("12h", 300),
    "1d":   ("1d",  365),
    "1w":   ("1w",  200),
    "1M":   ("1M",  60),
    "1y":   ("1d",  365),
    "All":  ("1w",  500),
}

SAVE_DIR = os.path.expanduser("~/btc_charts")
os.makedirs(SAVE_DIR, exist_ok=True)

# ──────────────────────────────────────────────────────────
# INDICATORS
# ──────────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)

def compute_crsi(close: pd.Series, rsi_p=3, streak_p=2, pct_p=100) -> pd.Series:
    """Connors RSI = avg(RSI3, StreakRSI2, PercentRank100)"""
    rsi3 = compute_rsi(close, rsi_p)
    # Up/Down streak
    streak = pd.Series(0.0, index=close.index)
    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i-1]:
            streak.iloc[i] = max(streak.iloc[i-1], 0) + 1
        elif close.iloc[i] < close.iloc[i-1]:
            streak.iloc[i] = min(streak.iloc[i-1], 0) - 1
    streak_rsi = compute_rsi(streak, streak_p)
    # Percent rank
    daily_chg  = close.pct_change()
    pct_rank   = daily_chg.rolling(pct_p).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    )
    return ((rsi3 + streak_rsi + pct_rank) / 3).clip(0, 100)

def compute_signals(df: pd.DataFrame):
    """Golden/Death cross signals and RSI extremes."""
    buy_dates, buy_prices   = [], []
    sell_dates, sell_prices = [], []

    # MA cross signals
    if "ma9" in df.columns and "ma26" in df.columns:
        prev_above = df["ma9"].shift(1) > df["ma26"].shift(1)
        cross_up   = (df["ma9"] > df["ma26"]) & (~prev_above)
        cross_down = (df["ma9"] < df["ma26"]) & (prev_above)
        buy_dates  += list(df.index[cross_up])
        buy_prices += list(df["close"][cross_up])
        sell_dates += list(df.index[cross_down])
        sell_prices += list(df["close"][cross_down])

    # RSI signals
    if "rsi" in df.columns:
        rsi_buy  = (df["rsi"] < 30) & (df["rsi"].shift(1) >= 30)
        rsi_sell = (df["rsi"] > 70) & (df["rsi"].shift(1) <= 70)
        buy_dates  += list(df.index[rsi_buy])
        buy_prices += list(df["close"][rsi_buy])
        sell_dates += list(df.index[rsi_sell])
        sell_prices += list(df["close"][rsi_sell])

    return buy_dates, buy_prices, sell_dates, sell_prices

# ──────────────────────────────────────────────────────────
# DATA FETCHING
# ──────────────────────────────────────────────────────────

def fetch_ohlcv(timeframe_label: str) -> pd.DataFrame:
    tf, limit = TIMEFRAME_MAP[timeframe_label]
    try:
        raw = EXCHANGE.fetch_ohlcv(SYMBOL, timeframe=tf, limit=limit)
    except Exception as e:
        print(f"[WARN] fetch failed: {e} — using demo data")
        return generate_demo_data(limit)

    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms")
    df.set_index("ts", inplace=True)

    # Moving averages
    df["ma9"]  = df["close"].ewm(span=9,   adjust=False).mean()
    df["ma26"] = df["close"].ewm(span=26,  adjust=False).mean()
    df["ma50"] = df["close"].rolling(50).mean()
    df["ma200"]= df["close"].rolling(200).mean()

    # Bollinger Bands (20,2)
    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_mid"]   = bb_mid

    # RSI & CRSI
    df["rsi"]  = compute_rsi(df["close"], 14)
    df["crsi"] = compute_crsi(df["close"])

    # Volatility close-to-close (annualised %)
    df["volatility"] = df["close"].pct_change().rolling(10).std() * np.sqrt(252) * 100

    return df.dropna(subset=["open","high","low","close"])


def generate_demo_data(limit: int = 300) -> pd.DataFrame:
    """Generates realistic-looking BTC OHLCV for offline use."""
    np.random.seed(42)
    dates  = pd.date_range(end=datetime.utcnow(), periods=limit, freq="1h")
    price  = 80000.0
    prices = []
    for _ in range(limit):
        price *= np.exp(np.random.normal(0.0002, 0.018))
        prices.append(price)
    close  = pd.Series(prices)
    high   = close * (1 + abs(np.random.normal(0, 0.005, limit)))
    low    = close * (1 - abs(np.random.normal(0, 0.005, limit)))
    open_  = close.shift(1).fillna(close)
    volume = np.random.lognormal(20, 1, limit)
    df = pd.DataFrame({"open": open_, "high": high, "low": low,
                        "close": close, "volume": volume}, index=dates)
    df.index.name = "ts"
    df["ma9"]   = df["close"].ewm(span=9,  adjust=False).mean()
    df["ma26"]  = df["close"].ewm(span=26, adjust=False).mean()
    df["ma50"]  = df["close"].rolling(50).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    bb_mid = df["close"].rolling(20).mean()
    bb_std = df["close"].rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std
    df["bb_mid"]   = bb_mid
    df["rsi"]       = compute_rsi(df["close"])
    df["crsi"]      = compute_crsi(df["close"])
    df["volatility"]= df["close"].pct_change().rolling(10).std() * np.sqrt(252) * 100
    return df.dropna(subset=["open","high","low","close"])

# ──────────────────────────────────────────────────────────
# CHART BUILDER
# ──────────────────────────────────────────────────────────

DARK_BG    = "#0a0e1a"
PANEL_BG   = "#0f1520"
GRID_COLOR = "#1e2a3a"
TEXT_COLOR  = "#c8d8e8"
GREEN       = "#00e676"
RED         = "#ff1744"
ORANGE      = "#ff9100"
BLUE        = "#40c4ff"
PURPLE      = "#ea80fc"
GOLD        = "#ffd740"

def build_figure(df: pd.DataFrame, log_scale: bool, show_signals: bool,
                  show_bb: bool, show_ma200: bool) -> go.Figure:

    buy_dates, buy_prices, sell_dates, sell_prices = (
        compute_signals(df) if show_signals else ([], [], [], [])
    )

    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=[0.55, 0.15, 0.15, 0.15],
        subplot_titles=["", "RSI / CRSI", "Volatility", "Volume"]
    )

    # ── Candlesticks ──
    fig.add_trace(go.Candlestick(
        x=df.index, open=df["open"], high=df["high"],
        low=df["low"],  close=df["close"],
        name="BTC/USDT",
        increasing_line_color=GREEN, decreasing_line_color=RED,
        increasing_fillcolor=GREEN,  decreasing_fillcolor=RED,
        line_width=1,
    ), row=1, col=1)

    # ── Bollinger Bands ──
    if show_bb and "bb_upper" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["bb_upper"], name="BB Upper",
            line=dict(color=PURPLE, width=1, dash="dot"), opacity=0.6
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["bb_lower"], name="BB Lower",
            line=dict(color=PURPLE, width=1, dash="dot"), opacity=0.6,
            fill="tonexty", fillcolor="rgba(234,128,252,0.04)"
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=df.index, y=df["bb_mid"], name="BB Mid",
            line=dict(color=PURPLE, width=1, dash="dash"), opacity=0.4
        ), row=1, col=1)

    # ── MAs ──
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ma9"], name="MA 9",
        line=dict(color=GREEN, width=1.5)
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=df["ma26"], name="MA 26",
        line=dict(color=ORANGE, width=1.5)
    ), row=1, col=1)

    if show_ma200 and "ma50" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["ma50"], name="MA 50",
            line=dict(color=BLUE, width=1.2, dash="dash")
        ), row=1, col=1)
    if show_ma200 and "ma200" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["ma200"], name="MA 200",
            line=dict(color=GOLD, width=2)
        ), row=1, col=1)

    # ── Buy / Sell Signals ──
    if show_signals and buy_dates:
        fig.add_trace(go.Scatter(
            x=buy_dates, y=buy_prices, mode="markers", name="BUY",
            marker=dict(symbol="triangle-up", size=14, color=GREEN,
                        line=dict(color="white", width=1))
        ), row=1, col=1)
    if show_signals and sell_dates:
        fig.add_trace(go.Scatter(
            x=sell_dates, y=sell_prices, mode="markers", name="SELL",
            marker=dict(symbol="triangle-down", size=14, color=RED,
                        line=dict(color="white", width=1))
        ), row=1, col=1)

    # ── RSI ──
    if "rsi" in df.columns:
        fig.add_hrect(y0=70, y1=100, row=2, col=1,
                      fillcolor="rgba(255,23,68,0.07)", line_width=0)
        fig.add_hrect(y0=0, y1=30, row=2, col=1,
                      fillcolor="rgba(0,230,118,0.07)", line_width=0)
        fig.add_hline(y=70, row=2, col=1,
                      line=dict(color=RED, width=1, dash="dot"))
        fig.add_hline(y=30, row=2, col=1,
                      line=dict(color=GREEN, width=1, dash="dot"))
        fig.add_trace(go.Scatter(
            x=df.index, y=df["rsi"], name="RSI 14",
            line=dict(color=BLUE, width=1.5)
        ), row=2, col=1)
    if "crsi" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["crsi"], name="CRSI",
            line=dict(color=PURPLE, width=1.2, dash="dot")
        ), row=2, col=1)

    # ── Volatility ──
    if "volatility" in df.columns:
        fig.add_trace(go.Scatter(
            x=df.index, y=df["volatility"], name="Volatility %",
            line=dict(color=ORANGE, width=1.5),
            fill="tozeroy", fillcolor="rgba(255,145,0,0.08)"
        ), row=3, col=1)

    # ── Volume ──
    colors = [GREEN if c >= o else RED
              for c, o in zip(df["close"], df["open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["volume"], name="Volume",
        marker_color=colors, opacity=0.8,
        marker_line_width=0
    ), row=4, col=1)

    # ── Layout ──
    y_type = "log" if log_scale else "linear"
    fig.update_yaxes(type=y_type, row=1, col=1)

    fig.update_layout(
        height=900,
        paper_bgcolor=DARK_BG,
        plot_bgcolor=PANEL_BG,
        font=dict(family="JetBrains Mono, monospace", color=TEXT_COLOR, size=11),
        legend=dict(
            bgcolor="rgba(10,14,26,0.8)",
            bordercolor=GRID_COLOR, borderwidth=1,
            orientation="h", x=0, y=1.01, xanchor="left",
            font=dict(size=10)
        ),
        xaxis_rangeslider_visible=False,
        margin=dict(l=10, r=10, t=30, b=10),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor=PANEL_BG,
            bordercolor=GRID_COLOR,
            font=dict(family="JetBrains Mono, monospace", color=TEXT_COLOR)
        ),
    )

    for row in range(1, 5):
        fig.update_xaxes(
            row=row, col=1,
            gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR,
            showgrid=True
        )
        fig.update_yaxes(
            row=row, col=1,
            gridcolor=GRID_COLOR, zerolinecolor=GRID_COLOR,
            showgrid=True
        )

    return fig

# ──────────────────────────────────────────────────────────
# DASH APP
# ──────────────────────────────────────────────────────────

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.CYBORG],
    title="BTC Dashboard",
    update_title=None,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}]
)

TIMEFRAMES = list(TIMEFRAME_MAP.keys())

def stat_card(label, value_id, color=TEXT_COLOR):
    return html.Div([
        html.Div(label, style={"fontSize": "9px", "color": "#607080",
                               "letterSpacing": "2px", "textTransform": "uppercase"}),
        html.Div(id=value_id, style={"fontSize": "16px", "fontWeight": "bold",
                                     "color": color, "fontFamily": "JetBrains Mono, monospace"})
    ], style={"textAlign": "center", "padding": "8px 16px",
              "background": PANEL_BG, "borderRadius": "6px",
              "border": f"1px solid {GRID_COLOR}"})

app.layout = html.Div([

    # ── TOP BAR ──
    html.Div([
        html.Div([
            html.Span("₿", style={"fontSize": "28px", "color": GOLD}),
            html.Span(" BTC/USDT", style={"fontSize": "22px", "fontWeight": "900",
                                           "color": TEXT_COLOR, "letterSpacing": "2px"}),
            html.Span(" DASHBOARD", style={"fontSize": "12px", "color": "#607080",
                                            "letterSpacing": "4px", "marginLeft": "6px"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "4px"}),

        # Stats row
        html.Div([
            stat_card("PRICE",   "stat-price",   GOLD),
            stat_card("CHANGE",  "stat-change",  GREEN),
            stat_card("HIGH",    "stat-high",    GREEN),
            stat_card("LOW",     "stat-low",     RED),
            stat_card("RSI",     "stat-rsi",     BLUE),
            stat_card("VOLUME",  "stat-volume",  TEXT_COLOR),
        ], style={"display": "flex", "gap": "8px", "alignItems": "center",
                  "flexWrap": "wrap"}),

    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
              "padding": "12px 20px", "background": PANEL_BG,
              "borderBottom": f"1px solid {GRID_COLOR}"}),

    # ── CONTROLS ──
    html.Div([
        # Timeframe selector
        html.Div([
            html.Span("TIMEFRAME", style={"fontSize": "9px", "color": "#607080",
                                          "letterSpacing": "2px", "marginRight": "8px"}),
            *[html.Button(tf, id=f"tf-{tf}", n_clicks=0,
                className="tf-btn",
                style={
                    "background": PANEL_BG if tf != "1d" else BLUE,
                    "color": TEXT_COLOR, "border": f"1px solid {GRID_COLOR}",
                    "borderRadius": "4px", "padding": "4px 10px",
                    "cursor": "pointer", "fontSize": "11px",
                    "fontFamily": "JetBrains Mono, monospace",
                    "transition": "all 0.15s"
                }
              ) for tf in TIMEFRAMES],
        ], style={"display": "flex", "alignItems": "center", "gap": "4px", "flexWrap": "wrap"}),

        # Right controls
        html.Div([
            dbc.Checklist(
                options=[
                    {"label": " Log Scale",  "value": "log"},
                    {"label": " Signals",    "value": "signals"},
                    {"label": " Bollinger",  "value": "bb"},
                    {"label": " MA50/200",   "value": "ma200"},
                ],
                value=["signals", "ma200"],
                id="overlay-checks",
                inline=True,
                switch=True,
                style={"color": TEXT_COLOR, "fontSize": "12px",
                       "fontFamily": "JetBrains Mono, monospace"}
            ),
            html.Button("⟳ Refresh", id="btn-refresh", n_clicks=0,
                style={"background": "rgba(64,196,255,0.15)", "color": BLUE,
                       "border": f"1px solid {BLUE}", "borderRadius": "4px",
                       "padding": "5px 14px", "cursor": "pointer", "fontSize": "11px",
                       "fontFamily": "JetBrains Mono, monospace"}),
            html.Button("💾 Save PNG", id="btn-save", n_clicks=0,
                style={"background": "rgba(255,215,64,0.1)", "color": GOLD,
                       "border": f"1px solid {GOLD}", "borderRadius": "4px",
                       "padding": "5px 14px", "cursor": "pointer", "fontSize": "11px",
                       "fontFamily": "JetBrains Mono, monospace"}),
        ], style={"display": "flex", "alignItems": "center", "gap": "10px"}),

    ], style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
              "padding": "10px 20px", "background": DARK_BG,
              "borderBottom": f"1px solid {GRID_COLOR}", "flexWrap": "wrap", "gap": "8px"}),

    # ── MAIN CHART ──
    dcc.Graph(
        id="main-chart",
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "modeBarButtonsToRemove": ["select2d", "lasso2d"],
            "toImageButtonOptions": {
                "format": "png", "filename": "btc_chart",
                "height": 900, "width": 1800, "scale": 2
            }
        },
        style={"height": "900px"}
    ),

    # ── STATUS BAR ──
    html.Div([
        html.Span(id="status-bar",
                  style={"fontSize": "10px", "color": "#607080",
                         "fontFamily": "JetBrains Mono, monospace"})
    ], style={"padding": "6px 20px", "background": PANEL_BG,
              "borderTop": f"1px solid {GRID_COLOR}"}),

    # ── HIDDEN STATE ──
    dcc.Store(id="store-tf", data="1d"),
    dcc.Store(id="store-refresh", data=0),
    dcc.Interval(id="auto-refresh", interval=60_000, n_intervals=0),  # every 60s

], style={"background": DARK_BG, "minHeight": "100vh", "fontFamily": "JetBrains Mono, monospace"})

# ──────────────────────────────────────────────────────────
# CALLBACKS
# ──────────────────────────────────────────────────────────

# Timeframe buttons → store
@app.callback(
    Output("store-tf", "data"),
    [Input(f"tf-{tf}", "n_clicks") for tf in TIMEFRAMES],
    prevent_initial_call=True,
)
def update_tf(*args):
    ctx = callback_context
    if not ctx.triggered:
        return "1d"
    btn_id = ctx.triggered[0]["prop_id"].split(".")[0]
    return btn_id.replace("tf-", "")

# Main chart update
@app.callback(
    Output("main-chart",  "figure"),
    Output("stat-price",  "children"),
    Output("stat-change", "children"),
    Output("stat-change", "style"),
    Output("stat-high",   "children"),
    Output("stat-low",    "children"),
    Output("stat-rsi",    "children"),
    Output("stat-volume", "children"),
    Output("status-bar",  "children"),
    Input("store-tf",     "data"),
    Input("overlay-checks","value"),
    Input("btn-refresh",  "n_clicks"),
    Input("auto-refresh", "n_intervals"),
)
def update_chart(tf, overlays, _refresh, _interval):
    overlays = overlays or []
    df = fetch_ohlcv(tf)

    fig = build_figure(
        df,
        log_scale    = "log"     in overlays,
        show_signals = "signals" in overlays,
        show_bb      = "bb"      in overlays,
        show_ma200   = "ma200"   in overlays,
    )

    last  = df["close"].iloc[-1]
    first = df["close"].iloc[0]
    chg   = (last - first) / first * 100
    hi    = df["high"].max()
    lo    = df["low"].min()
    rsi_v = df["rsi"].iloc[-1] if "rsi" in df.columns else float("nan")
    vol_v = df["volume"].sum()

    chg_color = {"fontSize":"16px","fontWeight":"bold","color": GREEN if chg>=0 else RED,
                  "fontFamily":"JetBrains Mono, monospace"}

    def fmt_price(v): return f"${v:,.0f}"
    def fmt_vol(v):
        if v > 1e9: return f"{v/1e9:.2f}B"
        if v > 1e6: return f"{v/1e6:.2f}M"
        return f"{v:,.0f}"

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    status = f"Last update: {now}  ·  Timeframe: {tf}  ·  Candles: {len(df)}"

    return (
        fig,
        fmt_price(last),
        f"{chg:+.2f}%",
        chg_color,
        fmt_price(hi),
        fmt_price(lo),
        f"{rsi_v:.1f}",
        fmt_vol(vol_v),
        status,
    )

# Save chart callback (client-side trigger via downloadImage)
app.clientside_callback(
    """
    function(n, figure) {
        if (n > 0) {
            var ts  = new Date().toISOString().slice(0,19).replace(/:/g,'-');
            var fname = 'btc_chart_' + ts;
            Plotly.downloadImage('main-chart', {format:'png', filename:fname,
                                  height:900, width:1800, scale:2});
        }
        return '';
    }
    """,
    Output("status-bar", "title"),
    Input("btn-save", "n_clicks"),
    State("main-chart", "figure"),
    prevent_initial_call=True,
)

# ──────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════╗
║          BTC DASHBOARD — Starting up...             ║
║  Open your browser at:  http://127.0.0.1:8050       ║
╚══════════════════════════════════════════════════════╝
""")
    app.run(debug=False, host="127.0.0.1", port=8050)
