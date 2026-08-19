"""
Panneau carnet d'ordres et profondeur.

Panneau « rapide » : il lit les carnets tenus en mémoire par le hub, sans
aucun appel réseau. Le coût d'un rafraîchissement se limite donc à
sérialiser quelques dizaines de lignes.
"""

from __future__ import annotations

from dash import Input, Output, State, dcc, html

from ..charts import build_depth_chart
from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE

#: Dans la grille, le panneau ne peut afficher qu'une douzaine de lignes :
#: au-delà, les meilleures offres d'achat sortaient du cadre et seules les
#: ventes restaient visibles. En plein écran, la place ne manque plus.
DEPTH = 6
DEPTH_MAX = 20


def layout():
    return html.Div([
        html.Div([
            html.Span("Carnet"),
            # Libellés abrégés : les noms complets faisaient passer le
            # sélecteur à la ligne dans la largeur du panneau.
            dcc.RadioItems(
                id="book-exchange",
                options=[{"label": short, "value": name} for short, name in
                         (("BIN", "Binance"), ("KRK", "Kraken"), ("BYB", "Bybit"),
                          ("OKX", "OKX"), ("CBS", "Coinbase"))],
                value="Binance", inline=True, className="tf-radio",
                style={"fontSize": "9px", "whiteSpace": "nowrap"},
            ),
        ], style=TITLE_STYLE),
        html.Div(id="book-table", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def depth_layout():
    return html.Div([
        html.Div(html.Span("Profondeur comparée"), style=TITLE_STYLE),
        dcc.Graph(
            id="depth-chart",
            style={"flex": "1", "minHeight": "0"},
            config={"displayModeBar": False, "scrollZoom": True},
        ),
    ], style=PANEL_STYLE)


def _row(price, qty, ratio, color):
    """Ligne de carnet, avec une barre de fond proportionnelle au volume."""
    return html.Tr([
        html.Td(f"{price:,.2f}", style={"color": color, "textAlign": "right",
                                        "padding": "1px 6px"}),
        html.Td(f"{qty:.4f}", style={"color": C["muted"], "textAlign": "right",
                                     "padding": "1px 6px"}),
        html.Td(style={
            "background": f"linear-gradient(to right, {color}22 {ratio*100:.0f}%,"
                          f" transparent {ratio*100:.0f}%)",
            "width": "45%",
        }),
    ])


def register(app, hub):
    @app.callback(
        Output("book-table", "children"),
        Input("tick-fast", "n_intervals"),
        Input("book-exchange", "value"),
        State("maximized", "data"),
    )
    def _refresh_book(_tick, exchange, maximized):
        book = hub.books[exchange]
        depth = DEPTH_MAX if maximized == "book" else DEPTH
        bids = book.top("bids", depth)
        asks = book.top("asks", depth)

        if not bids or not asks:
            message = book.error or "connexion en cours…"
            return html.Div(message, style={"color": C["muted"], "fontFamily": MONO,
                                            "fontSize": "11px", "padding": "12px"})

        largest = max((q for _, q in bids + asks), default=1) or 1
        spread = book.spread or 0
        spread_pct = book.spread_pct or 0

        return html.Table([
            html.Tbody(
                [_row(p, q, q / largest, C["red"]) for p, q in reversed(asks)]
                + [html.Tr(html.Td(
                    f"spread {spread:,.2f} $ · {spread_pct:.4f} % · "
                    f"{book.age_ms:.0f} ms",
                    colSpan=3,
                    style={"color": C["yellow"], "textAlign": "center",
                           "fontSize": "10px", "padding": "3px",
                           "borderTop": f"1px solid {C['border']}",
                           "borderBottom": f"1px solid {C['border']}"},
                ))]
                + [_row(p, q, q / largest, C["green"]) for p, q in bids]
            )
        ], style=TABLE_STYLE)

    @app.callback(
        Output("depth-chart", "figure"),
        Input("tick-fast", "n_intervals"),
    )
    def _refresh_depth(_tick):
        return build_depth_chart(hub.books)
