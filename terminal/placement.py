"""
Le dialogue de disposition : ranger les panneaux dans les cellules.

Ouvert par le ⚙ du bandeau, il pose une rangée par panneau et six
positions au choix ; « Appliquer » écrit le rangement dans le Store
`placement` (localStorage), que la grille (terminal/grid.py) lit pour
rendre ses cellules. Ce module ne rend rien d'autre que le dialogue et
ne connaît de la grille que son modèle de rangement.
"""

from __future__ import annotations

import dash
from dash import ALL, Input, Output, State, dcc, html

from .grid import (AREA_LABELS, AREAS, HOME_AREA, PANEL_REGISTRY,
                   normalize_placement)

#: Colonnes du dialogue : la position des cellules à l'écran, de gauche à
#: droite puis de haut en bas — pas l'ordre interne d'AREAS. Le test de
#: rangement vérifie qu'aucune cellule n'y manque.
DIALOG_COLUMNS = ("price", "book", "etf", "arb", "news", "macro")

_VISIBLE = "layout-overlay"
_HIDDEN = "layout-overlay layout-overlay-hidden"


def placement_from_choices(panel_ids, chosen_areas) -> dict[str, list[str]]:
    """Traduit les choix du dialogue — panneau → cellule — en rangement.

    L'ordre des onglets d'une cellule est l'ordre du registre : les rangs
    du dialogue sont parcourus tels qu'ils sont affichés.
    """
    placement: dict[str, list[str]] = {area: [] for area in AREAS}
    for panel_id, area in zip(panel_ids, chosen_areas):
        placement[area if area in placement else HOME_AREA[panel_id]].append(panel_id)
    return placement


def layout():
    """Le dialogue, fermé, à poser dans la page.

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
        className=_HIDDEN,
    )


def register(app: dash.Dash) -> None:
    """Ouvrir, pré-remplir, appliquer.

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
        panel_ids = [component_id["panel"] for component_id in ids]
        keep_values = [dash.no_update] * len(panel_ids)
        trigger = dash.ctx.triggered_id

        if trigger == "layout-close":
            return _HIDDEN, keep_values, dash.no_update, ""

        if trigger == "layout-btn":
            # Resynchroniser les sélecteurs sur le rangement en vigueur :
            # le dialogue a pu être fermé sur des choix non appliqués.
            placement = normalize_placement(stored)
            area_of = {panel_id: area for area, panels in placement.items()
                       for panel_id in panels}
            return (_VISIBLE, [area_of[panel_id] for panel_id in panel_ids],
                    dash.no_update, "")

        if trigger == "layout-reset":
            return (_VISIBLE, [HOME_AREA[panel_id] for panel_id in panel_ids],
                    dash.no_update, "rangement d'origine — Appliquer pour le retenir")

        placement = placement_from_choices(panel_ids, values)
        empty = [AREA_LABELS[area] for area, panels in placement.items()
                 if not panels]
        if empty:
            return (_VISIBLE, keep_values, dash.no_update,
                    f"cellule vide : {', '.join(empty)} — chaque cellule doit "
                    "garder au moins un panneau")
        return _HIDDEN, keep_values, placement, ""
