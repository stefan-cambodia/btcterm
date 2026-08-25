"""
Panneau liquidations : les positions fermées de force.

Une liquidation est une position à levier que la plateforme ferme au
marché faute de marge. Elles arrivent par rafales, et ces rafales
expliquent une partie des mèches du graphique du cours — c'est pour cela
qu'elles se lisent à côté du carnet, pas dans un onglet de navigateur.

Panneau « rapide » : il ne fait aucun appel réseau, il lit la fenêtre
glissante que le fil du hub entretient en mémoire.

**Le silence est normal.** Le flux est épisodique : plusieurs minutes
sans une seule liquidation ne signalent aucune panne, et le panneau
distingue les deux — un flux coupé le dit, un flux calme aussi.
"""

from __future__ import annotations

import time
from datetime import datetime

from dash import Input, Output, html

from ..theme import C, MONO, PANEL_STYLE, TABLE_STYLE, TITLE_STYLE

#: Lignes affichées dans la grille ; le plein écran en montre davantage.
ROWS = 8
ROWS_MAX = 24

#: Fenêtre des totaux affichés dans la barre de titre.
WINDOW = 3600

SIDE_LABEL = {"long": ("LONG", C["red"]), "short": ("SHORT", C["green"])}

#: Étiquette courte de la plateforme, dans la table — les mêmes
#: abréviations que le sélecteur du carnet.
EXCHANGE_TAG = {"Binance": "BIN", "Bybit": "BYB"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Liquidations"),
            html.Span(id="liq-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        html.Div(id="liq-table", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _montant(valeur: float) -> str:
    """Montant en dollars, abrégé — les liquidations vont du k$ au M$."""
    if valeur >= 1e6:
        return f"{valeur / 1e6:.2f} M$"
    if valeur >= 1e3:
        return f"{valeur / 1e3:.0f} k$"
    return f"{valeur:.0f} $"


def _badges(feed):
    """Totaux de l'heure, part du Bitcoin, et état du flux."""
    if not feed.connected:
        return html.Span(f"flux coupé{' · ' + feed.error if feed.error else ''}",
                         style={"color": C["red"]})

    # Un lien sur deux qui manque se dit, en queue de badge : le fil
    # reste vivant, mais ne voit qu'une plateforme.
    manque = feed.missing()
    sans = ([html.Span(" · sans " + ", ".join(manque),
                       style={"color": C["red"]},
                       title=feed.error or "lien non établi")]
            if manque else [])

    totals = feed.totals(WINDOW)
    if not totals["count"]:
        return html.Span([html.Span("aucune liquidation depuis une heure",
                                    style={"color": C["muted"]})] + sans,
                         style={"fontFamily": MONO})

    return html.Span([
        html.Span("1 h · ", style={"color": C["muted"]}),
        html.Span(f"longs {_montant(totals['long'])}",
                  style={"color": C["red"]},
                  title="positions longues fermées de force"),
        html.Span(" · ", style={"color": C["muted"]}),
        html.Span(f"shorts {_montant(totals['short'])}",
                  style={"color": C["green"]},
                  title="positions courtes fermées de force"),
        html.Span(" · dont BTC ", style={"color": C["muted"]}),
        html.Span(_montant(totals["btc"]), style={"color": C["yellow"]},
                  title="part des paires Bitcoin dans le total"),
    ] + sans, style={"fontFamily": MONO})


def _row(event):
    """Une liquidation : heure, plateforme, paire, côté liquidé, prix, montant."""
    label, color = SIDE_LABEL.get(event.side, ("?", C["muted"]))
    cell = {"padding": "1px 6px", "whiteSpace": "nowrap"}
    return html.Tr([
        html.Td(datetime.fromtimestamp(event.time).strftime("%H:%M:%S"),
                style={**cell, "color": C["muted"]}),
        html.Td(EXCHANGE_TAG.get(event.exchange, event.exchange[:3].upper()),
                style={**cell, "color": C["muted"], "fontSize": "9px"},
                title=event.exchange),
        html.Td(event.symbol.replace("USDT", ""),
                style={**cell, "color": C["text"]}),
        html.Td(label, style={**cell, "color": color, "fontWeight": "600"}),
        html.Td(f"{event.price:,.4g}",
                style={**cell, "color": C["muted"], "textAlign": "right"}),
        html.Td(_montant(event.notional),
                style={**cell, "color": color, "textAlign": "right"}),
    ])


def render(hub, expanded: bool):
    """Table et badges des liquidations, prêts à poser dans le panneau.

    Fonction pure vis-à-vis du rendu, partagée entre le callback Dash et
    le pousseur WebSocket.
    """
    feed = hub.liquidations
    limit = ROWS_MAX if expanded else ROWS
    events = feed.latest(limit)

    if not events:
        age = feed.last_event_age()
        attente = ("en attente du flux…" if not feed.connected else
                   "marché calme — aucune liquidation reçue"
                   if age is None else
                   f"rien depuis {int(age // 60)} min")
        table = html.Div(attente, style={
            "color": C["muted"], "fontFamily": MONO,
            "fontSize": "11px", "padding": "12px"})
    else:
        table = html.Table([html.Tbody([_row(e) for e in events])],
                           style=TABLE_STYLE)

    return table, _badges(feed)


def register(app, hub):
    @app.callback(
        Output("liq-table", "children"),
        Output("liq-badges", "children"),
        Input("tick-fast", "n_intervals"),
        # `expanded` désigne le panneau agrandi, plus sa cellule : depuis
        # la disposition configurable, les liquidations peuvent vivre
        # ailleurs que dans la cellule de l'arbitrage.
        Input("expanded", "data"),
    )
    def _refresh(_tick, expanded):
        return render(hub, expanded == "liq")
