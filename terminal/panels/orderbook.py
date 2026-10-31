"""
Panneau carnet d'ordres et profondeur.

Panneau « rapide » : il lit les carnets tenus en mémoire par le hub, sans
aucun appel réseau — le taux de change compris (`eur_rate_cached`). Le
coût d'un rafraîchissement se limite donc à sérialiser quelques dizaines
de lignes.

Le carnet suit la devise du panneau prix. La profondeur n'en a pas : elle
s'exprime en écart relatif et en bitcoins.
"""

from __future__ import annotations

from dash import Input, Output, State, dcc, html

from ..charts import build_depth_chart
from ..fx import display
from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE

#: Le carnet et la profondeur partagent désormais une cellule haute de
#: deux rangées, au lieu d'une chacun : la place regagnée revient aux
#: niveaux affichés. Au-delà, les meilleures offres d'achat sortent du
#: cadre et seules les ventes restent visibles. En plein écran, la place
#: ne manque plus du tout.
DEPTH = 8
DEPTH_MAX = 20


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Carnet"),
            # Libellés abrégés : les noms complets faisaient passer le
            # sélecteur à la ligne dans la largeur du panneau.
            dcc.RadioItems(
                id="book-exchange",
                options=[{"label": short, "value": name} for short, name in
                         (("BIN", "Binance"), ("KRK", "Kraken"), ("BYB", "Bybit"),
                          ("OKX", "OKX"), ("CBS", "Coinbase"))],
                value="Binance", inline=True, className="tf-radio",
                # La plateforme choisie survit au rechargement et au
                # passage par l'onglet profondeur, qui reconstruit ce
                # layout à ses défauts.
                persistence=True, persistence_type="local",
                style={"fontSize": "9px", "whiteSpace": "nowrap"},
            ),
        ], style=TITLE_STYLE),
        html.Div(id="book-table", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def depth_layout(title=None):
    return html.Div([
        html.Div(title if title is not None else
                 html.Span("Profondeur comparée"), style=TITLE_STYLE),
        dcc.Graph(
            id="depth-chart",
            style={"flex": "1", "minHeight": "0"},
            config={"displayModeBar": False, "scrollZoom": True},
        ),
    ], style=PANEL_STYLE)


def _row(price, qty, ratio, color, decimals=2):
    """Ligne de carnet, avec une barre de fond proportionnelle au volume."""
    return html.Tr([
        html.Td(f"{price:,.{decimals}f}",
                style={"color": color, "textAlign": "right",
                       "padding": "1px 6px"}),
        html.Td(f"{qty:.4f}", style={"color": C["muted"], "textAlign": "right",
                                     "padding": "1px 6px"}),
        html.Td(style={
            "background": f"linear-gradient(to right, {color}22 {ratio*100:.0f}%,"
                          f" transparent {ratio*100:.0f}%)",
            "width": "45%",
        }),
    ])


def render_book(hub, exchange: str, expanded: bool,
                currency: str | None = None):
    """Le carnet d'une plateforme, prêt à poser dans `book-table`.

    Fonction pure vis-à-vis du rendu : le callback Dash et le pousseur
    WebSocket l'appellent tous deux — un seul code décide de ce que le
    panneau montre, quel que soit le canal qui l'amène au navigateur.

    `currency` est la devise du panneau prix. Les carnets restent cotés en
    dollars en mémoire : seul l'affichage est converti, comme au bandeau.
    Un taux de secours se dit par un ⚠ sur la ligne du spread.
    """
    fx = display(hub, currency)
    # Un tick de 0,01 $ vaut moins de 0,01 € : à deux décimales, des niveaux
    # voisins se confondaient, meilleur achat et meilleure vente compris,
    # et le carnet avait l'air croisé.
    decimals = 2 if fx.rate == 1 else 3
    book = hub.books[exchange]
    depth = DEPTH_MAX if expanded else DEPTH
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
            [_row(p * fx.rate, q, q / largest, C["red"], decimals)
             for p, q in reversed(asks)]
            + [html.Tr(html.Td(
                f"{fx.warning}spread {spread * fx.rate:,.2f} {fx.unit} · "
                f"{spread_pct:.4f} % · {book.age_ms:.0f} ms",
                colSpan=3,
                title=fx.note,
                style={"color": C["yellow"], "textAlign": "center",
                       "fontSize": "10px", "padding": "3px",
                       "borderTop": f"1px solid {C['border']}",
                       "borderBottom": f"1px solid {C['border']}"},
            ))]
            + [_row(p * fx.rate, q, q / largest, C["green"], decimals)
               for p, q in bids]
        )
    ], style=TABLE_STYLE)


def render_depth(hub):
    """La profondeur comparée, prête à poser dans `depth-chart`."""
    return build_depth_chart(hub.books)


def register(app, hub):
    @app.callback(
        Output("book-table", "children"),
        Input("tick-fast", "n_intervals"),
        Input("book-exchange", "value"),
        # Le Store global plutôt que le sélecteur, qui quitte la page avec
        # le panneau prix. En Input : la bascule $/€ se voit tout de suite.
        Input("currency", "data"),
        # `expanded` désigne le panneau agrandi, plus sa cellule : depuis
        # la disposition configurable, le carnet peut vivre ailleurs que
        # dans sa cellule d'origine.
        State("expanded", "data"),
    )
    def _refresh_book(_tick, exchange, currency, expanded):
        return render_book(hub, exchange, expanded == "book", currency)

    @app.callback(
        Output("depth-chart", "figure"),
        Input("tick-fast", "n_intervals"),
    )
    def _refresh_depth(_tick):
        return render_depth(hub)
