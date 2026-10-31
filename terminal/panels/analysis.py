"""
Panneau analyse : où en est le marché, et ce que valent les signaux.

Deux tableaux côte à côte. À gauche, chaque intervalle d'un coup d'œil, ce
qu'il faudrait sinon aller lire graphique par graphique. À droite, le bilan
des signaux du panneau prix sur l'historique sauvegardé : les flèches du
graphique n'avaient jamais été mises à l'épreuve.

Les calculs sont dans `btcterm.analysis`, et leurs réserves aussi : le bilan
décrit le passé, sans frais, et ses fenêtres se recouvrent. Le panneau les
rappelle à l'écran, en face des chiffres.

L'horloge est la sienne : la rare (5 min) laisserait vieillir le RSI du quart
d'heure, la lente (2 s) demanderait cinq fins de série pour un tableau qui
ne change pas si vite. Elle ne tourne que panneau affiché.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from btcterm.analysis import MIN_SAMPLE, timeframe_summary

from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE
from .price import INTERVALS

#: Intervalles du tableau de gauche.
SUMMARY_INTERVALS = ("15m", "1h", "4h", "1d", "1w")

#: Intervalles du bilan : en deçà de l'heure, l'historique relu est court.
STATS_INTERVALS = ("1h", "4h", "1d", "1w")

REFRESH_MS = 30_000

GRADE_LABEL = {2: "achat fort", 1: "achat", -1: "vente", -2: "vente forte"}
TREND_LABEL = {"up": ("haussier", "green"), "down": ("baissier", "red"),
               "mixed": ("mixte", "yellow")}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Analyse"),
            html.Div([
                html.Span("bilan des signaux",
                          style={"color": C["muted"], "fontSize": "9px"}),
                dcc.RadioItems(
                    id="analysis-interval",
                    options=[{"label": k, "value": k} for k in STATS_INTERVALS],
                    value="1d", inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px"},
                ),
                html.A("CSV", id="analysis-export", href="/api/export?interval=1d",
                       title="exporter l'historique sauvegardé de cet intervalle",
                       style={"color": C["cyan"], "fontSize": "9px",
                              "marginLeft": "10px", "textDecoration": "none"}),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap"}),
        ], style=TITLE_STYLE),
        dcc.Interval(id="analysis-tick", interval=REFRESH_MS),
        html.Div([
            html.Div(id="analysis-summary", style={"flex": "0 0 38%"}),
            html.Div(id="analysis-stats", style={"flex": "1", "minWidth": "0"}),
        ], style={"display": "flex", "gap": "18px", "flex": "1",
                  "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _header(*labels):
    return html.Thead(html.Tr([
        html.Th(label, style={"color": C["muted"], "fontSize": "9px",
                              "textAlign": "left" if index == 0 else "right",
                              "padding": "2px 6px", "fontWeight": "400"})
        for index, label in enumerate(labels)
    ]))


def _cell(text, color=None, align="right"):
    return html.Td(text, style={"color": C[color or "text"], "textAlign": align,
                                "padding": "1px 6px", "whiteSpace": "nowrap"})


def _summary_row(interval: str, state: dict | None):
    if state is None:
        return html.Tr([_cell(interval, "muted", "left"),
                        _cell("historique insuffisant", "muted"),
                        _cell(""), _cell(""), _cell("")])
    label, color = TREND_LABEL[state["trend"]]
    rsi = state["rsi"]
    if state["signal"]:
        signal = (f"{GRADE_LABEL[state['signal']]} "
                  f"· il y a {state['signal_age']} b.")
        tone = "green" if state["signal"] > 0 else "red"
    else:
        signal, tone = "—", "muted"
    return html.Tr([
        _cell(interval, "muted", "left"),
        _cell(label, color),
        _cell(f"{state['gap']:+.1f} %", "green" if state["gap"] >= 0 else "red"),
        _cell(f"{rsi:.0f}", "red" if rsi >= 70 else "green" if rsi <= 30 else None),
        _cell(signal, tone),
    ])


def render_summary(hub):
    """Le tableau des intervalles, à leur dernière bougie."""
    rows = [_summary_row(interval, timeframe_summary(
                hub.klines(interval, limit=INTERVALS[interval])))
            for interval in SUMMARY_INTERVALS]
    return html.Table([_header("", "tendance", "MA 200", "RSI", "dernier signal"),
                       html.Tbody(rows)], style=TABLE_STYLE)


def _stats_cells(cell: dict | None):
    if cell is None:
        return [_cell("—", "muted"), _cell("—", "muted"), _cell("—", "muted")]
    # Sous l'effectif utile, le chiffre reste affiché mais éteint : le lire
    # en vert ou en rouge lui prêterait une assurance qu'il n'a pas.
    solid = cell["count"] >= MIN_SAMPLE
    edge = "green" if cell["edge"] > 0 else "red"
    return [
        _cell(f"{cell['mean']:+.2f} %", None if solid else "muted"),
        _cell(f"{cell['hit']:.0f} %", None if solid else "muted"),
        _cell(f"{cell['edge']:+.2f} %", edge if solid else "muted"),
    ]


def render_stats(stats: dict | None):
    """Le bilan d'un intervalle, ses réserves écrites en dessous."""
    if stats is None:
        return html.Div("historique insuffisant pour ce bilan",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"})
    horizons = stats["horizons"]
    labels = ["signal", "n"]
    for horizon in horizons:
        labels += [f"{horizon} b. moy.", "réussite", "écart"]
    rows = [
        html.Tr([
            _cell(GRADE_LABEL[row["grade"]],
                  "green" if row["grade"] > 0 else "red", "left"),
            _cell(str(row["count"]),
                  None if row["count"] >= MIN_SAMPLE else "muted"),
            *[td for horizon in horizons
              for td in _stats_cells(row["cells"][horizon])],
        ])
        for row in stats["rows"]
    ]
    base = " · ".join(f"{stats['base'][h]:+.2f} % à {h} bougies"
                      for h in horizons)
    note = (f"{stats['bars']:,} bougies closes, du {stats['first']:%d/%m/%Y} au "
            f"{stats['last']:%d/%m/%Y} · rester investi : {base} · écart : avance "
            f"sur cette référence, dans le sens du signal · entrée à l'ouverture "
            f"suivante, sans frais · n < {MIN_SAMPLE} en gris · le passé ne "
            f"promet rien")
    return html.Div([
        html.Table([_header(*labels), html.Tbody(rows)], style=TABLE_STYLE),
        html.Div(note, style={"color": C["muted"], "fontFamily": MONO,
                              "fontSize": "9px", "padding": "6px"}),
    ])


def register(app, hub):
    @app.callback(
        Output("analysis-summary", "children"),
        Input("analysis-tick", "n_intervals"),
    )
    def _summary(_tick):
        return render_summary(hub)

    @app.callback(
        Output("analysis-stats", "children"),
        Output("analysis-export", "href"),
        Input("analysis-interval", "value"),
        Input("analysis-tick", "n_intervals"),
    )
    def _stats(interval, _tick):
        return (render_stats(hub.signal_stats(interval)),
                f"/api/export?interval={interval}")
