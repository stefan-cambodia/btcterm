"""
Panneau perpétuels : financement, open interest et positionnement.

Le carnet dit ce qui se passe au comptant ; ce panneau dit ce que fait
l'effet de levier. Le taux de financement est le loyer que les longs
paient aux shorts toutes les huit heures — ou l'inverse quand il est
négatif —, l'open interest mesure la taille des positions ouvertes, et le
ratio des comptes dit de quel côté se tient la foule.

Les trois se lisent ensemble : un financement élevé sur un open interest
qui gonfle, c'est un marché endetté d'un seul côté, la configuration d'où
sortent les liquidations en cascade.

Rendu Lightweight Charts, comme le panneau prix : le serveur ne sert que
des données — `/api/perp` (terminal/lwc.py) — et le navigateur dessine
(assets/lwc-perp.js) : financement en histogramme signé, open interest
en ligne sur son axe gauche, crosshair commun. Ce module ne pose que la
barre de titre et deux relais clientside — configuration au montage et
au plein écran, poll à l'horloge rare ; ces données bougent par tranches
de 4 à 8 heures, aucun canal push n'est justifié.

Données publiques de Binance Futures, sans clé. Binance ne conserve que
trente jours d'open interest ; au-delà, la série continue sur les
instantanés que le hub journalise (§ journal) — le graphique remonte
donc aussi loin que l'accumulation locale le permet.
"""

from __future__ import annotations

import time

from dash import Input, Output, State, dcc, html

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Un financement de 0,01 % par période (le taux « neutre » de Binance)
#: fait 0,01 × 3 × 365 ≈ 11 % par an : c'est cette conversion qui rend le
#: chiffre lisible, un taux de 0,0001 ne parlant à personne.
PERIODS_PER_YEAR = 3 * 365


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Perpétuel BTC/USDT"),
            html.Span(id="perp-badges",
                      style={"fontSize": "9px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        # Le graphique est créé dans ce div par assets/lwc-perp.js ; le
        # Store porte la configuration que le serveur transmet au client
        # (le thème — une seule définition, ici), les puits sont ceux
        # qu'exigent les callbacks clientside.
        html.Div([
            html.Div(id="perp-lwc",
                     style={"flex": "1", "minHeight": "0",
                            "position": "relative"}),
            dcc.Store(id="lwc-perp-config", data={"theme": C, "mono": MONO}),
            dcc.Store(id="lwc-perp-sink"),
            dcc.Store(id="lwc-perp-poll-sink"),
        ], style={"flex": "1", "minHeight": "0", "display": "flex",
                  "flexDirection": "column"}),
    ], style=PANEL_STYLE)


def _countdown(next_funding_ms: int) -> str:
    """Temps restant avant le prochain financement, en h min."""
    remaining = int(next_funding_ms / 1000 - time.time())
    if remaining < 0:
        return "—"
    return f"{remaining // 3600:d} h {remaining % 3600 // 60:02d}"


def _badges(snapshot: dict, open_interest, verbose: bool = False):
    """Financement courant, échéance, open interest et part des longs.

    Les intitulés sont muets dans la grille — la barre de titre y est
    large de trois cents pixels, et des chiffres qui passent à la ligne
    volent leur hauteur au graphique. Ils reviennent en plein écran,
    et l'infobulle les donne dans les deux cas.
    """
    if not snapshot:
        return html.Span("marché à terme indisponible",
                         style={"color": C["muted"]})

    def label(texte: str):
        return html.Span(texte if verbose else " · ",
                         style={"color": C["muted"]})

    children = []
    rate = snapshot.get("funding_rate")
    if rate is not None:
        color = C["green"] if rate >= 0 else C["red"]
        annuel = rate * PERIODS_PER_YEAR * 100
        children += [
            html.Span("financement " if verbose else "", style={"color": C["muted"]}),
            html.Span(f"{rate * 100:+.4f} %", style={"color": color},
                      title="taux de financement de la période de 8 h"),
            html.Span(f" ({annuel:+.1f} % / an)", style={"color": color},
                      title="le même taux, annualisé"),
        ]
    # Le compte à rebours ne survit qu'en plein écran : dans la grille,
    # la barre de titre est déjà pleine, et c'est le chiffre dont on se
    # passe le plus facilement — les financements tombent à heure fixe.
    if verbose and snapshot.get("next_funding"):
        children += [
            label(" · prochain dans "),
            html.Span(_countdown(snapshot["next_funding"]),
                      style={"color": C["text"]},
                      title="temps restant avant le prochain financement"),
        ]
    if open_interest is not None and not open_interest.empty:
        children += [
            label(" · open interest "),
            html.Span(f"OI {open_interest['oi_usd'].dropna().iloc[-1] / 1e9:.2f} Md$",
                      style={"color": C["cyan"]},
                      title="valeur des positions ouvertes sur le perpétuel"),
        ]
    if snapshot.get("long_account") is not None:
        part = snapshot["long_account"] * 100
        # Au-delà de 60 % de comptes d'un côté, le déséquilibre mérite
        # d'être signalé : c'est le carburant des liquidations.
        color = C["orange"] if part >= 60 or part <= 40 else C["muted"]
        children += [
            label(" · comptes longs "),
            html.Span(f"{'longs ' if not verbose else ''}{part:.0f} %",
                      style={"color": color},
                      title="part des comptes positionnés à la hausse"),
        ]
    return html.Span(children, style={"fontFamily": MONO})


def register(app, hub):
    """Relie la barre de titre au serveur et le rendu LWC au client.

    Les badges restent un callback serveur — un instantané, pas une
    série. Le graphique, lui, vit côté client : `configure` au montage
    du panneau (le callback rejoue quand ses puits remontent) et à
    chaque bascule de plein écran, `poll` à l'horloge rare.
    """
    @app.callback(
        Output("perp-badges", "children"),
        Input("tick-rare", "n_intervals"),
        Input("maximized", "data"),
    )
    def _refresh(_tick, maximized):
        # Le panneau partage la cellule « etf » : c'est donc sur ce nom
        # de zone que se lit l'agrandissement, pas sur le sien.
        return _badges(hub.perp_snapshot(), hub.open_interest_extended(),
                       verbose=maximized == "etf")

    app.clientside_callback(
        """
        function (maximized, config) {
            if (window.lwcPerp) {
                window.lwcPerp.configure(
                    {maximized: maximized === 'etf'}, config);
            }
            return window.dash_clientside.no_update;
        }
        """,
        Output("lwc-perp-sink", "data"),
        Input("maximized", "data"),
        State("lwc-perp-config", "data"),
    )

    app.clientside_callback(
        """
        function (tick) {
            if (window.lwcPerp) { window.lwcPerp.poll(); }
            return window.dash_clientside.no_update;
        }
        """,
        Output("lwc-perp-poll-sink", "data"),
        Input("tick-rare", "n_intervals"),
    )
