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

Le hashprice dit la paie du mineur : ce que rapporte un PH/s de calcul par
jour, émission et frais au cours du jour (§ cycle.hashprice). En plein
écran, sa courbe sous le hashrate.

Source : blockchain.info, sans clé. mempool.space aurait été plus riche,
mais ne répond pas de façon fiable ici (voir `sources.BLOCKCHAIN_CHARTS`).
Le hashprice vient de Coin Metrics (CC BY-NC), nommé à l'écran.
"""

from __future__ import annotations

import time

from dash import Input, Output, dcc, html

from btcterm import cycle

from ..charts import placeholder, build_chain_chart
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Fenêtre des séries. Un an montre un cycle de difficulté complet sans
#: écraser les paliers récents.
TIMESPAN = "1year"
#: Le hashrate se lit sur deux ans : la moyenne de soixante jours des hash
#: ribbons doit être pleine dès le premier jour de l'année affichée.
RIBBONS_SPAN = "2years"

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
            id="onchain-chart", figure=placeholder(),
            style={"flex": "1", "minHeight": "0"},
            config={"scrollZoom": True, "displaylogo": False,
                    "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
        ),
    ], style=PANEL_STYLE)


#: Frais au-delà desquels la chaîne est disputée, puis saturée, en sat/vB :
#: l'ordinaire tient sous dix, les ruées de 2023 et 2024 ont dépassé cent.
FEES_BUSY = 20.0
FEES_RUSH = 80.0


def _frais(valeur: float) -> str:
    """Un taux lisible du centième à la centaine de sat/vB."""
    return f"{valeur:.2f}" if valeur < 1 else (
        f"{valeur:.1f}" if valeur < 10 else f"{valeur:.0f}")


def _badges(stats: dict, mempool, fees: dict | None = None,
            epoch: dict | None = None, hashprice: dict | None = None,
            verbose: bool = False):
    """Hashrate, difficulté, rythme des blocs, prochain ajustement, mempool,
    frais — et, en plein écran, le hashprice : la barre de titre de la
    grille tient tout juste les six premiers, et le débordait de 123 px."""
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

    if epoch:
        # Le prochain ajustement, au rythme de la période en cours : un
        # écart de 5 % ou plus se voit, le reste est le bruit des blocs.
        change = epoch["change_pct"]
        jours = epoch["blocks_left"] * epoch["avg_minutes"] / 1440
        quand = time.strftime("%d/%m", time.localtime(epoch["eta"]))
        children += [
            html.Span(" · ajust. ", style={"color": C["muted"]}),
            html.Span(f"{change:+z.1f} % J-{max(0, round(jours))}",
                      style={"color": C["orange"] if abs(change) >= 5
                             else C["muted"]},
                      title=f"prochain ajustement de difficulté dans "
                            f"{epoch['blocks_left']} blocs (≈ {jours:.1f} j, "
                            f"le {quand}) : la période en cours tourne à "
                            f"{epoch['avg_minutes']:.2f} min par bloc ; à ce "
                            f"rythme, la difficulté changera de {change:+z.1f} %"),
        ]

    if mempool is not None and not mempool.empty:
        children += [
            html.Span(" · mempool ", style={"color": C["muted"]}),
            html.Span(f"{mempool['value'].iloc[-1] / 1e6:.0f} Mo",
                      style={"color": C["cyan"]},
                      title="transactions en attente de confirmation"),
        ]

    if fees:
        prochain = fees["next"]
        couleur = (C["red"] if prochain >= FEES_RUSH else
                   C["orange"] if prochain >= FEES_BUSY else C["cyan"])
        children += [
            html.Span(" · frais ", style={"color": C["muted"]}),
            html.Span(f"{_frais(prochain)} sat/vB", style={"color": couleur},
                      title=f"frais pour le prochain bloc ; "
                            f"{_frais(fees['hour'])} sat/vB dans l'heure, "
                            f"{_frais(fees['day'])} dans la journée — "
                            "la demande d'espace dans les blocs"),
        ]

    if hashprice and verbose:
        change = hashprice["change_30d"]
        children += [
            html.Span(" · hashprice ", style={"color": C["muted"]}),
            html.Span(f"{hashprice['hashprice']:.1f} $/PH/j",
                      style={"color": C["orange"] if hashprice["at_low"]
                             else C["yellow"]},
                      title=f"ce que rapporte un PH/s de calcul par jour, "
                            f"moyenne de sept jours au "
                            f"{hashprice['date']:%d/%m/%Y}"
                            + (f" ; {change:+z.0f} % sur trente jours"
                               if change is not None else "")
                            + f" ; plus bas de l'année "
                            f"{hashprice['low_365']:.1f}"
                            + (" — il y est : les mineurs les moins "
                               "efficaces s'éteignent" if hashprice["at_low"]
                               else "")
                            + f" ; frais {hashprice['fee_share']:.1f} % du "
                            "revenu — données Coin Metrics, CC BY-NC"),
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
        hashrate = hub.chain_chart("hash-rate", RIBBONS_SPAN)
        difficulty = hub.chain_chart("difficulty", TIMESPAN)
        mempool = hub.chain_chart("mempool-size", "5weeks")
        try:
            epoch = hub.difficulty_epoch()
        except Exception:
            epoch = None
        daily = hub.mvrv()

        return (
            build_chain_chart(hashrate, difficulty,
                              maximized=(maximized == "macro"),
                              ribbons=cycle.hash_ribbons(hashrate),
                              days=365, hashprice=cycle.hashprice(daily)),
            _badges(hub.chain_stats(), mempool, hub.chain_fees(), epoch,
                    cycle.hashprice_state(daily),
                    verbose=(maximized == "macro")),
        )
