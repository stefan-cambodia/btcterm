"""
Assemblage du terminal.

Deux régimes de rafraîchissement plutôt qu'un seul — c'est ce qui permet
d'afficher un carnet vivant sans resérialiser les chandeliers quatre fois
par seconde :

    tick-fast  250 ms   carnet, profondeur, arbitrage  (mémoire, zéro réseau)
    tick-slow    2 s    chandeliers, indicateurs, bandeau
    tick-rare    5 min  flux ETF, news, Fear & Greed

Lancement :
    python -m terminal.app            # http://127.0.0.1:8050
    python -m terminal.app --port 8060
"""

from __future__ import annotations

import argparse
import os

import dash
from dash import ALL, Input, Output, State, dcc, html

from btcterm.hub import MarketHub

from .panels import PANELS, arbitrage, etf, macro, news, orderbook, perp, price
from .theme import C, MONO

REFRESH_FAST_MS = 250
REFRESH_SLOW_MS = 2_000
REFRESH_RARE_MS = 300_000

# Disposition des panneaux. Le graphique prix occupe toute la colonne de
# gauche — c'est lui qu'on regarde en séance d'analyse ; les panneaux de
# surveillance se rangent à droite.
#
#     ┌─────────┬────────┬───────────┐
#     │         │ carnet │ arbitrage │
#     │  prix   ├────────┼───────────┤
#     │         │ profo. │           │
#     │         ├────────┤   news    │
#     │         │  etf   │           │
#     │         ├────────┴───────────┤
#     │         │       macro        │
#     └─────────┴────────────────────┘
#
# Le panneau macro prend toute la largeur restante sur une rangée basse :
# deux séries mensuelles sur dix ans se lisent en longueur, pas en
# hauteur, et c'est la forme qui coûte le moins aux autres panneaux.
#
# Les hauteurs sont fixées en fractions du viewport : Plotly a besoin
# d'une hauteur explicite pour que le zoom se comporte correctement.
_GRID = {
    "display": "grid",
    "gridTemplateAreas": ('"price book arb" "price book news"'
                          ' "price etf news" "price macro macro"'),
    "gridTemplateColumns": "1.35fr 1fr 1fr",
    "gridTemplateRows": ("minmax(0, 1.05fr) minmax(0, 1fr)"
                         " minmax(0, 1fr) minmax(0, 0.85fr)"),
    "gap": "8px",
    "padding": "8px",
    "height": "calc(100vh - 46px)",
    "boxSizing": "border-box",
    "background": C["bg"],
}


#: Composition des cellules. Une cellule peut héberger plusieurs
#: panneaux, choisis par des onglets posés à la place du titre — c'est ce
#: qui permet d'ajouter des panneaux à une grille déjà pleine.
#:
#: Un panneau caché n'est pas dans la page : Dash ne fait donc tourner
#: aucun de ses callbacks, et il se remplit dès qu'on l'affiche, sans
#: attendre le prochain tour de son horloge.
CELLS: dict[str, tuple[tuple[str, str, object], ...]] = {
    "price": (("price", "PRIX", price.layout),),
    "book": (("book", "CARNET", orderbook.layout),
             ("depth", "PROFONDEUR", orderbook.depth_layout)),
    "arb": (("arb", "ARBITRAGE", arbitrage.layout),),
    "etf": (("etf", "FLUX ETF", etf.layout),
            ("perp", "PERPÉTUEL", perp.layout)),
    "news": (("news", "NEWS", news.layout),),
    "macro": (("macro", "MACRO", macro.layout),),
}

#: Zones de la grille, dans l'ordre où elles sont posées. C'est aussi
#: l'ordre des sorties du callback de plein écran.
AREAS = tuple(CELLS)

#: Panneau affiché par défaut dans chaque cellule.
DEFAULT_TABS = {area: panels[0][0] for area, panels in CELLS.items()}


def _tabs(area: str, active: str):
    """Barre d'onglets d'une cellule, posée à la place du titre du panneau.

    Rien n'est rendu pour une cellule qui n'héberge qu'un panneau : ce
    dernier garde alors son propre titre.
    """
    panels = CELLS[area]
    if len(panels) < 2:
        return None
    return html.Span([
        html.Span(
            label,
            id={"type": "tab", "area": area, "panel": panel_id},
            className=("cell-tab cell-tab-active" if panel_id == active
                       else "cell-tab"),
            n_clicks=0,
        )
        for panel_id, label, _ in panels
    ], className="cell-tabs")


def _body(area: str, active: str):
    """Contenu d'une cellule : le seul panneau actif, titré par ses onglets."""
    layout_fn = next(fn for panel_id, _, fn in CELLS[area] if panel_id == active)
    return layout_fn(_tabs(area, active))


def _cell(area: str):
    """Place une cellule dans sa zone, avec son bouton d'agrandissement.

    Le bouton est ajouté ici plutôt que dans chaque panneau : c'est la
    grille qui sait ce qu'agrandir veut dire, pas le panneau.
    """
    return html.Div(
        [
            html.Button("⛶", id=f"zoom-{area}", className="zoom-btn",
                        title="plein écran (Échap pour revenir)"),
            html.Div(_body(area, DEFAULT_TABS[area]), id=f"cell-{area}-body",
                     style={"height": "100%"}),
        ],
        id=f"cell-{area}",
        className="cell",
        style={"gridArea": area},
    )

_STAT = {"fontFamily": MONO, "fontSize": "11px", "color": C["text"],
         "marginRight": "18px"}


def _header():
    return html.Div([
        html.Span("₿ BTC TERMINAL", style={
            "fontFamily": MONO, "fontWeight": "700", "fontSize": "13px",
            "color": C["yellow"], "letterSpacing": "0.14em", "marginRight": "24px"}),
        html.Span(id="hdr-price", style={**_STAT, "fontSize": "14px",
                                         "fontWeight": "600"}),
        html.Span(id="hdr-change", style=_STAT),
        html.Span(id="hdr-spread", style=_STAT),
        html.Span("⛶ ou double-clic sur un panneau · Échap pour revenir",
                  style={**_STAT, "marginLeft": "auto", "color": C["muted"],
                         "fontSize": "10px"}),
        html.Span(id="hdr-status", style={**_STAT, "color": C["muted"]}),
    ], style={
        "display": "flex", "alignItems": "center", "padding": "0 14px",
        "height": "38px", "background": C["panel"],
        "borderBottom": f"1px solid {C['border']}",
    })


def create_app(hub: MarketHub) -> dash.Dash:
    app = dash.Dash(
        __name__,
        title="₿ BTC Terminal",
        update_title=None,          # pas de « Updating… » clignotant à 250 ms
        suppress_callback_exceptions=True,
    )

    app.layout = html.Div([
        dcc.Interval(id="tick-fast", interval=REFRESH_FAST_MS),
        dcc.Interval(id="tick-slow", interval=REFRESH_SLOW_MS),
        dcc.Interval(id="tick-rare", interval=REFRESH_RARE_MS),
        dcc.Store(id="maximized"),
        dcc.Store(id="tabs", data=DEFAULT_TABS),
        _header(),
        html.Div([_cell(area) for area in AREAS], id="grid", style=_GRID),
    ], style={"background": C["bg"], "margin": "0", "height": "100vh",
              "overflow": "hidden"})

    for panel in PANELS:
        panel.register(app, hub)

    _register_fullscreen(app)
    _register_tabs(app)

    @app.callback(
        Output("hdr-price", "children"),
        Output("hdr-change", "children"),
        Output("hdr-change", "style"),
        Output("hdr-spread", "children"),
        Output("hdr-status", "children"),
        Input("tick-slow", "n_intervals"),
    )
    def _refresh_header(_tick):
        ticker = hub.ticker()
        live = hub.reference_price()
        price_txt = f"{live:,.2f} $" if live else "—"

        change = float(ticker.get("priceChangePercent", 0) or 0)
        change_style = {**_STAT, "color": C["green"] if change >= 0 else C["red"]}
        change_txt = f"{change:+.2f} % 24 h"

        spreads = [b.spread_pct for b in hub.books.values() if b.spread_pct]
        spread_txt = f"spread min {min(spreads):.4f} %" if spreads else ""

        uptime = hub.uptime_seconds
        status = (f"{hub.connected_count}/5 flux · "
                  f"{uptime // 3600:02d}:{uptime % 3600 // 60:02d}:{uptime % 60:02d}")

        return price_txt, change_txt, change_style, spread_txt, status

    return app


def _register_fullscreen(app: dash.Dash) -> None:
    """Bascule un panneau en plein écran, côté navigateur.

    Le calcul est fait en clientside : basculer n'a aucune raison de
    faire un aller-retour serveur, et surtout cela évite de recalculer
    la figure — Plotly se contente d'être redimensionné.
    """
    app.clientside_callback(
        """
        function (...args) {
            const areas = %(areas)s;
            const current = args[areas.length];
            const context = dash_clientside.callback_context;
            if (!context.triggered.length) {
                return dash_clientside.no_update;
            }

            const clicked = context.triggered[0].prop_id
                .split('.')[0].replace('zoom-', '');
            const next = (current === clicked) ? null : clicked;

            // Plotly ne se redimensionne qu'au resize de la fenêtre ; sans
            // cet événement, le graphique agrandi garderait sa taille de
            // vignette. Le délai laisse le navigateur appliquer les classes.
            setTimeout(function () {
                window.dispatchEvent(new Event('resize'));
            }, 60);

            const classes = areas.map(function (area) {
                if (next === null) { return 'cell'; }
                return area === next ? 'cell cell-max' : 'cell cell-hidden';
            });
            return [next].concat(classes);
        }
        """ % {"areas": list(AREAS)},
        [Output("maximized", "data")]
        + [Output(f"cell-{area}", "className") for area in AREAS],
        [Input(f"zoom-{area}", "n_clicks") for area in AREAS],
        State("maximized", "data"),
        prevent_initial_call=True,
    )


def _register_tabs(app: dash.Dash) -> None:
    """Onglets : un clic choisit le panneau, le serveur rend le corps.

    Le clic est traité côté navigateur pour ne mettre à jour qu'un
    `Store` ; c'est ce Store, et non les onglets eux-mêmes, qui déclenche
    le rendu du corps. Sans ce détour, le callback aurait pour entrées
    des composants qu'il remplace lui-même, et chaque rendu le
    redéclencherait.
    """
    app.clientside_callback(
        r"""
        function (...args) {
            const context = dash_clientside.callback_context;
            if (!context.triggered.length) {
                return dash_clientside.no_update;
            }
            const current = args[args.length - 1] || {};
            const id = JSON.parse(
                context.triggered[0].prop_id.replace(/\.n_clicks$/, ''));
            if (current[id.area] === id.panel) {
                return dash_clientside.no_update;
            }
            const next = Object.assign({}, current);
            next[id.area] = id.panel;
            return next;
        }
        """,
        Output("tabs", "data"),
        Input({"type": "tab", "area": ALL, "panel": ALL}, "n_clicks"),
        State("tabs", "data"),
        prevent_initial_call=True,
    )

    for area, panels in CELLS.items():
        if len(panels) < 2:
            continue

        @app.callback(
            Output(f"cell-{area}-body", "children"),
            Input("tabs", "data"),
            prevent_initial_call=True,
        )
        def _switch(tabs, area=area):
            return _body(area, (tabs or DEFAULT_TABS).get(area,
                                                          DEFAULT_TABS[area]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminal Bitcoin")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="127.0.0.1 par défaut : pour un accès distant, préférer un "
             "tunnel SSH (ssh -L 8050:localhost:8050) plutôt que d'exposer "
             "le port sur le réseau",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--no-news", action="store_true",
        help="ne pas alimenter ~/.btc_news/news.db : le panneau news se "
             "contente alors de lire ce que le tracker y a mis",
    )
    parser.add_argument(
        "--cryptopanic-key", default=os.environ.get("CRYPTOPANIC_API_KEY", ""),
        help="clé CryptoPanic pour la collecte de news (défaut : variable "
             "d'environnement CRYPTOPANIC_API_KEY)",
    )
    args = parser.parse_args()

    hub = MarketHub(
        collect_news=not args.no_news,
        cryptopanic_key=args.cryptopanic_key,
    )
    hub.start()

    app = create_app(hub)
    print(f"""
╔════════════════════════════════════════════════════════╗
║  ₿  BTC TERMINAL                                       ║
║  → http://{args.host}:{args.port}{' ' * (43 - len(args.host) - len(str(args.port)))}║
║  → à distance :  ssh -L {args.port}:localhost:{args.port} <machine>{' ' * max(0, 6 - len(str(args.port)) * 2)}║
║  → Ctrl-C pour arrêter                                 ║
╚════════════════════════════════════════════════════════╝
""")
    try:
        app.run(debug=args.debug, host=args.host, port=args.port)
    finally:
        hub.stop()


if __name__ == "__main__":
    main()
