"""Panneau prix : chandeliers, indicateurs, profil de volume.

Rendu Lightweight Charts (TradingView, vendoré dans assets/vendor/) :
le serveur ne sert que des données — `/api/klines` et `/api/profile`
(terminal/lwc.py), plus la mutation temps réel du canal `/push` — et le
navigateur dessine sur canvas : crosshair aimanté, ligne du dernier
prix, panes natifs pour RSI et CRSI, historique chargé à la volée au
pan, profil de volume de la plage visible.

Le serveur reste la seule source de vérité pour les indicateurs ; ce
module ne fait que poser la barre de titre — dont les réglages persistés
survivent au rechargement — et relayer ses sélecteurs au client
(assets/lwc-price.js) par des callbacks clientside : aucun aller-retour
serveur pour un réglage d'affichage.
"""

from __future__ import annotations

from dash import Input, Output, State, dcc, html

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Intervalles proposés, chacun avec le nombre de bougies chargées.
#: La palette large — de la bougie de quinze minutes à la mensuelle —
#: vient de `btc_dashboard2.py`, dont c'était le seul apport sur le
#: panneau prix. Les profondeurs d'historique suivent l'échelle : de quoi
#: nourrir la MA 200 en intraday, sans tirer trente ans de mensuelles.
INTERVALS = {
    "15m": 300, "30m": 300, "1h": 350, "4h": 350, "6h": 300,
    "12h": 300, "1d": 365, "1w": 260, "1M": 120,
}
DEFAULT_INTERVAL = "1d"

#: Sous-graphiques et profil de volume, décochables. Le cours récupère la
#: hauteur libérée : c'est lui qu'on vient lire en séance d'analyse.
EXTRAS = [
    {"label": "RSI", "value": "rsi"},
    {"label": "CRSI", "value": "crsi"},
    {"label": "VOL", "value": "volume"},
    {"label": "PROFIL", "value": "profile"},
]
DEFAULT_EXTRAS = ["rsi", "volume", "profile"]


def layout(title=None):
    return html.Div([
        html.Div([
            # Titre court : la barre porte neuf intervalles, la devise,
            # l'échelle et quatre sous-graphiques ; un intitulé plus long
            # les faisait passer à la ligne dans la largeur de la grille.
            title if title is not None else
            html.Span("BTC/USDT", style={"fontSize": "9px",
                                         "letterSpacing": "0.02em",
                                         "whiteSpace": "nowrap"}),
            # `persistence` : les réglages survivent au rechargement de la
            # page (localStorage) — on ne reconfigure pas sa station de
            # travail à chaque session.
            html.Div([
                dcc.RadioItems(
                    id="price-interval",
                    options=[{"label": k, "value": k} for k in INTERVALS],
                    value=DEFAULT_INTERVAL, inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px"},
                ),
                dcc.RadioItems(
                    id="price-currency",
                    # Symboles plutôt que codes : trois lettres par devise
                    # faisaient passer la barre de titre à la ligne.
                    options=[{"label": "$", "value": "USD"},
                             {"label": "€", "value": "EUR"}],
                    value="USD", inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "10px"},
                ),
                dcc.Checklist(
                    id="price-scale",
                    options=[{"label": "LOG", "value": "log"}],
                    value=[], inline=True, className="tf-check",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "10px"},
                ),
                dcc.Checklist(
                    id="price-extras",
                    options=EXTRAS, value=DEFAULT_EXTRAS,
                    inline=True, className="tf-check",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "10px"},
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap"}),
        ], style=TITLE_STYLE),
        # Le graphique est créé dans ce div par assets/lwc-price.js ; les
        # deux Stores portent la configuration que le serveur transmet au
        # client (thème et profondeurs d'historique — une seule
        # définition, ici) et les puits qu'exigent les callbacks
        # clientside.
        html.Div([
            html.Div(id="price-lwc",
                     style={"flex": "1", "minHeight": "0",
                            "position": "relative"}),
            dcc.Store(id="lwc-config",
                      data={"theme": C, "mono": MONO, "intervals": INTERVALS}),
            dcc.Store(id="lwc-sink"),
            dcc.Store(id="lwc-poll-sink"),
            dcc.Store(id="lwc-alert-sink"),
        ], style={"flex": "1", "minHeight": "0", "display": "flex",
                  "flexDirection": "column"}),
    ], style=PANEL_STYLE)


def register(app, hub):
    """Relie les réglages de la barre de titre au rendu Lightweight Charts.

    Un seul callback clientside pour les sélecteurs : il relaie leur état
    à `window.lwcPrice` (assets/lwc-price.js), qui décide seul de ce qui
    en découle — refetch de `/api/klines` au changement d'intervalle,
    simple bascule locale pour la devise ou l'échelle. Le callback refait
    aussi surface quand le panneau est re-rendu — un déménagement de
    cellule remonte les sélecteurs — et lwc-price.js y reconstruit son
    graphique dans le div neuf.

    `hub` n'est pas utilisé ici : les données passent par `/api/klines`
    et le canal `/push`, jamais par un callback de ce panneau.
    """
    app.clientside_callback(
        """
        function (interval, currency, scale, extras, maximized, config) {
            if (window.lwcPrice) {
                window.lwcPrice.configure({
                    interval: interval,
                    currency: currency,
                    log: (scale || []).includes('log'),
                    extras: extras || [],
                    maximized: maximized === 'price',
                }, config);
            }
            return window.dash_clientside.no_update;
        }
        """,
        Output("lwc-sink", "data"),
        Input("price-interval", "value"),
        Input("price-currency", "value"),
        Input("price-scale", "value"),
        Input("price-extras", "value"),
        Input("maximized", "data"),
        State("lwc-config", "data"),
    )

    # Repli poll : au rythme de l'horloge lente, le client recharge la
    # dernière page. lwc-price.js s'abstient tant que le WebSocket /push
    # tient : c'est le badge « push / poll » du bandeau qui dit le canal
    # réellement actif.
    app.clientside_callback(
        """
        function (tick) {
            if (window.lwcPrice) { window.lwcPrice.poll(); }
            return window.dash_clientside.no_update;
        }
        """,
        Output("lwc-poll-sink", "data"),
        Input("tick-slow", "n_intervals"),
    )

    # Les seuils de cours du panneau ALERTES gagnent une ligne dédiée
    # sur le graphique — le Store alert-config est global et persisté,
    # les lignes suivent chaque pose et retrait, restauration comprise.
    app.clientside_callback(
        """
        function (config) {
            if (window.lwcPrice) {
                window.lwcPrice.alerts(
                    (config && config.price_levels) || []);
            }
            return window.dash_clientside.no_update;
        }
        """,
        Output("lwc-alert-sink", "data"),
        Input("alert-config", "data"),
    )
