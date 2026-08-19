"""
Panneau on-chain : la sécurité du réseau et son coût.

Le hashrate mesure la puissance de calcul branchée sur le réseau, la
difficulté l'effort exigé pour trouver un bloc. La première bouge en
continu, la seconde par paliers tous les 2 016 blocs : quand le hashrate
s'éloigne durablement de sa marche, le sens du prochain ajustement est
déjà écrit.

Le rythme des blocs vient de la même distance : au-dessus de dix minutes,
le réseau a perdu des mineurs depuis le dernier ajustement ; en dessous,
il en a gagné.

Source : blockchain.info, sans clé. mempool.space aurait été plus riche,
mais ne répond pas de façon fiable ici (voir `sources.BLOCKCHAIN_CHARTS`).
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from ..charts import build_chain_chart
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Fenêtre des séries. Un an montre un cycle de difficulté complet sans
#: écraser les paliers récents.
TIMESPAN = "1year"

#: Rythme visé par le protocole, en minutes par bloc.
TARGET_BLOCK_MINUTES = 10


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Réseau on-chain"),
            html.Span(id="onchain-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        dcc.Graph(
            id="onchain-chart",
            style={"flex": "1", "minHeight": "0"},
            config={"scrollZoom": True, "displaylogo": False,
                    "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
        ),
    ], style=PANEL_STYLE)


def _badges(stats: dict, mempool):
    """Hashrate, difficulté, rythme des blocs et taille du mempool."""
    if not stats:
        return html.Span("réseau indisponible", style={"color": C["muted"]})

    children = [
        html.Span(f"{stats['hash_rate_ghs'] / 1e9:,.0f} EH/s",
                  style={"color": C["green"]},
                  title="puissance de calcul branchée sur le réseau"),
        html.Span(" · diff ", style={"color": C["muted"]}),
        html.Span(f"{stats['difficulty'] / 1e12:.1f} T",
                  style={"color": C["purple"]},
                  title="effort exigé pour trouver un bloc"),
    ]

    minutes = stats.get("minutes_between_blocks")
    if minutes:
        # Le protocole vise dix minutes ; s'en écarter dit que le réseau
        # a gagné ou perdu des mineurs depuis le dernier ajustement.
        couleur = (C["muted"] if abs(minutes - TARGET_BLOCK_MINUTES) < 0.5
                   else C["orange"])
        children += [
            html.Span(" · bloc ", style={"color": C["muted"]}),
            html.Span(f"{minutes:.1f} min", style={"color": couleur},
                      title="temps moyen entre deux blocs (cible : 10 min)"),
        ]

    if mempool is not None and not mempool.empty:
        children += [
            html.Span(" · mempool ", style={"color": C["muted"]}),
            html.Span(f"{mempool['value'].iloc[-1] / 1e6:.0f} Mo",
                      style={"color": C["cyan"]},
                      title="transactions en attente de confirmation"),
        ]
    return html.Span(children, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("onchain-chart", "figure"),
        Output("onchain-badges", "children"),
        Input("tick-rare", "n_intervals"),
        Input("maximized", "data"),
    )
    def _refresh(_tick, maximized):
        hashrate = hub.chain_chart("hash-rate", TIMESPAN)
        difficulty = hub.chain_chart("difficulty", TIMESPAN)
        mempool = hub.chain_chart("mempool-size", "5weeks")

        return (
            build_chain_chart(hashrate, difficulty,
                              maximized=(maximized == "macro")),
            _badges(hub.chain_stats(), mempool),
        )
