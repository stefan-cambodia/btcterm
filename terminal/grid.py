"""
La grille du terminal : cellules, onglets, plein écran.

Ce module tient tout ce qui décide *où* un panneau s'affiche — pas ce
qu'il affiche, qui appartient à `panels/`. Trois couches :

- le **rangement** : `CELLS` est la seule liste de ce qui est affichable ;
  le registre des panneaux, leur cellule d'origine et le rangement par
  défaut en découlent. `normalize_placement` rend exploitable un
  rangement restauré du localStorage, quoi qu'il contienne ;
- les **cellules** : chaque zone de la grille ne rend que son panneau
  actif, titré par ses onglets, et porte son bouton d'agrandissement ;
- les **callbacks** : le clic d'onglet, le rendu du corps d'une cellule,
  la bascule plein écran et sa traduction en panneau agrandi.

Le dialogue qui modifie le rangement vit à part (terminal/placement.py) ;
il ne connaît de la grille que ce que ce module expose.
"""

from __future__ import annotations

import dash
from dash import ALL, Input, Output, State, dcc, html

from .panels import (alerts, arbitrage, calendar, dominance, etf, journal,
                     liquidations, macro, news, onchain, orderbook, perp,
                     price)
from .theme import C

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
            ("liq", "LIQUIDATIONS", liquidations.layout),
            ("journal", "JOURNAL", journal.layout)),
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


# ───────────────────────────── rangement ─────────────────────────────

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


def active_panel(area: str, tabs, placement: dict[str, tuple[str, ...]]) -> str:
    """Le panneau qu'une cellule affiche, d'après l'onglet retenu.

    Un localStorage peut dater d'avant un renommage de panneau, ou
    pointer un panneau parti dans une autre cellule : un identifiant
    absent retombe sur le premier onglet au lieu de casser le rendu.
    """
    panels = placement[area]
    active = (tabs or {}).get(area, panels[0])
    return active if active in panels else panels[0]


def reveal(panel_id: str, tabs, placement) -> dict[str, str] | None:
    """Le choix d'onglets qui amène `panel_id` à l'écran, ou `None`.

    Un panneau n'a plus de cellule fixe depuis que le rangement est
    configurable : qui veut le montrer — le clic sur la cloche des
    alertes, aujourd'hui — doit d'abord demander à la grille où il a
    atterri. `None` dit qu'il est déjà à l'écran, et vaut alors
    `no_update` : réécrire le Store re-rendrait la cellule pour rien.
    """
    placement = normalize_placement(placement)
    area = next(name for name, panels in placement.items()
                if panel_id in panels)
    if active_panel(area, tabs, placement) == panel_id:
        return None
    return {**(tabs or {}), area: panel_id}


# ───────────────────────────── cellules ──────────────────────────────

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


def body(area: str, active: str, placement: dict[str, tuple[str, ...]]):
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
            html.Div(body(area, DEFAULT_PLACEMENT[area][0], DEFAULT_PLACEMENT),
                     id=f"cell-{area}-body",
                     style={"height": "100%"}),
        ],
        id=f"cell-{area}",
        className="cell",
        style={"gridArea": area},
    )


def stores() -> list:
    """Les Stores dont la grille a besoin, à poser dans la page.

    `local` : l'onglet actif de chaque cellule et le rangement survivent
    au rechargement. Le plein écran, lui, reste en mémoire — restaurer
    un panneau agrandi sans les classes CSS qui vont avec laisserait la
    page dans un état incohérent, et revenir à la grille est le
    comportement attendu d'un rechargement.

    Surtout pas de `data=` sur les Stores persistés : une donnée fournie
    par le layout est réécrite dans le localStorage à chaque chargement,
    ce qui écraserait précisément ce qu'on veut restaurer. Le repli sur
    les défauts appartient aux callbacks, qui le font déjà.
    """
    return [
        # La cellule agrandie, ou None.
        dcc.Store(id="maximized"),
        # Le panneau que le plein écran montre réellement — l'identifiant
        # du panneau, pas celui de la cellule : depuis la disposition
        # configurable, carnet et liquidations peuvent vivre ailleurs que
        # dans leur cellule d'origine, et c'est à eux que l'agrandissement
        # accorde des lignes supplémentaires.
        dcc.Store(id="expanded"),
        dcc.Store(id="tabs", storage_type="local"),
        dcc.Store(id="placement", storage_type="local"),
        # Ce qu'affiche réellement chaque cellule (panneau actif + liste
        # d'onglets), en mémoire : c'est le garde qui évite de re-rendre
        # une cellule dont rien n'a changé (§ _register_tabs).
        *[dcc.Store(id=f"cell-{area}-view") for area in AREAS],
    ]


def layout():
    """La grille elle-même : une cellule par zone, dans l'ordre d'AREAS."""
    return html.Div([_cell(area) for area in AREAS], id="grid", style=_GRID)


# ───────────────────────────── callbacks ─────────────────────────────

def register(app: dash.Dash) -> None:
    _register_fullscreen(app)
    _register_tabs(app)
    _register_expanded(app)


def _register_fullscreen(app: dash.Dash) -> None:
    """Bascule un panneau en plein écran, côté navigateur.

    Le calcul est fait en clientside : basculer n'a aucune raison de
    faire un aller-retour serveur, et surtout cela évite de recalculer
    la figure — Plotly se contente d'être redimensionné.

    `tests/test_fullscreen_toggle.py` extrait cette fonction du fichier
    pour l'exécuter sous Node : son gabarit (`%(areas)s`) et son
    indentation font partie du contrat.
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
            active = active_panel(area, tabs, placement)
            view = [active, list(placement[area])]
            if view == rendered:
                return dash.no_update, dash.no_update
            return body(area, active, placement), view


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
        return active_panel(maximized, tabs, normalize_placement(placement))
