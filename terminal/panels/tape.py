"""
Panneau ruban : les gros ordres qui s'exécutent, et qui les a voulus.

Le pendant du carnet : le carnet montre des intentions, le ruban ce qui
s'est exécuté — chaque gros échange de Binance, Coinbase et Bybit au
comptant, avec le côté de celui qui a **agressé** (§ tape). Le titre dit
le flux agressif des cinq dernières minutes, toutes tailles confondues :
la part des achats, et où ils ont eu lieu ; et la prime Coinbase, l'écart
du bitcoin en dollars sur Coinbase à Binance en USDT. Coinbase est la place des
acheteurs américains ; qu'elle achète quand Binance vend se lit ici.

Le seuil se règle : 100 k$, 250 k$, 1 M$. Montants et prix suivent la
devise du panneau prix — c'est le marché du moment (§4.3).
"""

from __future__ import annotations

from datetime import datetime

from dash import Input, Output, dcc, html

from ..charts import build_tape_history
from ..fx import DOLLARS, display
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Seuils proposés, en dollars, et celui par défaut.
THRESHOLDS = {"100 k": 100_000, "250 k": 250_000, "1 M": 1_000_000}
DEFAULT_THRESHOLD = "250 k"
#: Fenêtre du flux agressif du titre.
FLOW_WINDOW = 300
#: Lignes du ruban.
ROWS = 40

EXCHANGE_TAG = {"Binance": "BIN", "Coinbase": "CBS", "Bybit": "BYB"}
SIDE_LABEL = {"buy": ("ACHAT", C["green"]), "sell": ("VENTE", C["red"])}


def layout(title=None):
    """Le titre ne garde que la part des achats et les liens absents : la
    cellule du carnet est étroite, et le détail du flux comme le seuil y
    débordaient de 180 px. Ils vont sur une ligne à eux, sous le titre —
    comme la comparaison des plateformes du panneau perpétuel."""
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Ruban"),
            html.Span(id="tape-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap"}),
        ], style=TITLE_STYLE),
        html.Div([
            html.Span(id="tape-flow", style={"overflow": "hidden"}),
            dcc.RadioItems(
                id="tape-threshold",
                options=[{"label": k, "value": k} for k in THRESHOLDS],
                value=DEFAULT_THRESHOLD, inline=True, className="tf-radio",
                style={"flexShrink": "0", "marginLeft": "8px"},
                persistence=True, persistence_type="local",
            ),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center", "fontFamily": MONO,
                  "fontSize": "9px", "padding": "0 6px 4px",
                  "whiteSpace": "nowrap"}),
        html.Div(id="tape-table", style={"flex": "1", "overflowY": "auto",
                                         "minHeight": "0"}),
        # Le ruban dans le temps : ce que le journal en garde.
        html.Div("gros ordres, solde par heure · prime Coinbase",
                 id="tape-history-title",
                 style={"color": C["muted"], "fontFamily": MONO,
                        "fontSize": "9px", "padding": "4px 6px 0",
                        "whiteSpace": "nowrap", "overflow": "hidden"}),
        dcc.Graph(id="tape-history",
                  style={"flex": "1", "minHeight": "0"},
                  config={"displayModeBar": False}),
    ], style=PANEL_STYLE)


#: Fenêtre de l'historique, en jours : la journée dans la grille, la
#: semaine en plein écran.
HISTORY_DAYS = 1
HISTORY_DAYS_MAX = 7


def render_history(hub, expanded: bool = False):
    """Le graphique du bas et sa légende : solde horaire des gros ordres et
    prime Coinbase, sur la fenêtre que la place permet."""
    days = HISTORY_DAYS_MAX if expanded else HISTORY_DAYS
    flow = hub.tape_flow_history(days)
    figure = build_tape_history(flow, hub.market_snapshots(days),
                                uirevision=f"tape-{days}")
    net = float(flow["net"].sum()) if not flow.empty else 0.0
    legende = [
        html.Span(f"{'7 j' if expanded else '24 h'} · ",
                  style={"color": C["muted"]}),
        html.Span("gros ordres ", style={"color": C["muted"]}),
        html.Span(("+" if net >= 0 else "−") + _montant(abs(net)),
                  style={"color": C["green"] if net >= 0 else C["red"]},
                  title="achats moins ventes des ordres de 100 k$ et plus "
                        "journalisés sur la fenêtre, en barres par heure"),
        html.Span(" · ", style={"color": C["muted"]}),
        html.Span("prime Coinbase", style={"color": C["yellow"]},
                  title="la ligne, à droite : Coinbase en dollars contre "
                        "Binance en USDT, un relevé toutes les cinq minutes"),
    ]
    return figure, legende


def _montant(valeur: float, unit: str = "$") -> str:
    if valeur >= 1e6:
        return f"{valeur / 1e6:.2f} M{unit}"
    return f"{valeur / 1e3:.0f} k{unit}"


def badges(tape):
    """Au titre : la part des achats agressifs sur cinq minutes, et les
    liens absents — ce qui doit se voir même cellule étroite."""
    manque = [name for name, (up, _) in tape.links.items() if not up]
    sans = ([html.Span(" · sans " + ", ".join(manque),
                       style={"color": C["red"]})] if manque else [])
    flow = tape.flow(FLOW_WINDOW)
    total = flow["buy"] + flow["sell"]
    if not total:
        return html.Span([html.Span("aucun échange reçu",
                                    style={"color": C["muted"]})] + sans,
                         style={"fontFamily": MONO})
    share = flow["buy"] / total * 100
    prime = tape.coinbase_premium(FLOW_WINDOW)
    prime_span = ([] if prime is None else [
        html.Span(" · ", style={"color": C["muted"]}),
        html.Span(f"prime CBS {prime * 100:+.2f} %",
                  style={"color": C["green"] if prime >= 0 else C["red"]},
                  title=("prime Coinbase : le bitcoin en dollars sur Coinbase "
                         "contre Binance en USDT, prix moyens pondérés de cinq "
                         "minutes. Positive, l'Amérique paie plus cher. Un "
                         "USDT décoté sur le dollar en fait une part : l'écart "
                         "ne se lit qu'au-delà de quelques centièmes.")),
    ])
    detail = " · ".join(
        f"{EXCHANGE_TAG.get(name, name)} "
        f"{sides['buy'] / (sides['buy'] + sides['sell']) * 100:.0f} %"
        for name, sides in sorted(flow["by_exchange"].items())
        if sides["buy"] + sides["sell"])
    return html.Span([
        html.Span(f"{share:.0f} % achats",
                  style={"color": C["green"] if share >= 50 else C["red"]},
                  title=("part des achats agressifs sur cinq minutes, par "
                         f"place : {detail}")),
    ] + prime_span + sans, style={"fontFamily": MONO})


def flow_line(tape, fx=DOLLARS):
    """Sous le titre : les montants agressifs des cinq dernières minutes."""
    flow = tape.flow(FLOW_WINDOW)
    if not flow["buy"] + flow["sell"]:
        return ""
    return [
        html.Span("5 min · ", style={"color": C["muted"]}),
        html.Span(f"{fx.warning}achats {_montant(flow['buy'] * fx.rate, fx.unit)}",
                  style={"color": C["green"]},
                  title=fx.title("achats agressifs, toutes tailles")),
        html.Span(" · ", style={"color": C["muted"]}),
        html.Span(f"ventes {_montant(flow['sell'] * fx.rate, fx.unit)}",
                  style={"color": C["red"]},
                  title=fx.title("ventes agressives, toutes tailles")),
    ]


def table(trades, fx=DOLLARS):
    """Les gros ordres, du plus récent au plus ancien ; ceux d'un million et
    plus en gras."""
    if not trades:
        return html.Div("aucun ordre au-dessus du seuil depuis une heure",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "10px", "padding": "8px"})
    cell = {"padding": "1px 6px", "whiteSpace": "nowrap"}
    rows = []
    for trade in trades:
        label, color = SIDE_LABEL[trade.side]
        weight = "700" if trade.notional >= 1_000_000 else "400"
        rows.append(html.Tr([
            html.Td(datetime.fromtimestamp(trade.time).strftime("%H:%M:%S"),
                    style={**cell, "color": C["muted"]}),
            html.Td(EXCHANGE_TAG.get(trade.exchange, trade.exchange[:3].upper()),
                    style={**cell, "color": C["muted"], "fontSize": "9px"},
                    title=trade.exchange),
            html.Td(label, style={**cell, "color": color, "fontWeight": "600"}),
            html.Td(f"{trade.quantity:.3f} BTC",
                    style={**cell, "color": C["text"], "textAlign": "right"}),
            html.Td(f"{trade.price * fx.rate:,.0f}",
                    style={**cell, "color": C["muted"], "textAlign": "right"}),
            html.Td(_montant(trade.notional * fx.rate, fx.unit),
                    style={**cell, "color": color, "textAlign": "right",
                           "fontWeight": weight}),
        ]))
    return html.Table(html.Tbody(rows), style={
        "width": "100%", "fontFamily": MONO, "fontSize": "10px",
        "borderCollapse": "collapse"})


def register(app, hub):
    @app.callback(
        Output("tape-table", "children"),
        Output("tape-badges", "children"),
        Output("tape-flow", "children"),
        Input("tick-slow", "n_intervals"),
        Input("tape-threshold", "value"),
        Input("currency", "data"),
    )
    def _refresh(_tick, threshold, currency):
        fx = display(hub, currency)
        minimum = THRESHOLDS.get(threshold, THRESHOLDS[DEFAULT_THRESHOLD])
        return (table(hub.tape.large(minimum, ROWS), fx),
                badges(hub.tape), flow_line(hub.tape, fx))

    @app.callback(
        Output("tape-history", "figure"),
        Output("tape-history-title", "children"),
        # Le journal avance par heure et par cinq minutes : l'horloge rare.
        Input("tick-rare", "n_intervals"),
        Input("expanded", "data"),
    )
    def _refresh_history(_tick, expanded):
        return render_history(hub, expanded == "tape")
