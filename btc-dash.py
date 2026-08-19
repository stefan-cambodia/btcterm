#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════╗
║        BTC ULTRA DASHBOARD  —  Real-Time Bitcoin Monitor      ║
║  Signals • MA200 • RSI • CRSI • Volume Profile • Volatility   ║
╚═══════════════════════════════════════════════════════════════╝

REMPLACÉ PAR LE TERMINAL — voir `python -m terminal.app`, dont le
panneau prix reprend ce graphique à l'identique au sein d'une interface
unifiée. Ce fichier ne subsiste que le temps de la transition et se
contente d'appeler `terminal.charts` ; il disparaîtra à l'étape 4 de la
feuille de route.

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

import dash
from dash import dcc, html, Input, Output
import dash_bootstrap_components as dbc

from btcterm import sources
from terminal.charts import build_price_chart, prepare_price_frame
from terminal.theme import C


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
SYMBOL       = "BTCUSDT"
REFRESH_MS   = 10_000            # chart refresh interval (ms)

INTERVALS = {
    "1H":  "1h",
    "4H":  "4h",
    "1D":  "1d",
    "1W":  "1w",
}

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
        df     = prepare_price_frame(df)
        ticker = sources.fetch_ticker_24h(SYMBOL)
        fig    = build_price_chart(df, currency=currency, eur_rate=eur_rate)

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
