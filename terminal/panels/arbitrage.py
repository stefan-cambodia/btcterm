"""
Panneau arbitrage : écarts exploitables entre plateformes.

Panneau « rapide », alimenté par le moteur du hub. Les montants affichés
sont nets des frais maker des deux plateformes ; ils ne tiennent compte
ni des frais de transfert, ni du temps d'exécution.
"""

from __future__ import annotations

from dash import Input, Output, html

from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE

ROWS = 8


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Arbitrage"),
            html.Span(id="arb-count", style={"color": C["muted"], "fontSize": "9px"}),
        ], style=TITLE_STYLE),
        html.Div(id="arb-table", style={"flex": "1", "overflow": "hidden"}),
    ], style=PANEL_STYLE)


def render(hub):
    """Table et badge de l'arbitrage, prêts à poser dans le panneau.

    Fonction pure vis-à-vis du rendu, partagée entre le callback Dash et
    le pousseur WebSocket.
    """
    opportunities = hub.engine.scan()
    total = hub.engine.stats["total_opportunities"]
    badge = f"{total} détectées · {hub.connected_count}/5 connectés"

    if not opportunities:
        return html.Div("en attente des carnets…",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"}), badge

    header = html.Thead(html.Tr([
        html.Th(label, style={"color": C["muted"], "fontSize": "9px",
                              "textAlign": align, "padding": "2px 6px",
                              "fontWeight": "400"})
        for label, align in (("Acheter", "left"), ("Vendre", "left"),
                             ("Brut %", "right"), ("Net %", "right"))
    ]))

    rows = []
    for opportunity in opportunities[:ROWS]:
        color = C["green"] if opportunity.is_profitable else C["muted"]
        rows.append(html.Tr([
            html.Td(f"{opportunity.buy_exchange} {opportunity.buy_price:,.2f}",
                    style={"padding": "1px 6px"}),
            html.Td(f"{opportunity.sell_exchange} {opportunity.sell_price:,.2f}",
                    style={"padding": "1px 6px"}),
            html.Td(f"{opportunity.gross_profit_pct:+.3f}",
                    style={"textAlign": "right", "color": C["muted"],
                           "padding": "1px 6px"}),
            html.Td(f"{opportunity.net_profit_pct:+.3f}",
                    style={"textAlign": "right", "color": color,
                           "fontWeight": "600", "padding": "1px 6px"}),
        ]))

    return html.Table([header, html.Tbody(rows)], style=TABLE_STYLE), badge


def register(app, hub):
    @app.callback(
        Output("arb-table", "children"),
        Output("arb-count", "children"),
        Input("tick-fast", "n_intervals"),
    )
    def _refresh(_tick):
        return render(hub)
