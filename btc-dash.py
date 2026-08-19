#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║        BTC ULTRA DASHBOARD  —  Real-Time Bitcoin Monitor      ║
║  Signals • MA200 • RSI • CRSI • Volume Profile • Volatility   ║
╚═══════════════════════════════════════════════════════════════╝

INSTALL (once):
    pip install dash dash-bootstrap-components plotly pandas numpy requests

RUN:
    python btc_dashboard.py
    → Open http://127.0.0.1:8050

Data source : Binance public API (no key required)
Auto-refresh : every 10 seconds
"""

# ─────────────────────────────────────────────────────────────
# IMPORTS
# ─────────────────────────────────────────────────────────────
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from datetime import datetime

import plotly.graph_objects as go
from plotly.subplots import make_subplots

import dash
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc

from btcterm import indicators as ind
from btcterm import sources


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
SYMBOL       = "BTCUSDT"
REFRESH_MS   = 10_000            # chart refresh interval (ms)
VOL_BINS     = 60                # volume profile buckets

INTERVALS = {
    "1H":  "1h",
    "4H":  "4h",
    "1D":  "1d",
    "1W":  "1w",
}

# ── Colour palette (dark TradingView-inspired) ──────────────
C = {
    "bg":       "#080b12",
    "panel":    "#0e1117",
    "card":     "#111827",
    "border":   "#1e2639",
    "text":     "#e2e8f0",
    "muted":    "#4b5563",
    "green":    "#00d4aa",
    "red":      "#ff3d6b",
    "orange":   "#f59e0b",
    "blue":     "#3b82f6",
    "purple":   "#a78bfa",
    "yellow":   "#fbbf24",
    "cyan":     "#22d3ee",
    "grid":     "#111827",
    "ma9":      "#00d4aa",
    "ma26":     "#f59e0b",
    "ma200":    "#a78bfa",
    "bb":       "#3b82f6",
    "buy":      "#00ff99",
    "sell":     "#ff2255",
    "poc":      "#fbbf24",
    "va":       "rgba(251,191,36,0.07)",
}

# ─────────────────────────────────────────────────────────────
# TECHNICAL INDICATORS
#
# Les calculs eux-mêmes vivent dans `btcterm.indicators` ; ne reste ici
# que la composition propre à ce panneau (quelles périodes, quelles
# colonnes).
# ─────────────────────────────────────────────────────────────
def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # ── Moving averages ────────────────────────────
    df["ma9"]   = ind.sma(df["close"], 9)
    df["ma26"]  = ind.sma(df["close"], 26)
    df["ma200"] = ind.sma(df["close"], 200)

    # ── Bollinger Bands (20, 2σ) ───────────────────
    df["bb_mid"], df["bb_upper"], df["bb_lower"] = ind.bollinger(df["close"], 20, 2)

    # ── RSI (14) et Connors RSI ────────────────────
    df["rsi"]  = ind.rsi(df["close"], 14)
    df["crsi"] = ind.connors_rsi(df["close"])

    # ── Volatilité annualisée (close-to-close) ─────
    df["vol_252"] = ind.volatility(df["close"], 252)

    # ── ATR ────────────────────────────────────────
    df["atr"] = ind.atr(df, 14)

    # ── Volume MA ─────────────────────────────────
    df["vol_ma20"] = ind.sma(df["volume"], 20)

    # ── Signals ────────────────────────────────────
    df["signal"] = _signals(df)

    return df


def _signals(df: pd.DataFrame) -> pd.Series:
    """Signal gradué de -2 (vente forte) à +2 (achat fort)."""
    return ind.graded_signals(df, fast="ma9", slow="ma26", trend="ma200")


# ─────────────────────────────────────────────────────────────
# VOLUME PROFILE  (Liquidity Clusters)
# ─────────────────────────────────────────────────────────────
def volume_profile(df: pd.DataFrame, bins: int = VOL_BINS):
    """Profil de volume : centres, volumes, POC et bornes de la Value Area."""
    return ind.volume_profile(df, bins)


# ─────────────────────────────────────────────────────────────
# CHART BUILDER
# ─────────────────────────────────────────────────────────────
def build_chart(df: pd.DataFrame, currency: str, eur_rate: float) -> go.Figure:
    mult = eur_rate if currency == "EUR" else 1.0
    sym  = "€" if currency == "EUR" else "$"

    centers, vp_vols, poc, va_lo, va_hi = volume_profile(df)

    # ── Subplot grid ──────────────────────────────
    #   col 1 (82%) : candles / RSI / CRSI / Volume
    #   col 2 (18%) : Volume Profile (shares row 1 y-axis, spanning all rows)
    fig = make_subplots(
        rows=4, cols=2,
        column_widths=[0.82, 0.18],
        row_heights=[0.54, 0.17, 0.13, 0.16],
        shared_xaxes=True,
        vertical_spacing=0.018,
        horizontal_spacing=0.008,
        specs=[
            [{"type": "candlestick"}, {"type": "bar", "rowspan": 4}],
            [{"type": "scatter"},     None],
            [{"type": "scatter"},     None],
            [{"type": "bar"},         None],
        ],
    )

    px  = lambda s: s * mult          # price in chosen currency
    pxv = lambda v: v                 # volumes stay raw

    close = px(df["close"]); high = px(df["high"])
    low   = px(df["low"]);   open_ = px(df["open"])

    # ── 1. Candlesticks ──────────────────────────
    fig.add_trace(go.Candlestick(
        x=df["time"], open=open_, high=high, low=low, close=close,
        name="BTC",
        increasing_fillcolor=C["green"], increasing_line_color=C["green"],
        decreasing_fillcolor=C["red"],   decreasing_line_color=C["red"],
        line_width=1, whiskerwidth=0.4,
    ), row=1, col=1)

    # ── 2. Moving Averages ───────────────────────
    for col, name, color, width, dash in [
        ("ma9",   "MA 9",   C["ma9"],   1.6, "solid"),
        ("ma26",  "MA 26",  C["ma26"],  1.6, "solid"),
        ("ma200", "MA 200", C["ma200"], 2.0, "dot"),
    ]:
        if df[col].notna().any():
            fig.add_trace(go.Scatter(
                x=df["time"], y=px(df[col]), name=name,
                line=dict(color=color, width=width, dash=dash), mode="lines",
                hovertemplate=f"{name}: {sym}%{{y:,.0f}}<extra></extra>",
            ), row=1, col=1)

    # ── 3. Bollinger Bands ───────────────────────
    fig.add_trace(go.Scatter(
        x=df["time"], y=px(df["bb_upper"]), name="BB Upper",
        line=dict(color=C["bb"], width=0.7, dash="dash"),
        mode="lines", showlegend=False,
        hovertemplate=f"BB Upper: {sym}%{{y:,.0f}}<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df["time"], y=px(df["bb_lower"]), name="BB Lower",
        line=dict(color=C["bb"], width=0.7, dash="dash"),
        fill="tonexty", fillcolor="rgba(59,130,246,0.04)",
        mode="lines", showlegend=False,
        hovertemplate=f"BB Lower: {sym}%{{y:,.0f}}<extra></extra>",
    ), row=1, col=1)

    # ── 4. Point of Control & Value Area ─────────
    fig.add_hline(
        y=poc * mult, row=1, col=1,
        line=dict(color=C["poc"], width=1.4, dash="dash"),
        annotation_text=f" POC  {sym}{poc*mult:,.0f}",
        annotation_position="left",
        annotation_font=dict(color=C["poc"], size=10),
    )
    fig.add_hrect(
        y0=va_lo * mult, y1=va_hi * mult, row=1, col=1,
        fillcolor=C["va"], line_width=0,
    )
    fig.add_hline(
        y=va_hi * mult, row=1, col=1,
        line=dict(color=C["poc"], width=0.5, dash="dot"),
        annotation_text=f" VAH  {sym}{va_hi*mult:,.0f}",
        annotation_position="left",
        annotation_font=dict(color=C["poc"], size=9),
    )
    fig.add_hline(
        y=va_lo * mult, row=1, col=1,
        line=dict(color=C["poc"], width=0.5, dash="dot"),
        annotation_text=f" VAL  {sym}{va_lo*mult:,.0f}",
        annotation_position="left",
        annotation_font=dict(color=C["poc"], size=9),
    )

    # ── 5. Buy / Sell signal markers ─────────────
    for sig_val, label, marker, offset, color in [
        ( 2, "STRONG BUY",  "triangle-up",   0.994, C["buy"]),
        ( 1, "BUY",         "triangle-up",   0.997, C["green"]),
        (-1, "SELL",        "triangle-down", 1.003, C["red"]),
        (-2, "STRONG SELL", "triangle-down", 1.006, C["sell"]),
    ]:
        subset = df[df["signal"] == sig_val]
        if subset.empty:
            continue
        y_pos = (subset["low"] if sig_val > 0 else subset["high"]) * mult * offset
        fig.add_trace(go.Scatter(
            x=subset["time"], y=y_pos,
            mode="markers", name=label,
            marker=dict(
                symbol=marker,
                size=13 if abs(sig_val) == 2 else 9,
                color=color,
                opacity=1.0 if abs(sig_val) == 2 else 0.7,
                line=dict(width=0),
            ),
            hovertemplate=f"{label}<br>{sym}%{{y:,.0f}}<extra></extra>",
        ), row=1, col=1)

    # ── 6. Volume Profile (horizontal bars) ──────
    vp_norm = vp_vols / vp_vols.max()
    vp_clr  = [
        C["poc"]    if abs(c - poc) < (poc * 0.004) else
        C["green"]  if va_lo <= c <= va_hi else
        "rgba(100,116,139,0.5)"
        for c in centers
    ]
    fig.add_trace(go.Bar(
        x=vp_norm, y=centers * mult,
        orientation="h", name="Liq. Clusters",
        marker=dict(color=vp_clr, opacity=0.75),
        hovertemplate="Vol: %{x:.2f}<br>%{y:,.0f}<extra></extra>",
    ), row=1, col=2)

    # ── 7. RSI panel ─────────────────────────────
    rsi_clr = [
        C["red"]   if v > 70 else
        C["green"] if v < 30 else
        C["blue"]
        for v in df["rsi"].fillna(50)
    ]
    fig.add_trace(go.Scatter(
        x=df["time"], y=df["rsi"], name="RSI 14",
        line=dict(color=C["blue"], width=1.4), mode="lines",
        hovertemplate="RSI: %{y:.1f}<extra></extra>",
    ), row=2, col=1)
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(255,61,107,0.08)", line_width=0, row=2, col=1)
    fig.add_hrect(y0=0,  y1=30,  fillcolor="rgba(0,212,170,0.08)",  line_width=0, row=2, col=1)
    for lvl, c in ((30, C["green"]), (50, C["muted"]), (70, C["red"])):
        fig.add_hline(y=lvl, line=dict(color=c, width=0.6, dash="dot"), row=2, col=1)

    # ── 8. CRSI panel ────────────────────────────
    fig.add_trace(go.Scatter(
        x=df["time"], y=df["crsi"], name="CRSI",
        line=dict(color=C["purple"], width=1.4), mode="lines",
        hovertemplate="CRSI: %{y:.1f}<extra></extra>",
    ), row=3, col=1)
    for lvl in (20, 50, 80):
        fig.add_hline(y=lvl, line=dict(color=C["muted"], width=0.5, dash="dot"), row=3, col=1)
    fig.add_hrect(y0=80, y1=100, fillcolor="rgba(255,61,107,0.07)", line_width=0, row=3, col=1)
    fig.add_hrect(y0=0,  y1=20,  fillcolor="rgba(0,212,170,0.07)",  line_width=0, row=3, col=1)

    # ── 9. Volume bars ───────────────────────────
    v_clr = [
        C["green"] if df["close"].iloc[i] >= df["open"].iloc[i] else C["red"]
        for i in range(len(df))
    ]
    fig.add_trace(go.Bar(
        x=df["time"], y=df["volume"], name="Volume",
        marker_color=v_clr, opacity=0.75,
        hovertemplate="Vol: %{y:,.0f}<extra></extra>",
    ), row=4, col=1)
    fig.add_trace(go.Scatter(
        x=df["time"], y=df["vol_ma20"], name="Vol MA20",
        line=dict(color=C["orange"], width=1.2), mode="lines", showlegend=False,
    ), row=4, col=1)

    # ── Layout ───────────────────────────────────
    axis_common = dict(
        gridcolor=C["grid"], zerolinecolor=C["grid"],
        showgrid=True, tickfont=dict(size=9, color=C["muted"]),
    )
    fig.update_layout(
        paper_bgcolor=C["bg"],
        plot_bgcolor=C["panel"],
        font=dict(family="'JetBrains Mono', 'Fira Code', monospace",
                  color=C["text"], size=11),
        margin=dict(l=12, r=12, t=8, b=8),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=11),
        legend=dict(
            bgcolor="rgba(14,17,23,0.92)", bordercolor=C["border"],
            borderwidth=1, orientation="h",
            x=0.0, y=1.015, font=dict(size=10),
            itemsizing="constant",
        ),
        xaxis_rangeslider_visible=False,
        dragmode="zoom",
        newshape_line_color=C["cyan"],
        modebar=dict(bgcolor="rgba(0,0,0,0)", color=C["muted"],
                     activecolor=C["text"]),
    )

    # Axes configuration
    fig.update_yaxes(title_text=f"Price ({sym})",  row=1, col=1, **axis_common)
    fig.update_yaxes(title_text="RSI",     row=2, col=1, range=[0, 100], **axis_common)
    fig.update_yaxes(title_text="CRSI",    row=3, col=1, range=[0, 100], **axis_common)
    fig.update_yaxes(title_text="Volume",  row=4, col=1, **axis_common)
    fig.update_yaxes(showticklabels=False, showgrid=False, row=1, col=2)

    for r in range(1, 5):
        fig.update_xaxes(row=r, col=1, **axis_common)

    return fig


# ─────────────────────────────────────────────────────────────
# DASH APP
# ─────────────────────────────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[
        dbc.themes.CYBORG,
        "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700"
        "&family=Space+Grotesk:wght@400;600;700;800&display=swap",
    ],
    title="₿ BTC Ultra Dashboard",
    suppress_callback_exceptions=True,
)

# ── Helper: stat card ─────────────────────────────────────────
def stat_card(card_id: str, label: str) -> dbc.Col:
    return dbc.Col(
        html.Div([
            html.P(label, style={
                "margin": "0 0 4px 0", "fontSize": "9px",
                "letterSpacing": "2px", "color": C["muted"],
                "textTransform": "uppercase", "fontFamily": "'Space Grotesk'",
            }),
            html.Div(id=card_id, children="—", style={
                "fontSize": "15px", "fontWeight": 700,
                "fontFamily": "'Space Grotesk'", "letterSpacing": "0.5px",
            }),
        ], style={
            "background": C["card"],
            "border": f"1px solid {C['border']}",
            "borderRadius": "10px",
            "padding": "10px 14px",
            "minHeight": "58px",
        }),
        xs=6, sm=4, md=3, lg="auto", style={"flex": "1 1 120px"},
    )


# ── Layout ────────────────────────────────────────────────────
app.layout = html.Div(
    style={
        "background": C["bg"], "minHeight": "100vh",
        "padding": "16px 20px",
        "fontFamily": "'JetBrains Mono', monospace",
    },
    children=[
        dcc.Interval(id="tick", interval=REFRESH_MS, n_intervals=0),
        dcc.Store(id="eur", data=0.924),

        # ── HEADER ──────────────────────────────────────────
        html.Div([
            # Logo / title
            html.Div([
                html.Span("₿", style={
                    "fontSize": "32px", "color": C["orange"],
                    "marginRight": "10px", "lineHeight": "1",
                }),
                html.Div([
                    html.Span("BTC ULTRA", style={
                        "fontSize": "22px", "fontWeight": 800,
                        "fontFamily": "'Space Grotesk'",
                        "background": f"linear-gradient(90deg,{C['orange']},{C['yellow']})",
                        "-webkit-background-clip": "text",
                        "color": "transparent",
                        "letterSpacing": "1px",
                    }),
                    html.Span("  DASHBOARD", style={
                        "fontSize": "11px", "color": C["muted"],
                        "letterSpacing": "4px", "marginLeft": "4px",
                        "verticalAlign": "middle",
                    }),
                ]),
            ], style={"display": "flex", "alignItems": "center"}),

            # Controls
            html.Div([
                # Interval selector
                html.Div([
                    html.Span("TIMEFRAME", style={
                        "fontSize": "9px", "color": C["muted"],
                        "letterSpacing": "2px", "marginRight": "8px",
                    }),
                    dcc.Dropdown(
                        id="interval-dd",
                        options=[{"label": k, "value": v} for k, v in INTERVALS.items()],
                        value="1d", clearable=False,
                        style={"width": "85px", "fontSize": "12px",
                               "fontFamily": "'JetBrains Mono'"},
                    ),
                ], style={"display": "flex", "alignItems": "center", "marginRight": "20px"}),

                # Currency toggle
                html.Div([
                    html.Span("CURRENCY", style={
                        "fontSize": "9px", "color": C["muted"],
                        "letterSpacing": "2px", "marginRight": "8px",
                    }),
                    dcc.RadioItems(
                        id="currency-sel",
                        options=[{"label": "  USD ($)", "value": "USD"},
                                 {"label": "  EUR (€)", "value": "EUR"}],
                        value="USD", inline=True,
                        inputStyle={"marginRight": "4px", "marginLeft": "12px",
                                    "accentColor": C["orange"]},
                        labelStyle={"color": C["text"], "fontSize": "12px",
                                    "fontFamily": "'Space Grotesk'"},
                    ),
                ], style={"display": "flex", "alignItems": "center"}),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "center",
            "marginBottom": "18px", "paddingBottom": "14px",
            "borderBottom": f"1px solid {C['border']}",
        }),

        # ── STAT CARDS ──────────────────────────────────────
        dbc.Row([
            stat_card("c-price",  "BTC Price"),
            stat_card("c-change", "24h Change"),
            stat_card("c-signal", "Signal"),
            stat_card("c-rsi",    "RSI (14)"),
            stat_card("c-crsi",   "CRSI"),
            stat_card("c-vol",    "24h Volume"),
            stat_card("c-ma200",  "vs MA200"),
            stat_card("c-atr",    "ATR (14)"),
            stat_card("c-volat",  "Volatility"),
            stat_card("c-spread", "BB Width"),
        ], className="g-2 mb-3", style={"flexWrap": "wrap"}),

        # ── MAIN CHART ──────────────────────────────────────
        html.Div(
            dcc.Graph(
                id="chart",
                config={
                    "displayModeBar": True,
                    "scrollZoom": True,
                    "displaylogo": False,
                    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
                    "toImageButtonOptions": {"filename": "btc_ultra_dashboard"},
                },
                style={"height": "76vh"},
            ),
            style={
                "background": C["panel"],
                "border": f"1px solid {C['border']}",
                "borderRadius": "12px",
                "padding": "10px",
            },
        ),

        # ── FOOTER ──────────────────────────────────────────
        html.Div([
            html.Span(id="ts", style={"color": C["muted"], "fontSize": "10px"}),
            html.Span(
                "  ·  Data: Binance  ·  No financial advice",
                style={"color": C["muted"], "fontSize": "10px", "opacity": "0.5"},
            ),
        ], style={"textAlign": "right", "marginTop": "10px", "paddingRight": "4px"}),
    ],
)


# ─────────────────────────────────────────────────────────────
# CALLBACKS
# ─────────────────────────────────────────────────────────────
@app.callback(Output("eur", "data"), Input("tick", "n_intervals"))
def refresh_eur(n):
    return sources.fetch_eur_rate()


@app.callback(
    [
        Output("chart",    "figure"),
        Output("c-price",  "children"),
        Output("c-change", "children"),
        Output("c-signal", "children"),
        Output("c-rsi",    "children"),
        Output("c-crsi",   "children"),
        Output("c-vol",    "children"),
        Output("c-ma200",  "children"),
        Output("c-atr",    "children"),
        Output("c-volat",  "children"),
        Output("c-spread", "children"),
        Output("ts",       "children"),
    ],
    [
        Input("tick",         "n_intervals"),
        Input("currency-sel", "value"),
        Input("interval-dd",  "value"),
        Input("eur",          "data"),
    ],
)
def refresh_all(n, currency, interval, eur_rate):
    sym  = "€" if currency == "EUR" else "$"
    mult = eur_rate if currency == "EUR" else 1.0

    def colored(text, color="#e2e8f0"):
        return html.Span(str(text), style={"color": color})

    try:
        df     = sources.fetch_klines(SYMBOL, interval, limit=350)
        df     = compute_indicators(df)
        ticker = sources.fetch_ticker_24h(SYMBOL)
        fig    = build_chart(df, currency=currency, eur_rate=eur_rate)

        last   = df.iloc[-1]
        price  = last["close"] * mult
        rsi_v  = last["rsi"]
        crsi_v = last["crsi"]
        atr_v  = last["atr"]  * mult
        ma200v = last["ma200"] * mult
        sig    = int(last["signal"])
        vol252 = last["vol_252"]

        # 24h change
        change_pct = float(ticker.get("priceChangePercent", 0))
        vol_24h    = float(ticker.get("quoteVolume", 0)) * mult

        # Bollinger width
        bb_width = ((last["bb_upper"] - last["bb_lower"]) / last["bb_mid"]) * 100 * mult

        # Signal mapping
        sig_info = {
             2: ("🟢 STRONG BUY",  C["buy"]),
             1: ("🟡 BUY",         C["green"]),
             0: ("⚪ NEUTRAL",      C["muted"]),
            -1: ("🟠 SELL",        C["orange"]),
            -2: ("🔴 STRONG SELL", C["sell"]),
        }
        sig_label, sig_color = sig_info.get(sig, ("⚪ NEUTRAL", C["muted"]))

        # RSI label
        if rsi_v > 75:
            rsi_label, rsi_color = f"{rsi_v:.1f} OVERBOUGHT", C["red"]
        elif rsi_v < 25:
            rsi_label, rsi_color = f"{rsi_v:.1f} OVERSOLD", C["green"]
        else:
            rsi_label, rsi_color = f"{rsi_v:.1f}", C["blue"]

        # CRSI label
        if crsi_v > 80:
            crsi_label, crsi_color = f"{crsi_v:.1f} OB", C["red"]
        elif crsi_v < 20:
            crsi_label, crsi_color = f"{crsi_v:.1f} OS", C["green"]
        else:
            crsi_label, crsi_color = f"{crsi_v:.1f}", C["purple"]

        # vs MA200
        ma200_pct = ((last["close"] - last["ma200"]) / last["ma200"]) * 100
        ma200_str  = f"{'▲' if ma200_pct > 0 else '▼'} {abs(ma200_pct):.1f}%"
        ma200_clr  = C["green"] if ma200_pct > 0 else C["red"]

        vol_str  = f"{sym}{vol_24h / 1e9:.2f}B"
        volat_v  = f"{vol252:.1f}%" if not np.isnan(vol252) else "—"
        bb_str   = f"{bb_width:.2f}%"

        ts = (f"⟳ {datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}  "
              f"·  auto-refresh {REFRESH_MS // 1000}s")

        return (
            fig,
            colored(f"{sym}{price:,.0f}", C["text"]),
            colored(f"{'▲' if change_pct >= 0 else '▼'} {abs(change_pct):.2f}%",
                    C["green"] if change_pct >= 0 else C["red"]),
            colored(sig_label, sig_color),
            colored(rsi_label, rsi_color),
            colored(crsi_label, crsi_color),
            colored(vol_str, C["text"]),
            colored(ma200_str, ma200_clr),
            colored(f"{sym}{atr_v:,.0f}", C["orange"]),
            colored(volat_v, C["cyan"]),
            colored(bb_str, C["blue"]),
            ts,
        )

    except Exception as exc:
        empty = go.Figure()
        empty.update_layout(
            paper_bgcolor=C["bg"], plot_bgcolor=C["panel"],
            annotations=[dict(
                text=f"⚠ {exc}", showarrow=False,
                x=0.5, y=0.5, xref="paper", yref="paper",
                font=dict(color=C["red"], size=14),
            )],
        )
        blank = colored(f"Error", C["red"])
        return (empty, blank, blank, blank, blank, blank,
                blank, blank, blank, blank, blank, "Connection error")


# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("""
╔═══════════════════════════════════════════════════════════════╗
║        BTC ULTRA DASHBOARD  — Starting up …                   ║
╠═══════════════════════════════════════════════════════════════╣
║  → Open your browser at   http://127.0.0.1:8050               ║
║  → Press  Ctrl-C  to stop                                     ║
╚═══════════════════════════════════════════════════════════════╝
""")
    app.run(debug=False, host="0.0.0.0", port=8050)
