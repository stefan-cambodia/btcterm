"""Panneau prix : chandeliers, indicateurs, profil de volume."""

from __future__ import annotations

from dash import Input, Output, dcc, html

from ..charts import build_price_chart, prepare_price_frame
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

INTERVALS = {"1H": "1h", "4H": "4h", "1D": "1d", "1W": "1w"}

_BTN = {
    "background": "transparent", "color": C["muted"],
    "border": f"1px solid {C['border']}", "borderRadius": "3px",
    "padding": "2px 9px", "cursor": "pointer", "fontSize": "10px",
    "fontFamily": MONO, "marginLeft": "3px",
}


def layout():
    return html.Div([
        html.Div([
            html.Span("BTC/USDT · prix & indicateurs"),
            html.Div([
                dcc.RadioItems(
                    id="price-interval",
                    options=[{"label": k, "value": v} for k, v in INTERVALS.items()],
                    value="1d", inline=True, className="tf-radio",
                    style={"display": "inline-block", "fontSize": "10px"},
                ),
                dcc.RadioItems(
                    id="price-currency",
                    options=[{"label": "USD", "value": "USD"},
                             {"label": "EUR", "value": "EUR"}],
                    value="USD", inline=True, className="tf-radio",
                    style={"display": "inline-block", "fontSize": "10px",
                           "marginLeft": "14px"},
                ),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style=TITLE_STYLE),
        dcc.Graph(
            id="price-chart",
            style={"flex": "1", "minHeight": "0"},
            config={
                "scrollZoom": True,
                "displaylogo": False,
                "modeBarButtonsToRemove": ["select2d", "lasso2d"],
            },
        ),
    ], style=PANEL_STYLE)


def register(app, hub):
    @app.callback(
        Output("price-chart", "figure"),
        Input("tick-slow", "n_intervals"),
        Input("price-interval", "value"),
        Input("price-currency", "value"),
    )
    def _refresh(_tick, interval, currency):
        df = prepare_price_frame(hub.klines(interval, limit=350))
        rate = hub.eur_rate() if currency == "EUR" else 1.0
        # La clé de révision inclut intervalle et devise : changer l'un des
        # deux doit recadrer, un simple rafraîchissement ne doit pas.
        return build_price_chart(df, currency, rate, uirevision=f"{interval}:{currency}")
