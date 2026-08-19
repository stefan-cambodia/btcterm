"""Panneau prix : chandeliers, indicateurs, profil de volume."""

from __future__ import annotations

from dash import Input, Output, dcc, html

from ..charts import build_price_chart, prepare_price_frame
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Intervalles proposés, chacun avec le nombre de bougies chargées.
#: La palette large — de la bougie de quinze minutes à la mensuelle —
#: vient de `btc_dashboard2.py`, dont c'était le seul apport sur le
#: panneau prix. Les profondeurs d'historique suivent l'échelle : de quoi
#: nourrir la MA 200 en intraday, sans tirer trente ans de mensuelles.
INTERVALS = {
    "15m": 300, "30m": 300, "1h": 350, "4h": 350, "6h": 300,
    "12h": 300, "1d": 365, "1w": 260, "1M": 120,
}
DEFAULT_INTERVAL = "1d"

#: Sous-graphiques et profil de volume, décochables. Le cours récupère la
#: hauteur libérée : c'est lui qu'on vient lire en séance d'analyse.
EXTRAS = [
    {"label": "RSI", "value": "rsi"},
    {"label": "CRSI", "value": "crsi"},
    {"label": "VOL", "value": "volume"},
    {"label": "PROFIL", "value": "profile"},
]
DEFAULT_EXTRAS = ["rsi", "volume", "profile"]

_BTN = {
    "background": "transparent", "color": C["muted"],
    "border": f"1px solid {C['border']}", "borderRadius": "3px",
    "padding": "2px 9px", "cursor": "pointer", "fontSize": "10px",
    "fontFamily": MONO, "marginLeft": "3px",
}


def layout(title=None):
    return html.Div([
        html.Div([
            # Titre court : la barre porte neuf intervalles, la devise,
            # l'échelle et quatre sous-graphiques ; un intitulé plus long
            # les faisait passer à la ligne dans la largeur de la grille.
            title if title is not None else
            html.Span("BTC/USDT", style={"fontSize": "9px",
                                         "letterSpacing": "0.02em",
                                         "whiteSpace": "nowrap"}),
            html.Div([
                dcc.RadioItems(
                    id="price-interval",
                    options=[{"label": k, "value": k} for k in INTERVALS],
                    value=DEFAULT_INTERVAL, inline=True, className="tf-radio",
                    style={"display": "inline-block", "fontSize": "9px"},
                ),
                dcc.RadioItems(
                    id="price-currency",
                    # Symboles plutôt que codes : trois lettres par devise
                    # faisaient passer la barre de titre à la ligne.
                    options=[{"label": "$", "value": "USD"},
                             {"label": "€", "value": "EUR"}],
                    value="USD", inline=True, className="tf-radio",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "10px"},
                ),
                dcc.Checklist(
                    id="price-scale",
                    options=[{"label": "LOG", "value": "log"}],
                    value=[], inline=True, className="tf-check",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "10px"},
                ),
                dcc.Checklist(
                    id="price-extras",
                    options=EXTRAS, value=DEFAULT_EXTRAS,
                    inline=True, className="tf-check",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "10px"},
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap"}),
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
        Input("price-scale", "value"),
        Input("price-extras", "value"),
        Input("maximized", "data"),
    )
    def _refresh(_tick, interval, currency, scale, extras, maximized):
        extras = extras or []
        log_scale = "log" in (scale or [])
        interval = interval if interval in INTERVALS else DEFAULT_INTERVAL
        df = prepare_price_frame(hub.klines(interval, limit=INTERVALS[interval]))
        rate = hub.eur_rate() if currency == "EUR" else 1.0

        # La clé de révision décrit la série et la structure du graphique :
        # elle ne change pas au rafraîchissement, ni au passage en plein
        # écran — le zoom en cours survit donc à l'agrandissement — mais
        # change quand le contenu affiché change, ce qui recadre à propos.
        revision = (f"{interval}:{currency}:{'log' if log_scale else 'lin'}"
                    f":{','.join(sorted(extras))}")

        return build_price_chart(
            df, currency, rate,
            uirevision=revision,
            subpanels=tuple(e for e in extras if e != "profile"),
            profile="profile" in extras,
            maximized=(maximized == "price"),
            log_scale=log_scale,
        )
