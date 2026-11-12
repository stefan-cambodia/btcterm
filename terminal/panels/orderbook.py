"""
Panneau carnet d'ordres et profondeur.

Panneau « rapide » : il lit les carnets tenus en mémoire par le hub, sans
aucun appel réseau — le taux de change compris (`eur_rate_cached`).

Le carnet n'est pas rendu par Dash. Le serveur envoie des données déjà
formatées (`book_payload`) et `assets/book.js` les écrit dans le tableau, en
place : seuls les textes qui changent sont touchés. Rendu par Dash, chaque
ligne coûtait un arbre de composants à réconcilier, et la page ne tenait
plus que la moitié de ses minuteries, avec des gels de près de neuf
secondes en plein écran (mesure du 5 novembre 2026). Le canal push et le
repli par interrogation livrent les mêmes données au même script : il n'y a
qu'un moteur de rendu, et le formatage reste ici, testé sans navigateur.

Le carnet suit la devise du panneau prix. La profondeur n'en a pas : elle
s'exprime en écart relatif et en bitcoins.
"""

from __future__ import annotations

import math
import time

from dash import Input, Output, State, dcc, html

from ..charts import (placeholder, build_depth_chart, build_liquidity_chart,
                      liquidity_series)
from ..fx import display, spread_text
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Le carnet et la profondeur partagent désormais une cellule haute de
#: deux rangées, au lieu d'une chacun : la place regagnée revient aux
#: niveaux affichés. Au-delà, les meilleures offres d'achat sortent du
#: cadre et seules les ventes restent visibles. En plein écran, la place
#: ne manque plus du tout.
DEPTH = 8
DEPTH_MAX = 20

#: Les murs se cherchent par tranches d'écart au prix médian, en % : soit
#: quelques dollars à ce cours. Pas en prix absolu : les plateformes cotent à
#: plusieurs dizaines de dollars d'écart, et additionner leurs carnets tels
#: quels posait un mur d'achat au-dessus du mur de vente. La profondeur
#: comparée se recentre pour la même raison.
WALL_BUCKET = 0.01
#: Niveaux lus par carnet et par côté.
WALL_DEPTH = 100

#: Les couleurs que `assets/book.js` applique, posées en variables CSS sur
#: le conteneur : le thème garde une seule définition, ici.
BOOK_VARS = {
    "--book-ask": C["red"], "--book-bid": C["green"],
    "--book-ask-bar": f"{C['red']}22", "--book-bid-bar": f"{C['green']}22",
    "--book-text": C["text"], "--book-muted": C["muted"],
    "--book-spread": C["yellow"], "--book-border": C["border"],
    "--book-mono": MONO,
}


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
        # Rempli par assets/book.js, jamais par Dash : sans enfants côté
        # Dash, React ne touche pas à ce que le script y écrit.
        html.Div(id="book-table",
                 style={"flex": "1", "overflowY": "auto", **BOOK_VARS}),
        # Les données du repli par interrogation ; le canal push, lui, les
        # remet directement au script.
        dcc.Store(id="book-data"),
        dcc.Store(id="book-sink"),
    ], style=PANEL_STYLE)


#: Fenêtre de l'historique de liquidité, en jours : la journée dans la
#: grille, la semaine en plein écran.
LIQUIDITY_DAYS = 1
LIQUIDITY_DAYS_MAX = 7


def depth_layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Profondeur comparée"),
            html.Span(id="depth-liquidity",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        dcc.Graph(
            id="depth-chart", figure=placeholder(),
            style={"flex": "2", "minHeight": "0"},
            config={"displayModeBar": False, "scrollZoom": True},
        ),
        # La mémoire de la profondeur : le même carnet, dans le temps.
        dcc.Graph(
            id="depth-history", figure=placeholder(),
            style={"flex": "1", "minHeight": "0"},
            config={"displayModeBar": False},
        ),
    ], style=PANEL_STYLE)


def render_liquidity(hub, expanded: bool = False):
    """L'historique de liquidité et son badge — pur vis-à-vis de Dash.

    Le badge dit le dernier relevé — le volume à portée, des deux côtés
    réunis — et ce qu'il vaut contre la moyenne de la fenêtre : c'est
    l'écart qui se lit, un carnet n'étant ni plein ni vide dans l'absolu.
    """
    days = LIQUIDITY_DAYS_MAX if expanded else LIQUIDITY_DAYS
    history = hub.book_depth_history(days)
    series, gardees, ecartees = liquidity_series(history)
    # En plein écran, ce que chaque plateforme offre, empilé : les mêmes
    # plateformes et les mêmes relevés que la somme.
    detail_par_place = None
    if expanded and not series.empty:
        detail_par_place = history[history["exchange"].isin(gardees)
                                   & history["time"].isin(series["time"])]
    figure = build_liquidity_chart(
        series, gap=getattr(hub, "SNAPSHOT_GAP", 900),
        uirevision=f"liquidity-{days}", by_exchange=detail_par_place)
    band = getattr(hub, "DEPTH_BAND", 0.1)
    if series.empty:
        return figure, html.Span(f"liquidité ±{band:g} % : en constitution",
                                 style={"color": C["muted"],
                                        "fontFamily": MONO})
    total = series["bid"] + series["ask"]
    last, mean = float(total.iloc[-1]), float(total.mean())
    ecart = (last / mean - 1) * 100 if mean else 0.0
    detail = (f"volume offert à moins de {band:g} % du prix, achats et ventes, "
              f"moyenne de chaque période de cinq minutes — "
              f"{', '.join(gardees)}"
              + (f" ; sans {', '.join(ecartees)}, trop souvent absent"
                 if ecartees else "")
              + f" ; écart à la moyenne de {'7 j' if expanded else '24 h'}"
                f" ({mean:.0f} BTC)")
    children = [
        html.Span(f"±{band:g} % · ", style={"color": C["muted"]}),
        html.Span(f"{last:.0f} BTC", style={"color": C["text"]}),
    ]
    if len(series) >= 12:        # une heure de relevés : la moyenne dit quelque chose
        children.append(html.Span(
            f" {ecart:+z.0f} %",
            style={"color": C["red"] if ecart <= -25 else
                   C["green"] if ecart >= 25 else C["muted"]}))
    if ecartees:
        children.append(html.Span(f" · {len(gardees)}/{len(gardees) + len(ecartees)}",
                                  style={"color": C["yellow"]}))
    return figure, html.Span(children, style={"fontFamily": MONO}, title=detail)


def wall(books: dict, side: str) -> tuple[float, float] | None:
    """La tranche la plus chargée d'un côté, plateformes réunies.

    Rend `(écart au prix médian en %, BTC)`. L'arrondi s'éloigne du milieu :
    un mur d'achat reste sous le prix, un mur de vente au-dessus, jusque
    dans la toute première tranche. Un carnet déconnecté ne compte pas : ses
    niveaux sont ceux de sa dernière trame, et un mur fantôme serait pire
    qu'aucun.
    """
    sign = -1 if side == "bids" else 1
    totals: dict[float, float] = {}
    for book in books.values():
        mid = book.mid
        if not book.connected or not mid:
            continue
        for price, qty in book.top(side, WALL_DEPTH):
            distance = abs(price / mid - 1) * 100
            # L'arrondi intérieur écarte le bruit du calcul en flottants.
            step = max(1, math.ceil(round(distance / WALL_BUCKET, 6)))
            bucket = sign * step * WALL_BUCKET
            totals[bucket] = totals.get(bucket, 0.0) + qty
    return max(totals.items(), key=lambda item: item[1], default=None)


#: Durée de vie des murs calculés, en secondes. Le carnet part dix fois par
#: seconde, et chercher les murs dans cinq carnets entiers prenait 1,9 des
#: 2,6 ms de chaque envoi (mesure du 11 novembre 2026, Binance à mille
#: niveaux par côté) : un mur ne se déplace pas en cent millisecondes.
WALLS_TTL = 1.0


def _wall_levels(hub) -> tuple:
    """Les deux murs, recalculés au plus une fois par `WALLS_TTL`."""
    memo = getattr(hub, "_walls_memo", None)
    now = time.monotonic()
    if memo is None or now - memo[0] >= WALLS_TTL or memo[1] is not hub.books:
        memo = (now, hub.books,
                wall(hub.books, "bids"), wall(hub.books, "asks"))
        try:
            hub._walls_memo = memo
        except AttributeError:      # un hub d'essai sans attributs libres
            pass
    return memo[2], memo[3]


def _walls(books: dict, mid: float | None, fx, levels=None) -> dict | None:
    """La ligne des murs ; rien tant qu'aucun carnet connecté n'en donne.

    Les écarts sont ramenés au prix de la plateforme affichée : c'est celui
    qu'on a sous les yeux, juste au-dessus. `levels` sont les deux murs
    déjà cherchés (`_wall_levels`) ; sans eux, ils le sont ici.
    """
    bid, ask = levels or (wall(books, "bids"), wall(books, "asks"))
    if bid is None or ask is None or not mid:
        return None

    def label(side, level):
        price = mid * (1 + level[0] / 100) * fx.rate
        return f"{side} {price:,.0f} ({level[0]:+z.2f} %) · {level[1]:.1f} BTC"

    return {"bid": label("achat", bid), "ask": label("vente", ask),
            "title": fx.title("la tranche la plus chargée de chaque côté, "
                              "toutes plateformes connectées, par écart à "
                              "leur prix médian")}


def book_payload(hub, exchange: str, expanded: bool,
                 currency: str | None = None) -> dict:
    """Le carnet d'une plateforme, en données prêtes pour `assets/book.js`.

    Fonction pure vis-à-vis du rendu : le callback Dash et le pousseur
    WebSocket l'appellent tous deux — un seul code décide de ce que le
    panneau montre, quel que soit le canal qui l'amène au navigateur.

    Chaque niveau est `[prix, quantité, largeur de la barre en %]`, les prix
    déjà dans la devise affichée ; les ventes viennent dans l'ordre de
    l'écran, la plus chère en haut. Sans niveaux, seul `message` est rendu.

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
        return {"message": book.error or "connexion en cours…"}

    largest = max((q for _, q in bids + asks), default=1) or 1
    bought = sum(q for _, q in bids)
    pressure = bought / ((bought + sum(q for _, q in asks)) or 1) * 100
    spread = book.spread or 0
    spread_pct = book.spread_pct or 0

    def levels(side):
        return [[f"{p * fx.rate:,.{decimals}f}", f"{q:.4f}",
                 round(q / largest * 100)] for p, q in side]

    return {
        "asks": levels(reversed(asks)),
        "bids": levels(bids),
        "spread": (f"{fx.warning}spread {spread * fx.rate:,.2f} {fx.unit} · "
                   f"{spread_text(spread_pct)} · achat {pressure:.0f} % · "
                   f"{book.age_ms:.0f} ms"),
        "spread_title": fx.title("achat : part des acheteurs dans le volume "
                                 "des niveaux affichés"),
        "walls": _walls(hub.books, book.mid, fx, _wall_levels(hub)),
    }


def render_depth(hub):
    """La profondeur comparée, prête à poser dans `depth-chart`."""
    return build_depth_chart(hub.books)


def register(app, hub):
    @app.callback(
        Output("book-data", "data"),
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
        return book_payload(hub, exchange, expanded == "book", currency)

    # Le script écrit le carnet ; la sortie n'est qu'un puits.
    app.clientside_callback(
        "function (data) {"
        " if (window.btcBook) { window.btcBook.render(data); }"
        " return window.dash_clientside.no_update; }",
        Output("book-sink", "data"),
        Input("book-data", "data"),
    )

    @app.callback(
        Output("depth-chart", "figure"),
        Input("tick-fast", "n_intervals"),
    )
    def _refresh_depth(_tick):
        return render_depth(hub)

    @app.callback(
        Output("depth-history", "figure"),
        Output("depth-liquidity", "children"),
        # Un relevé toutes les cinq minutes : l'horloge rare suffit.
        Input("tick-rare", "n_intervals"),
        Input("expanded", "data"),
    )
    def _refresh_liquidity(_tick, expanded):
        return render_liquidity(hub, expanded == "depth")
