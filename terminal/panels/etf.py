"""
Panneau flux ETF : entrées et sorties nettes des ETF Bitcoin spot.

Panneau « lent » : la source ne publie qu'une valeur par jour ouvré, le
cache du hub la conserve une demi-heure.

Dans la grille, le panneau s'en tient aux barres du flux net : la cellule
y est basse, et une barre par jour est tout ce qui s'y lit. Le plein
écran ouvre la page complète — cumul depuis le lancement sur l'axe droit,
classement des émetteurs sur la fenêtre, chiffres clés en barre de titre
et sélecteur de fenêtre. C'est la même donnée, lue à trois échelles que
la vignette ne pouvait pas porter.
"""

from __future__ import annotations

import pandas as pd
from dash import Input, Output, dcc, html

from ..charts import (ETF_DEFAULT_WINDOW, ETF_WINDOWS, build_etf_chart,
                      etf_stats, format_flow)
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Tableau vide passé au constructeur quand la source est muette : il
#: rend alors son cadre expliqué plutôt que de lever.
_VIDE = pd.DataFrame()

#: Le sélecteur de fenêtre n'a pas la place de s'afficher dans la grille :
#: la cellule partage sa barre de titre avec les onglets du perpétuel.
_PICKER_HIDDEN = {"display": "none"}
_PICKER_SHOWN = {"display": "inline-block", "fontSize": "9px",
                 "marginLeft": "12px"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Flux ETF spot (M$)"),
            html.Div([
                html.Span(id="etf-total", style={"fontSize": "10px"}),
                # `persistence` : la fenêtre choisie survit au
                # rechargement et au passage par l'onglet du perpétuel,
                # qui reconstruit ce layout à ses défauts.
                dcc.RadioItems(
                    id="etf-window",
                    options=[{"label": k, "value": k} for k in ETF_WINDOWS],
                    value=ETF_DEFAULT_WINDOW, inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style=_PICKER_HIDDEN,
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap"}),
        ], style=TITLE_STYLE),
        dcc.Graph(id="etf-chart", style={"flex": "1", "minHeight": "0"},
                  config={"displayModeBar": False}),
    ], style=PANEL_STYLE)


def _badges(stats: dict, window: str, verbose: bool):
    """Chiffres clés des flux : le jour, la semaine, la fenêtre, le stock.

    Muets dans la grille — la barre de titre y tient à peine le total de
    la fenêtre —, ils se déplient en plein écran. L'infobulle donne
    l'intitulé dans les deux cas.
    """
    if stats["window"] is None:
        return html.Span("source indisponible", style={"color": C["muted"]})

    def montant(value: float, titre: str, prefixe: str = ""):
        color = C["green"] if value >= 0 else C["red"]
        return html.Span(f"{prefixe}{format_flow(value)}",
                         style={"color": color}, title=titre)

    def muet(texte: str):
        return html.Span(texte, style={"color": C["muted"]})

    if not verbose:
        # Dans la grille, le total de la fenêtre et rien d'autre : la
        # barre de titre y partage déjà la place avec les onglets.
        return html.Span([
            muet(f"{window} : "),
            montant(stats["window"], f"flux net cumulé sur {window}"),
        ], style={"fontFamily": MONO})

    children = []
    if stats["last_date"] is not None:
        children += [
            muet(f"{stats['last_date']:%d %b} "),
            montant(stats["last"], "flux net du dernier jour publié"),
            muet(" · 5 j "),
            montant(stats["five"], "flux net des cinq derniers jours ouvrés"),
            muet(" · "),
        ]

    children += [
        muet(f"{window} "),
        montant(stats["window"], f"flux net cumulé sur la fenêtre ({window})"),
    ]

    if not stats["full"]:
        children += [
            muet(" · stock "),
            html.Span(format_flow(stats["cumul"]), style={"color": C["yellow"]},
                      title="cumul depuis le lancement des ETF, janvier 2024"),
        ]

    # Les records bornent la fenêtre : ils disent d'un coup d'œil si la
    # période contient un choc ou seulement du courant continu.
    for cle, intitule in (("best", "record d'entrée"),
                          ("worst", "record de sortie")):
        if stats[cle] is not None:
            date, valeur = stats[cle]
            children += [
                muet(f" · {intitule} "),
                montant(valeur, f"{intitule} sur la fenêtre"),
                muet(f" {date:%d/%m/%y}"),
            ]

    return html.Span(children, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("etf-chart", "figure"),
        Output("etf-total", "children"),
        Output("etf-window", "style"),
        Input("tick-rare", "n_intervals"),
        Input("etf-window", "value"),
        Input("expanded", "data"),
    )
    def _refresh(_tick, window, expanded):
        maximized = expanded == "etf"
        style = _PICKER_SHOWN if maximized else _PICKER_HIDDEN
        days = ETF_WINDOWS.get(window, ETF_WINDOWS[ETF_DEFAULT_WINDOW])

        try:
            frame = hub.etf_flows()
        except Exception as exc:
            return (build_etf_chart(_VIDE, maximized=maximized),
                    html.Span(f"source indisponible : {exc}",
                              style={"color": C["muted"]}), style)

        # La révision suit la fenêtre et l'agrandissement : changer l'un
        # ou l'autre doit recadrer, un rafraîchissement de cinq minutes
        # non — c'est ce qui préserve le zoom pendant une séance.
        return (
            build_etf_chart(frame, days, maximized=maximized,
                            uirevision=f"etf:{window}:{maximized}"),
            _badges(etf_stats(frame, days), window, verbose=maximized),
            style,
        )
