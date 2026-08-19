"""
Panneau macro : le cours face à la masse monétaire.

La thèse est banale et vaut d'être regardée plutôt que crue : le Bitcoin
suivrait la liquidité en dollars, avec un ou deux trimestres de retard.
Le panneau met les deux séries côte à côte, laisse choisir ce décalage,
et affiche deux corrélations dont la seconde est la seule intéressante
(voir `macro_stats`).

La donnée est mensuelle et paraît avec deux mois de retard : ce panneau
vit sur l'horloge rare, et la ligne M2 s'arrête avant celle du cours.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from ..charts import build_macro_chart, macro_stats, prepare_macro_frame
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Fenêtres d'observation, en mois. Binance ne cote le Bitcoin que depuis
#: 2017 : au-delà de dix ans, il n'y aurait plus de bougies à montrer.
WINDOWS = {"2A": 24, "5A": 60, "10A": 120}
DEFAULT_WINDOW = "5A"

#: Décalage appliqué à M2, en mois — l'hypothèse d'un cours qui réagit à
#: la liquidité avec un trimestre de retard se vérifie en le bougeant.
LAGS = (0, 1, 2, 3)


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else
            html.Span("BTC × masse monétaire M2 (US)"),
            html.Div([
                html.Span(id="macro-stats", style={"fontSize": "10px",
                                                   "marginRight": "14px"}),
                dcc.RadioItems(
                    id="macro-window",
                    options=[{"label": k, "value": k} for k in WINDOWS],
                    value=DEFAULT_WINDOW, inline=True, className="tf-radio",
                    style={"display": "inline-block", "fontSize": "9px"},
                ),
                dcc.RadioItems(
                    id="macro-lag",
                    options=[{"label": f"+{lag}M" if lag else "0", "value": lag}
                             for lag in LAGS],
                    value=0, inline=True, className="tf-radio",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "12px"},
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap"}),
        ], style=TITLE_STYLE),
        dcc.Graph(
            id="macro-chart",
            style={"flex": "1", "minHeight": "0"},
            config={"scrollZoom": True, "displaylogo": False,
                    "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
        ),
    ], style=PANEL_STYLE)


def _stats_badge(stats: dict):
    """Les deux corrélations et la croissance annuelle de M2."""
    if stats["niveaux"] is None:
        return html.Span("historique insuffisant", style={"color": C["muted"]})

    def colored(value: float):
        color = (C["green"] if value >= 0.5 else
                 C["red"] if value <= -0.2 else C["muted"])
        return html.Span(f"{value:+.2f}", style={"color": color})

    children = [
        html.Span("r niveaux ", style={"color": C["muted"]}), colored(stats["niveaux"]),
        html.Span(" · r variations 3M ", style={"color": C["muted"]}),
        colored(stats["variations"]),
    ]
    if stats["m2_yoy"] is not None:
        children += [
            html.Span(" · M2 ", style={"color": C["muted"]}),
            html.Span(f"{stats['m2_yoy']:+.1f} % / an",
                      style={"color": C["cyan"]}),
        ]
    return html.Span(children, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("macro-chart", "figure"),
        Output("macro-stats", "children"),
        Input("tick-rare", "n_intervals"),
        Input("macro-window", "value"),
        Input("macro-lag", "value"),
        Input("maximized", "data"),
    )
    def _refresh(_tick, window, lag, maximized):
        months = WINDOWS.get(window, WINDOWS[DEFAULT_WINDOW])
        lag = lag if lag in LAGS else 0

        frame = prepare_macro_frame(
            hub.klines("1M", limit=120), hub.m2_supply(), lag_months=lag
        ).tail(months)

        # La révision suit la fenêtre et le décalage : changer l'un ou
        # l'autre doit recadrer, un rafraîchissement mensuel non.
        return (
            build_macro_chart(frame, uirevision=f"{window}:{lag}",
                              maximized=(maximized == "macro")),
            _stats_badge(macro_stats(frame)),
        )
