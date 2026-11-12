"""
Panneau marchés : le bitcoin face aux actions, à l'or, au dollar et aux taux.

À gauche, le graphique, en deux vues — **PERF**, la performance comparée sur
la fenêtre choisie, chaque série ramenée à 100 ; **CORR**, la corrélation
glissante des variations quotidiennes sur trente séances. À droite, le
tableau : dernier cours, variation sur une séance et sur un mois,
corrélation au bitcoin sur 30 et 90 séances, pour les cinq marchés — le
Nasdaq et le taux à 10 ans n'y sont qu'au tableau (`btcterm.markets`).

Rien ne s'y convertit en euros : ce sont des indices, des cours et un taux
cotés dans leurs propres unités.

Source : Yahoo Finance, sans clé.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from btcterm import markets as mkt

from ..charts import (placeholder, MARKETS_DEFAULT_WINDOW, MARKETS_VIEWS, MARKETS_WINDOWS,
                      build_markets_chart)
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

_CELL = {"padding": "2px 6px", "textAlign": "right", "whiteSpace": "nowrap"}
_HEAD = {**_CELL, "color": C["muted"], "fontWeight": "400",
         "borderBottom": f"1px solid {C['border']}"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Marchés"),
            html.Div([
                html.Span(id="markets-badges", style={"fontSize": "9px"}),
                dcc.RadioItems(
                    id="markets-window",
                    options=[{"label": k, "value": k} for k in MARKETS_WINDOWS],
                    value=MARKETS_DEFAULT_WINDOW, inline=True,
                    className="tf-radio", style={"marginLeft": "8px"},
                    persistence=True, persistence_type="local",
                ),
                dcc.RadioItems(
                    id="markets-view",
                    options=[{"label": k, "value": k} for k in MARKETS_VIEWS],
                    value="PERF", inline=True, className="tf-radio",
                    style={"marginLeft": "8px"},
                    persistence=True, persistence_type="local",
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap", "overflow": "hidden"}),
        ], style=TITLE_STYLE),
        html.Div([
            dcc.Graph(id="markets-chart", figure=placeholder(),
                      style={"flex": "1", "minWidth": "0", "minHeight": "0"},
                      config={"displaylogo": False, "scrollZoom": True,
                              "modeBarButtonsToRemove": ["select2d",
                                                         "lasso2d"]}),
            html.Div(id="markets-table",
                     style={"flexShrink": "0", "overflow": "auto",
                            "marginLeft": "8px"}),
        ], style={"display": "flex", "flex": "1", "minHeight": "0"}),
    ], style=PANEL_STYLE)


def _signed(value, kind: str):
    """Une variation : en % pour un prix, en points de base pour un taux."""
    if value is None:
        return html.Td("—", style={**_CELL, "color": C["muted"]})
    text = (f"{value * 100:+z.0f} pb" if kind == "rate"
            else f"{value:+z.2f} %")
    color = C["green"] if value > 0 else C["red"] if value < 0 else C["muted"]
    return html.Td(text, style={**_CELL, "color": color})


def _corr(value):
    if value is None:
        return html.Td("—", style={**_CELL, "color": C["muted"]})
    # L'intensité dit la force du lien, pas son sens : le signe est écrit.
    strong = abs(value) >= 0.5
    return html.Td(f"{value:+z.2f}", style={
        **_CELL, "color": C["text"] if strong else C["muted"],
        "fontWeight": "600" if strong else "400"})


def _last(row):
    value = row["last"]
    if value is None:
        return "indisponible"
    if row["kind"] == "rate":
        return f"{value:.2f} %"
    return f"{value:,.2f}" if value < 1000 else f"{value:,.0f}"


def table(rows: list[dict]):
    """Le tableau des marchés ; une ligne vide dit une place muette."""
    head = html.Tr([html.Th(text, style={**_HEAD, "textAlign": align})
                    for text, align in (("marché", "left"), ("dernier", "right"),
                                        ("1 j", "right"), ("1 mois", "right"),
                                        ("corr 30", "right"),
                                        ("corr 90", "right"))])
    body = [
        html.Tr([
            html.Td(row["label"], style={**_CELL, "textAlign": "left",
                                         "color": C["text"]}),
            html.Td(_last(row), style={**_CELL, "color": C["text"]
                                       if row["last"] is not None
                                       else C["muted"]}),
            _signed(row["day"], row["kind"]),
            _signed(row["month"], row["kind"]),
            _corr(row["corr30"]),
            _corr(row["corr90"]),
        ])
        for row in rows
    ]
    return html.Table([html.Thead(head), html.Tbody(body)],
                      style={"fontFamily": MONO, "fontSize": "10px",
                             "borderCollapse": "collapse"},
                      title="corr : corrélation des variations quotidiennes "
                            "au bitcoin, sur 30 et 90 séances")


def badges(rows: list[dict]):
    """La corrélation au S&P 500 et au dollar, d'un coup d'œil."""
    by_key = {row["key"]: row for row in rows}
    parts = []
    for key, short in (("spx", "S&P"), ("gold", "or"), ("dxy", "DXY")):
        value = (by_key.get(key) or {}).get("corr30")
        if value is not None:
            if parts:
                parts.append(html.Span(" · ", style={"color": C["muted"]}))
            # « corr. » et non ρ : la barre de titre passe en majuscules,
            # et le Ρ majuscule se lit P.
            parts += [html.Span(f"corr. {short} ", style={"color": C["muted"]}),
                      html.Span(f"{value:+z.2f}", style={"color": C["text"]})]
    if not parts:
        return html.Span("marchés indisponibles", style={"color": C["muted"]})
    return html.Span(parts, style={"fontFamily": MONO},
                     title="corrélation au bitcoin sur 30 séances")


def register(app, hub):
    @app.callback(
        Output("markets-chart", "figure"),
        Output("markets-table", "children"),
        Output("markets-badges", "children"),
        Input("tick-rare", "n_intervals"),
        Input("markets-view", "value"),
        Input("markets-window", "value"),
        Input("expanded", "data"),
    )
    def _refresh(_tick, view, window, expanded):
        maximized = expanded == "markets"
        view = view if view in MARKETS_VIEWS else "PERF"
        window = window if window in MARKETS_WINDOWS else MARKETS_DEFAULT_WINDOW
        btc = hub.btc_daily()
        frames = hub.markets()
        rows = mkt.snapshot(btc, frames)
        # La révision suit la vue, la fenêtre et l'agrandissement : en
        # changer recadre, le tour d'horloge non.
        figure = build_markets_chart(
            view, btc, frames, window, maximized=maximized,
            uirevision=f"markets:{view}:{window}:{maximized}")
        return figure, table(rows), badges(rows)
