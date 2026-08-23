"""Panneau prix : chandeliers, indicateurs, profil de volume.

Deux rendus cohabitent le temps de la bascule (feuille de route, voie A) :

- Plotly, l'historique : figure recalculée côté serveur à chaque tour
  d'horloge ;
- Lightweight Charts, sous drapeau `BTCTERM_LWC=1` (ou `--lwc`) : le
  serveur ne sert que des données (`/api/klines`, terminal/lwc.py), le
  navigateur dessine — crosshair natif, ligne de dernier prix, canvas.

La barre de titre et ses réglages persistés sont les mêmes dans les deux
régimes ; seul le corps du panneau et le canal de rafraîchissement
changent.
"""

from __future__ import annotations

import os

from dash import Input, Output, State, dcc, html

from ..charts import build_price_chart, prepare_price_frame
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

_BTN = {
    "background": "transparent", "color": C["muted"],
    "border": f"1px solid {C['border']}", "borderRadius": "3px",
    "padding": "2px 9px", "cursor": "pointer", "fontSize": "10px",
    "fontFamily": MONO, "marginLeft": "3px",
}

#: Valeurs d'environnement tenues pour « vrai » — les mêmes que wsgi.py.
_TRUE = {"1", "true", "yes", "oui", "on"}


def lwc_enabled() -> bool:
    """Le rendu Lightweight Charts est-il demandé ?

    Lu à chaque appel plutôt que figé à l'import : le drapeau `--lwc`
    de la ligne de commande pose la variable avant `create_app`, et les
    tests doivent pouvoir basculer sans recharger le module.
    """
    return os.environ.get("BTCTERM_LWC", "").strip().lower() in _TRUE


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
        _body(),
    ], style=PANEL_STYLE)


def _body():
    """Le corps du panneau selon le régime de rendu.

    Version Lightweight Charts : un simple div — le graphique est créé
    dedans par assets/lwc-price.js — accompagné de deux Stores : la
    configuration que le serveur veut transmettre au client (thème et
    profondeurs d'historique, une seule définition, ici), et le puits
    qu'exige le callback clientside.
    """
    if lwc_enabled():
        return html.Div([
            html.Div(id="price-lwc",
                     style={"flex": "1", "minHeight": "0",
                            "position": "relative"}),
            dcc.Store(id="lwc-config",
                      data={"theme": C, "mono": MONO, "intervals": INTERVALS}),
            dcc.Store(id="lwc-sink"),
            dcc.Store(id="lwc-poll-sink"),
        ], style={"flex": "1", "minHeight": "0", "display": "flex",
                  "flexDirection": "column"})
    return dcc.Graph(
        id="price-chart",
        style={"flex": "1", "minHeight": "0"},
        config={
            "scrollZoom": True,
            "displaylogo": False,
            "modeBarButtonsToRemove": ["select2d", "lasso2d"],
        },
    )


def register(app, hub):
    if lwc_enabled():
        _register_lwc(app)
        return

    @app.callback(
        Output("price-chart", "figure"),
        Input("tick-slow", "n_intervals"),
        Input("price-interval", "value"),
        Input("price-currency", "value"),
        Input("price-scale", "value"),
        Input("price-extras", "value"),
        Input("maximized", "data"),
    )
    def _refresh(_tick, interval, currency, scale, extras, maximized):
        extras = extras or []
        log_scale = "log" in (scale or [])
        interval = interval if interval in INTERVALS else DEFAULT_INTERVAL
        df = prepare_price_frame(hub.klines(interval, limit=INTERVALS[interval]))
        rate = hub.eur_rate() if currency == "EUR" else 1.0

        # La clé de révision décrit la série et la structure du graphique :
        # elle ne change pas au rafraîchissement, ni au passage en plein
        # écran — le zoom en cours survit donc à l'agrandissement — mais
        # change quand le contenu affiché change, ce qui recadre à propos.
        revision = (f"{interval}:{currency}:{'log' if log_scale else 'lin'}"
                    f":{','.join(sorted(extras))}")

        return build_price_chart(
            df, currency, rate,
            uirevision=revision,
            subpanels=tuple(e for e in extras if e != "profile"),
            profile="profile" in extras,
            maximized=(maximized == "price"),
            log_scale=log_scale,
        )


def _register_lwc(app):
    """Relie les réglages de la barre de titre au rendu Lightweight Charts.

    Un seul callback clientside : il relaie l'état des sélecteurs à
    `window.lwcPrice` (assets/lwc-price.js), qui décide seul de ce qui
    en découle — refetch de `/api/klines` au changement d'intervalle,
    simple bascule locale pour la devise ou l'échelle. Aucun aller-retour
    serveur pour un réglage d'affichage : c'est le principe du régime.

    Le callback refait aussi surface quand le panneau est re-rendu — un
    déménagement de cellule remonte les sélecteurs — et lwc-price.js y
    reconstruit son graphique dans le div neuf.
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

    # Repli poll : au rythme de l'horloge lente — celui qu'avait le
    # rendu Plotly — le client recharge la dernière page. lwc-price.js
    # s'abstient tant que le WebSocket /push tient : c'est le badge
    # « push / poll » du bandeau qui dit le canal réellement actif.
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
