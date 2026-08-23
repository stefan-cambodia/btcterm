"""
Assemblage du terminal.

Deux régimes de rafraîchissement plutôt qu'un seul — c'est ce qui permet
d'afficher un carnet vivant sans resérialiser les chandeliers quatre fois
par seconde :

    tick-fast  250 ms   carnet, profondeur, arbitrage  (mémoire, zéro réseau)
    tick-slow    2 s    chandeliers, indicateurs, bandeau
    tick-rare    5 min  flux ETF, news, Fear & Greed

Le régime rapide a un second canal : quand le navigateur tient un
WebSocket ouvert sur /push, le serveur pousse le rendu et tick-fast est
coupé (terminal/push.py). L'horloge reste le repli — le terminal
fonctionne à l'identique sans le canal, juste au rythme de
l'interrogation.

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

from . import lwc, push
from .panels import (PANELS, alerts, arbitrage, calendar, dominance, etf,
                     liquidations, macro, news, onchain, orderbook, perp,
                     price)
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


#: Composition des cellules **par défaut**. Une cellule peut héberger
#: plusieurs panneaux, choisis par des onglets posés à la place du titre
#: — c'est ce qui permet d'ajouter des panneaux à une grille déjà pleine.
#: La répartition réelle est configurable (dialogue ⚙, Store
#: `placement`) ; `CELLS` reste la seule liste de ce qui est affichable,
#: et fournit le registre et le rangement de repli.
#:
#: Un panneau caché n'est pas dans la page : Dash ne fait donc tourner
#: aucun de ses callbacks, et il se remplit dès qu'on l'affiche, sans
#: attendre le prochain tour de son horloge.
CELLS: dict[str, tuple[tuple[str, str, object], ...]] = {
    "price": (("price", "PRIX", price.layout),),
    "book": (("book", "CARNET", orderbook.layout),
             ("depth", "PROFONDEUR", orderbook.depth_layout)),
    "arb": (("arb", "ARBITRAGE", arbitrage.layout),
            ("liq", "LIQUIDATIONS", liquidations.layout)),
    "etf": (("etf", "FLUX ETF", etf.layout),
            ("perp", "PERPÉTUEL", perp.layout)),
    "news": (("news", "NEWS", news.layout),
             ("cal", "CALENDRIER", calendar.layout),
             ("alerts", "ALERTES", alerts.layout)),
    "macro": (("macro", "MACRO", macro.layout),
              ("dominance", "DOMINANCE", dominance.layout),
              ("onchain", "ON-CHAIN", onchain.layout)),
}

#: Zones de la grille, dans l'ordre où elles sont posées. C'est aussi
#: l'ordre des sorties du callback de plein écran.
AREAS = tuple(CELLS)

#: Nom des cellules dans le dialogue de disposition — leur position à
#: l'écran, seule chose qui parle à l'utilisateur.
AREA_LABELS = {
    "price": "gauche",
    "book": "centre haut",
    "etf": "centre bas",
    "arb": "droite haut",
    "news": "droite bas",
    "macro": "rangée basse",
}

#: Colonnes du dialogue : la position des cellules à l'écran, de gauche à
#: droite puis de haut en bas — pas l'ordre interne d'AREAS. Le test de
#: rangement vérifie qu'aucune cellule n'y manque.
DIALOG_COLUMNS = ("price", "book", "etf", "arb", "news", "macro")

#: Registre à plat des panneaux : identifiant → (libellé, layout).
PANEL_REGISTRY: dict[str, tuple[str, object]] = {
    panel_id: (label, fn)
    for panels in CELLS.values() for panel_id, label, fn in panels
}

#: Cellule d'origine de chaque panneau — celle où il revient quand un
#: rangement restauré ne le mentionne plus.
HOME_AREA = {panel_id: area
             for area, panels in CELLS.items() for panel_id, _, _ in panels}

#: Rangement par défaut : cellule → panneaux, dans l'ordre des onglets.
DEFAULT_PLACEMENT = {area: tuple(panel_id for panel_id, _, _ in panels)
                     for area, panels in CELLS.items()}


def normalize_placement(data) -> dict[str, tuple[str, ...]]:
    """Rend un rangement exploitable, quoi que contienne le localStorage.

    Un rangement restauré peut dater d'avant un renommage de panneau, ou
    avoir été altéré : les identifiants inconnus sont écartés, un panneau
    rangé deux fois ne garde que sa première place, un panneau rangé
    nulle part revient dans sa cellule d'origine. Une cellule vide fait
    retomber le tout sur le rangement par défaut — le dialogue refusant
    d'en produire, elle ne peut venir que d'un localStorage périmé.
    """
    if not isinstance(data, dict):
        return dict(DEFAULT_PLACEMENT)
    seen: set[str] = set()
    placement: dict[str, list[str]] = {}
    for area in AREAS:
        listed = data.get(area, [])
        kept = []
        for panel_id in (listed if isinstance(listed, (list, tuple)) else []):
            if panel_id in PANEL_REGISTRY and panel_id not in seen:
                kept.append(panel_id)
                seen.add(panel_id)
        placement[area] = kept
    for panel_id in PANEL_REGISTRY:
        if panel_id not in seen:
            placement[HOME_AREA[panel_id]].append(panel_id)
    if any(not panels for panels in placement.values()):
        return dict(DEFAULT_PLACEMENT)
    return {area: tuple(panels) for area, panels in placement.items()}


def placement_from_choices(panel_ids, chosen_areas) -> dict[str, list[str]]:
    """Traduit les choix du dialogue — panneau → cellule — en rangement.

    L'ordre des onglets d'une cellule est l'ordre du registre : les rangs
    du dialogue sont parcourus tels qu'ils sont affichés.
    """
    placement: dict[str, list[str]] = {area: [] for area in AREAS}
    for panel_id, area in zip(panel_ids, chosen_areas):
        placement[area if area in placement else HOME_AREA[panel_id]].append(panel_id)
    return placement


def _tabs(area: str, panels: tuple[str, ...], active: str):
    """Barre d'onglets d'une cellule, posée à la place du titre du panneau.

    Rien n'est rendu pour une cellule qui n'héberge qu'un panneau : ce
    dernier garde alors son propre titre.
    """
    if len(panels) < 2:
        return None
    return html.Span([
        html.Span(
            PANEL_REGISTRY[panel_id][0],
            id={"type": "tab", "area": area, "panel": panel_id},
            className=("cell-tab cell-tab-active" if panel_id == active
                       else "cell-tab"),
            n_clicks=0,
        )
        for panel_id in panels
    ], className="cell-tabs")


def _body(area: str, active: str, placement: dict[str, tuple[str, ...]]):
    """Contenu d'une cellule : le seul panneau actif, titré par ses onglets."""
    return PANEL_REGISTRY[active][1](_tabs(area, placement[area], active))


def _cell(area: str):
    """Place une cellule dans sa zone, avec son bouton d'agrandissement.

    Le bouton est ajouté ici plutôt que dans chaque panneau : c'est la
    grille qui sait ce qu'agrandir veut dire, pas le panneau.
    """
    return html.Div(
        [
            html.Button("⛶", id=f"zoom-{area}", className="zoom-btn",
                        title="plein écran (Échap pour revenir)"),
            html.Div(_body(area, DEFAULT_PLACEMENT[area][0], DEFAULT_PLACEMENT),
                     id=f"cell-{area}-body",
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
        html.Button("⚙", id="layout-btn", className="layout-btn",
                    title="disposition de la grille"),
        # La cloche : sonneries de la dernière heure. Tenue par le
        # callback du panneau alertes — qui tourne toujours, le fil
        # devant compter et sonner même panneau replié.
        html.Span(id="hdr-alerts", title="alertes de la dernière heure",
                  style={**_STAT, "color": C["muted"], "fontSize": "11px"}),
        html.Span("⛶ ou double-clic sur un panneau · Échap pour revenir",
                  style={**_STAT, "marginLeft": "12px", "color": C["muted"],
                         "fontSize": "10px"}),
        # Canal des panneaux rapides : « push » quand le WebSocket est
        # ouvert, « poll » en repli. Tenu par assets/push.js, jamais par
        # un callback — c'est un état du navigateur, pas du serveur.
        html.Span(id="hdr-push", title="canal des panneaux rapides",
                  style={**_STAT, "color": C["muted"], "fontSize": "10px"}),
        html.Span(id="hdr-status", style={**_STAT, "color": C["muted"]}),
    ], style={
        "display": "flex", "alignItems": "center", "padding": "0 14px",
        "height": "38px", "background": C["panel"],
        "borderBottom": f"1px solid {C['border']}",
    })


def _layout_dialog():
    """Dialogue de rangement des panneaux, ouvert par le ⚙ du bandeau.

    Un sélecteur par panneau plutôt qu'une liste par cellule : la
    structure garantit d'elle-même qu'un panneau vit dans exactement une
    cellule — impossible d'en perdre un ou de l'afficher deux fois. La
    seule erreur qui reste constructible, une cellule vidée de tous ses
    panneaux, est refusée à l'application.

    Les sélecteurs ne portent pas de `persistence` : c'est le Store
    `placement` qui persiste, et le dialogue est resynchronisé sur lui à
    chaque ouverture.
    """
    rows = [
        html.Div([
            html.Span(label, className="layout-panel-name"),
            dcc.RadioItems(
                id={"type": "layout-cell", "panel": panel_id},
                options=[{"label": AREA_LABELS[area], "value": area}
                         for area in DIALOG_COLUMNS],
                value=HOME_AREA[panel_id], inline=True,
                className="tf-radio layout-radio",
            ),
        ], className="layout-row")
        for panel_id, (label, _) in PANEL_REGISTRY.items()
    ]
    return html.Div(
        html.Div([
            html.Div("DISPOSITION DE LA GRILLE", className="layout-title"),
            html.Div("Chaque panneau se range dans une cellule ; plusieurs "
                     "panneaux dans la même cellule se choisissent par "
                     "onglets, dans l'ordre de cette liste.",
                     className="layout-help"),
            *rows,
            html.Div(id="layout-msg", className="layout-msg"),
            html.Div([
                html.Button("Appliquer", id="layout-apply",
                            className="layout-button layout-button-primary"),
                html.Button("Par défaut", id="layout-reset",
                            className="layout-button"),
                html.Button("Fermer", id="layout-close",
                            className="layout-button"),
            ], className="layout-actions"),
        ], className="layout-dialog"),
        id="layout-overlay",
        className="layout-overlay layout-overlay-hidden",
    )


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
        # Le panneau que le plein écran montre réellement — l'identifiant
        # du panneau, pas celui de la cellule : depuis la disposition
        # configurable, carnet et liquidations peuvent vivre ailleurs que
        # dans leur cellule d'origine, et c'est à eux que l'agrandissement
        # accorde des lignes supplémentaires.
        dcc.Store(id="expanded"),
        # `local` : l'onglet actif de chaque cellule survit au
        # rechargement. Le plein écran, lui, reste en mémoire — restaurer
        # un panneau agrandi sans les classes CSS qui vont avec laisserait
        # la page dans un état incohérent, et revenir à la grille est le
        # comportement attendu d'un rechargement.
        #
        # Surtout pas de `data=DEFAULT_TABS` ici : une donnée fournie par
        # le layout est réécrite dans le localStorage à chaque chargement,
        # ce qui écraserait précisément ce qu'on veut restaurer. Le repli
        # sur les défauts appartient aux callbacks, qui le font déjà.
        dcc.Store(id="tabs", storage_type="local"),
        # Même régime que `tabs` : la répartition des panneaux dans les
        # cellules survit au rechargement, et surtout pas de `data=` — le
        # repli sur le rangement par défaut appartient aux callbacks.
        dcc.Store(id="placement", storage_type="local"),
        # Ce qu'affiche réellement chaque cellule (panneau actif + liste
        # d'onglets), en mémoire : c'est le garde qui évite de re-rendre
        # une cellule dont rien n'a changé (§ _register_tabs).
        *[dcc.Store(id=f"cell-{area}-view") for area in AREAS],
        # Puits des relais d'état du pousseur (terminal/push.py) : leurs
        # callbacks clientside n'écrivent que pour avoir une sortie.
        dcc.Store(id="push-sink-expanded"),
        dcc.Store(id="push-sink-exchange"),
        # Alertes : les réglages survivent au rechargement et réarment
        # le moteur au chargement ; le fil et ses puits sont globaux —
        # la sonnerie navigateur doit retentir même panneau replié.
        dcc.Store(id="alert-config", storage_type="local"),
        dcc.Store(id="alerts-feed"),
        dcc.Store(id="alerts-feed-sink"),
        dcc.Store(id="alert-config-sink"),
        _header(),
        html.Div([_cell(area) for area in AREAS], id="grid", style=_GRID),
        _layout_dialog(),
    ], style={"background": C["bg"], "margin": "0", "height": "100vh",
              "overflow": "hidden"})

    for panel in PANELS:
        panel.register(app, hub)

    _register_fullscreen(app)
    _register_tabs(app)
    _register_expanded(app)
    _register_layout_dialog(app)
    push.register(app, hub)
    lwc.register_api(app, hub)

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
            // Un onglet qui vient d'être monté déclenche aussi ce
            // callback, avec n_clicks à zéro — c'est le rendu initial ou
            // un changement d'onglet, pas un clic. L'ignorer évite
            // d'écraser l'état restauré du localStorage.
            if (!context.triggered[0].value) {
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

    for area in AREAS:
        # Pas de `prevent_initial_call` : les Stores étant persistés, le
        # premier tour sert à synchroniser la cellule avec l'onglet et le
        # rangement restaurés du localStorage — sans lui, la page
        # rechargée marquerait l'onglet actif sans afficher son panneau.
        #
        # Le Store `cell-…-view` retient ce que la cellule affiche déjà.
        # `tabs` et `placement` sont globaux : sans ce garde, un clic
        # d'onglet re-rendrait les six cellules, et remonter un graphique
        # lui fait perdre son zoom — `uirevision` ne survit pas à un
        # remontage, seulement aux mises à jour.
        @app.callback(
            Output(f"cell-{area}-body", "children"),
            Output(f"cell-{area}-view", "data"),
            Input("tabs", "data"),
            Input("placement", "data"),
            State(f"cell-{area}-view", "data"),
        )
        def _switch(tabs, placement, rendered, area=area):
            placement = normalize_placement(placement)
            panels = placement[area]
            active = (tabs or {}).get(area, panels[0])
            # Un localStorage peut dater d'avant un renommage de panneau,
            # ou pointer un panneau parti dans une autre cellule : un
            # identifiant absent retombe sur le premier onglet au lieu de
            # casser le rendu de la cellule.
            if active not in panels:
                active = panels[0]
            view = [active, list(panels)]
            if view == rendered:
                return dash.no_update, dash.no_update
            return _body(area, active, placement), view


def _register_expanded(app: dash.Dash) -> None:
    """Traduit la cellule agrandie en panneau agrandi.

    `maximized` retient une cellule ; le carnet et les liquidations, qui
    gagnent des lignes en plein écran, ont besoin de savoir si c'est
    *eux* qu'on regarde — et depuis la disposition configurable, leur
    cellule n'est plus connue d'avance. `tabs` est aussi une entrée : on
    peut changer d'onglet sans quitter le plein écran.
    """
    @app.callback(
        Output("expanded", "data"),
        Input("maximized", "data"),
        Input("tabs", "data"),
        State("placement", "data"),
    )
    def _expanded(maximized, tabs, placement):
        if not maximized:
            return None
        panels = normalize_placement(placement)[maximized]
        active = (tabs or {}).get(maximized, panels[0])
        return active if active in panels else panels[0]


def _register_layout_dialog(app: dash.Dash) -> None:
    """Le dialogue de disposition : ouvrir, pré-remplir, appliquer.

    Un seul callback pour les quatre boutons — ils écrivent tous dans les
    mêmes sorties, et Dash n'accepte qu'un écrivain par sortie. Le
    serveur est le bon endroit : ouvrir le dialogue est un geste rare, et
    pré-remplir les sélecteurs demande le rangement normalisé.
    """
    @app.callback(
        Output("layout-overlay", "className"),
        Output({"type": "layout-cell", "panel": ALL}, "value"),
        Output("placement", "data"),
        Output("layout-msg", "children"),
        Input("layout-btn", "n_clicks"),
        Input("layout-apply", "n_clicks"),
        Input("layout-reset", "n_clicks"),
        Input("layout-close", "n_clicks"),
        State({"type": "layout-cell", "panel": ALL}, "value"),
        State({"type": "layout-cell", "panel": ALL}, "id"),
        State("placement", "data"),
        prevent_initial_call=True,
    )
    def _dialog(_open, _apply, _reset, _close, values, ids, stored):
        visible = "layout-overlay"
        hidden = "layout-overlay layout-overlay-hidden"
        panel_ids = [component_id["panel"] for component_id in ids]
        keep_values = [dash.no_update] * len(panel_ids)
        trigger = dash.ctx.triggered_id

        if trigger == "layout-close":
            return hidden, keep_values, dash.no_update, ""

        if trigger == "layout-btn":
            # Resynchroniser les sélecteurs sur le rangement en vigueur :
            # le dialogue a pu être fermé sur des choix non appliqués.
            placement = normalize_placement(stored)
            area_of = {panel_id: area for area, panels in placement.items()
                       for panel_id in panels}
            return (visible, [area_of[panel_id] for panel_id in panel_ids],
                    dash.no_update, "")

        if trigger == "layout-reset":
            return (visible, [HOME_AREA[panel_id] for panel_id in panel_ids],
                    dash.no_update, "rangement d'origine — Appliquer pour le retenir")

        placement = placement_from_choices(panel_ids, values)
        empty = [AREA_LABELS[area] for area, panels in placement.items()
                 if not panels]
        if empty:
            return (visible, keep_values, dash.no_update,
                    f"cellule vide : {', '.join(empty)} — chaque cellule doit "
                    "garder au moins un panneau")
        return hidden, keep_values, placement, ""


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
        "--no-journal", action="store_true",
        help="ne pas tenir ~/.btcterm/journal.db (liquidations et épisodes "
             "d'arbitrage de la séance)",
    )
    parser.add_argument(
        "--cryptopanic-key", default=os.environ.get("CRYPTOPANIC_API_KEY", ""),
        help="clé CryptoPanic pour la collecte de news (défaut : variable "
             "d'environnement CRYPTOPANIC_API_KEY)",
    )
    parser.add_argument(
        "--lwc", action="store_true",
        help="rendu Lightweight Charts du panneau prix (équivaut à "
             "BTCTERM_LWC=1) — drapeau de transition de la voie A, "
             "appelé à devenir le défaut",
    )
    args = parser.parse_args()

    if args.lwc:
        # Posé dans l'environnement plutôt que passé en paramètre : c'est
        # la même source que lit le régime service (wsgi), et le panneau
        # prix la consulte au rendu.
        os.environ["BTCTERM_LWC"] = "1"

    hub = MarketHub(
        collect_news=not args.no_news,
        cryptopanic_key=args.cryptopanic_key,
        keep_journal=not args.no_journal,
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
