"""
Le bandeau du terminal : cours, variation, spread, état des flux.

Une ligne au-dessus de la grille, rafraîchie au régime lent. Deux de ses
champs sont tenus par d'autres modules et ne font ici que réserver leur
place : la cloche des alertes (`hdr-alerts`, panneau alertes) et le canal
des panneaux rapides (`hdr-push`, assets/push.js).

La cloche est en revanche *cliquable* d'ici : compter les sonneries sans
offrir de chemin vers la liste laissait l'alerte visible et injoignable,
le panneau alertes vivant derrière le troisième onglet d'une cellule.
Le clic est donc traité ici, seul endroit qui connaisse à la fois le
bandeau et la grille (§ `_register_reveal`).
"""

from __future__ import annotations

import dash
from dash import Input, Output, State, html

from btcterm.hub import MarketHub

from .grid import AREAS, reveal
from .theme import C, MONO

#: Style commun des champs du bandeau.
STAT = {"fontFamily": MONO, "fontSize": "11px", "color": C["text"],
        "marginRight": "18px"}


def layout():
    return html.Div([
        html.Span("₿ BTC TERMINAL", style={
            "fontFamily": MONO, "fontWeight": "700", "fontSize": "13px",
            "color": C["yellow"], "letterSpacing": "0.14em", "marginRight": "24px"}),
        html.Span(id="hdr-price", style={**STAT, "fontSize": "14px",
                                         "fontWeight": "600"}),
        html.Span(id="hdr-change", style=STAT),
        html.Span(id="hdr-spread", style=STAT),
        html.Button("⚙", id="layout-btn", className="layout-btn",
                    title="disposition de la grille"),
        # La cloche : sonneries de la dernière heure. Son contenu est
        # tenu par le callback du panneau alertes — qui tourne toujours,
        # le fil devant compter et sonner même panneau replié ; son clic
        # ouvre le panneau (§ `_register_reveal`).
        #
        # Le curseur et le survol passent par la classe : le callback des
        # alertes réécrit `style` à chaque tour et effacerait ce qu'on y
        # mettrait ici.
        html.Span(id="hdr-alerts", className="hdr-bell", n_clicks=0,
                  title="alertes de la dernière heure — cliquer pour ouvrir "
                        "le panneau",
                  style={**STAT, "color": C["muted"], "fontSize": "11px"}),
        html.Span("⛶ ou double-clic sur un panneau · Échap pour revenir",
                  style={**STAT, "marginLeft": "12px", "color": C["muted"],
                         "fontSize": "10px"}),
        # Canal des panneaux rapides : « push » quand le WebSocket est
        # ouvert, « poll » en repli. Tenu par assets/push.js, jamais par
        # un callback — c'est un état du navigateur, pas du serveur.
        html.Span(id="hdr-push", title="canal des panneaux rapides",
                  style={**STAT, "color": C["muted"], "fontSize": "10px"}),
        html.Span(id="hdr-status", style={**STAT, "color": C["muted"]}),
    ], style={
        "display": "flex", "alignItems": "center", "padding": "0 14px",
        "height": "38px", "background": C["panel"],
        "borderBottom": f"1px solid {C['border']}",
    })


def register(app: dash.Dash, hub: MarketHub) -> None:
    _register_reveal(app)

    @app.callback(
        Output("hdr-price", "children"),
        Output("hdr-change", "children"),
        Output("hdr-change", "style"),
        Output("hdr-spread", "children"),
        Output("hdr-status", "children"),
        Input("tick-slow", "n_intervals"),
    )
    def _refresh(_tick):
        ticker = hub.ticker()
        live = hub.reference_price()
        price_txt = f"{live:,.2f} $" if live else "—"

        change = float(ticker.get("priceChangePercent", 0) or 0)
        change_style = {**STAT, "color": C["green"] if change >= 0 else C["red"]}
        change_txt = f"{change:+.2f} % 24 h"

        spreads = [b.spread_pct for b in hub.books.values() if b.spread_pct]
        spread_txt = f"spread min {min(spreads):.4f} %" if spreads else ""

        uptime = hub.uptime_seconds
        status = (f"{hub.connected_count}/5 flux · "
                  f"{uptime // 3600:02d}:{uptime % 3600 // 60:02d}:{uptime % 60:02d}")

        return price_txt, change_txt, change_style, spread_txt, status


def _register_reveal(app: dash.Dash) -> None:
    """Le clic sur la cloche amène le panneau alertes à l'écran.

    Deux gestes, parce qu'ils ne se décident pas au même endroit :

    - **choisir l'onglet**, côté serveur : le panneau alertes n'a pas de
      cellule fixe depuis que le rangement est configurable, et seule la
      grille (`reveal`) sait où il a atterri ;
    - **quitter le plein écran**, côté navigateur : si une *autre*
      cellule est agrandie, elle masque la grille et changer d'onglet
      dessous ne se verrait pas. Le geste est le même que celui du
      bouton ⛶ — remettre la classe `cell` partout — et se termine par
      l'événement `resize` que Plotly attend pour se redimensionner.
    """
    @app.callback(
        Output("tabs", "data", allow_duplicate=True),
        Input("hdr-alerts", "n_clicks"),
        State("tabs", "data"),
        State("placement", "data"),
        prevent_initial_call=True,
    )
    def _open(clicks, tabs, placement):
        # Le montage de la cloche déclenche aussi ce callback, n_clicks à
        # zéro : même garde que pour les onglets.
        if not clicks:
            return dash.no_update
        choix = reveal("alerts", tabs, placement)
        return dash.no_update if choix is None else choix

    app.clientside_callback(
        """
        function (clicks) {
            const areas = %(areas)s;
            if (!clicks) {
                return [dash_clientside.no_update].concat(
                    areas.map(function () {
                        return dash_clientside.no_update;
                    }));
            }
            setTimeout(function () {
                window.dispatchEvent(new Event('resize'));
            }, 60);
            return [null].concat(areas.map(function () { return 'cell'; }));
        }
        """ % {"areas": list(AREAS)},
        [Output("maximized", "data", allow_duplicate=True)]
        + [Output(f"cell-{area}", "className", allow_duplicate=True)
           for area in AREAS],
        Input("hdr-alerts", "n_clicks"),
        prevent_initial_call=True,
    )
