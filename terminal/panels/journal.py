"""
Panneau journal : la séance se relit sans quitter le terminal.

`python -m btcterm.journal` relit la séance à la ligne de commande ;
c'était le dernier usage qui obligeait à sortir du terminal. Ce panneau
est la même relecture, en onglet de la cellule d'arbitrage : les
alertes sonnées, les épisodes d'arbitrage rentables et le bilan des
liquidations des dernières vingt-quatre heures, plus la profondeur de
l'historique d'instantanés dans la barre de titre.

Il ne montre pas le présent — les panneaux voisins s'en chargent — mais
le passé proche : ce qui a sonné pendant qu'on ne regardait pas, ce que
l'arbitrage a réellement donné, ce que la séance a liquidé. Tout vient
du journal SQLite (§ btcterm.journal) ; sans journal (`--no-journal`),
le panneau le dit.
"""

from __future__ import annotations

import time

from dash import Input, Output, html

from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE
from .alerts import KIND_COLORS

#: Fenêtre relue, en heures — la même que le défaut de la CLI.
HOURS = 24

#: Lignes par section dans la grille ; le plein écran en montre plus.
ROWS = 6
ROWS_MAX = 20

_HEAD = {"color": C["muted"], "fontFamily": MONO, "fontSize": "9px",
         "letterSpacing": "1px", "textTransform": "uppercase",
         "padding": "8px 6px 2px", "borderBottom": f"1px solid {C['border']}"}
_EMPTY = {"color": C["muted"], "fontFamily": MONO, "fontSize": "11px",
          "padding": "4px 6px"}
_CELL = {"padding": "1px 6px", "whiteSpace": "nowrap"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Journal"),
            html.Span(id="journal-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        html.Div(id="journal-view", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _montant(valeur: float) -> str:
    if valeur >= 1e6:
        return f"{valeur / 1e6:.2f} M$"
    if valeur >= 1e3:
        return f"{valeur / 1e3:.0f} k$"
    return f"{valeur:.0f} $"


def _quand(ts: float) -> str:
    """Heure seule dans la fenêtre du jour ; la date au-delà n'existe
    pas ici, la fenêtre fait vingt-quatre heures."""
    return time.strftime("%H:%M:%S", time.localtime(ts))


def _section_alertes(rows, limit):
    if not rows:
        return [html.Div("rien n'a sonné", style=_EMPTY)]
    lignes = []
    for row in rows[-limit:][::-1]:
        lignes.append(html.Tr([
            html.Td(_quand(row["ts"]), style={**_CELL, "color": C["muted"]}),
            html.Td(row["kind"], style={
                **_CELL, "color": KIND_COLORS.get(row["kind"], C["text"]),
                "fontSize": "9px", "textTransform": "uppercase"}),
            html.Td(row["message"],
                    style={**_CELL, "color": C["text"],
                           "whiteSpace": "normal"}),
        ]))
    return [html.Table([html.Tbody(lignes)], style=TABLE_STYLE)]


def _section_episodes(rows, limit):
    if not rows:
        return [html.Div("aucun épisode rentable", style=_EMPTY)]
    lignes = []
    for row in rows[-limit:][::-1]:
        duree = row["last_seen"] - row["first_seen"]
        lignes.append(html.Tr([
            html.Td(_quand(row["first_seen"]),
                    style={**_CELL, "color": C["muted"]}),
            html.Td(f"{row['buy_exchange']} → {row['sell_exchange']}",
                    style={**_CELL, "color": C["text"]}),
            html.Td(f"{row['best_net_pct']:+.3f} %",
                    style={**_CELL, "color": C["green"], "textAlign": "right"},
                    title="meilleur profit net observé pendant l'épisode"),
            html.Td(f"{duree:.0f} s",
                    style={**_CELL, "color": C["muted"], "textAlign": "right"},
                    title="durée de l'épisode"),
            html.Td(f"{row['samples']} obs.",
                    style={**_CELL, "color": C["muted"], "textAlign": "right"}),
        ]))
    return [html.Table([html.Tbody(lignes)], style=TABLE_STYLE)]


def _section_liquidations(rows):
    """Un bilan, pas un fil : le panneau LIQUIDATIONS montre déjà les
    événements un à un — ici, ce que la fenêtre pèse et son extrême."""
    if not rows:
        return [html.Div("aucune liquidation journalisée", style=_EMPTY)]
    par_cote = {"long": 0.0, "short": 0.0}
    for row in rows:
        par_cote[row["side"]] += row["notional"]
    gros = max(rows, key=lambda r: r["notional"])
    return [html.Div([
        html.Span(f"{len(rows)} événements · ", style={"color": C["text"]}),
        html.Span(f"longs {_montant(par_cote['long'])}",
                  style={"color": C["red"]}),
        html.Span(" · ", style={"color": C["muted"]}),
        html.Span(f"shorts {_montant(par_cote['short'])}",
                  style={"color": C["green"]}),
        html.Span(
            f" · la plus grosse : {gros['symbol']} {gros['side']} "
            f"{_montant(gros['notional'])} à {_quand(gros['ts'])}",
            style={"color": C["muted"]}),
    ], style={**_EMPTY, "color": C["text"]})]


def render(hub, expanded: bool):
    """Le contenu du panneau et ses badges — pur vis-à-vis de Dash."""
    journal = getattr(hub, "journal", None)
    if journal is None:
        vide = html.Div("journal désactivé (--no-journal)", style=_EMPTY)
        return vide, html.Span("", style={"color": C["muted"]})

    end = time.time()
    start = end - HOURS * 3600
    alerts = journal.alerts_between(start, end)
    episodes = journal.episodes_between(start, end)
    liquidations = journal.liquidations_between(start, end)
    limit = ROWS_MAX if expanded else ROWS

    view = html.Div(
        [html.Div("alertes sonnées", style=_HEAD)]
        + _section_alertes(alerts, limit)
        + [html.Div("épisodes d'arbitrage", style=_HEAD)]
        + _section_episodes(episodes, limit)
        + [html.Div("liquidations", style=_HEAD)]
        + _section_liquidations(liquidations),
        style={"fontFamily": MONO, "fontSize": "11px"})

    #: La profondeur de l'historique d'instantanés se lit ici : c'est la
    #: donnée qui se construit à l'usage, sa croissance mérite d'être vue.
    badges = [html.Span(f"{HOURS} h · ", style={"color": C["muted"]}),
              html.Span(f"{len(alerts)} alertes · {len(episodes)} épisodes"
                        f" · {len(liquidations)} liq.",
                        style={"color": C["text"]})]
    snapshots = journal.snapshots_between(0, end)
    if snapshots:
        depuis = time.strftime("%d/%m", time.localtime(snapshots[0]["ts"]))
        badges += [html.Span(" · instantanés depuis le ",
                             style={"color": C["muted"]}),
                   html.Span(depuis, style={"color": C["cyan"]},
                             title="début de l'historique de marché "
                                   "accumulé localement (§ journal)")]
    return view, html.Span(badges, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("journal-view", "children"),
        Output("journal-badges", "children"),
        # L'horloge lente suffit : le journal s'écrit à la seconde mais
        # se relit pour l'œil, pas pour l'action.
        Input("tick-slow", "n_intervals"),
        Input("expanded", "data"),
    )
    def _refresh(_tick, expanded):
        return render(hub, expanded == "journal")
