"""
Panneau alertes : ce qui a sonné, et les seuils qui font sonner.

Le moteur (btcterm/alerts.py) tourne dans le hub, navigateur ouvert ou
non ; ce panneau n'est que sa fenêtre — la liste des sonneries — et son
tableau de réglages. Les réglages vivent dans le Store `alert-config`
(localStorage) : ils survivent au rechargement, et un callback les
pousse au moteur à chaque changement comme au chargement — après un
redémarrage du serveur, le premier navigateur venu réarme donc les
seuils.

Les réglages affichés à l'ouverture du panneau viennent du **moteur**,
pas du Store : le layout est construit au montage, côté serveur, et le
moteur — réarmé depuis le Store au chargement de la page — est la seule
source que le serveur puisse lire à ce moment-là. Un callback dont
l'entrée est le Store ne se rejouerait pas au montage : il a déjà tiré
au chargement, sorties absentes, et son entrée n'a pas changé depuis.
Le Store ne pilote donc que ce qui bouge après coup : les puces des
seuils de prix, re-rendues à chaque pose et retrait.

La sonnerie côté navigateur — bip et notification — est un callback
clientside sur le Store `alerts-feed`, global : elle sonne même quand
le panneau est replié derrière son onglet.
"""

from __future__ import annotations

import time

import dash
from dash import ALL, Input, Output, State, dcc, html

from btcterm.alerts import DEFAULT_CONFIG, normalize_config

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Sonneries affichées dans la liste et transmises au navigateur.
ROWS = 30

#: Couleur d'étiquette par règle — celles des panneaux correspondants.
KIND_COLORS = {"price": C["yellow"], "liq": C["red"],
               "funding": C["orange"], "news": C["blue"], "arb": C["green"],
               "trend": C["purple"], "rsi": C["cyan"], "signal": C["green"],
               "dominance": C["yellow"]}

_INPUT = {
    "width": "52px", "background": C["card"], "color": C["text"],
    "border": f"1px solid {C['border']}", "borderRadius": "3px",
    "fontFamily": MONO, "fontSize": "10px", "padding": "1px 4px",
}
_LABEL = {"color": C["muted"], "fontSize": "9px", "fontFamily": MONO}

#: Posé par `register` : le layout lit les réglages en vigueur dans le
#: moteur — construit au montage du panneau, il est le seul à pouvoir
#: montrer l'état restauré sans attendre un changement du Store.
_hub = None


def _field(label, component):
    return html.Span([html.Span(label + " ", style=_LABEL), component],
                     style={"whiteSpace": "nowrap"})


def layout(title=None):
    config = (normalize_config(None) if _hub is None
              else dict(_hub.alerts.config))
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Alertes"),
            html.Span(id="alerts-badge",
                      style={"color": C["muted"], "fontSize": "9px"}),
        ], style=TITLE_STYLE),
        # ── Réglages ────────────────────────────────────────
        html.Div([
            html.Div([
                # `debounce` : la valeur est committée par Entrée ou au
                # blur — que le clic sur « poser » provoque de lui-même.
                _field("seuil de cours ($)", dcc.Input(
                    id="alert-price-input", type="number", min=0,
                    debounce=True, style=_INPUT)),
                html.Button("poser", id="alert-price-add",
                            className="layout-button",
                            style={"padding": "1px 8px", "fontSize": "9px"}),
                html.Span([_chip(i, item) for i, item in
                           enumerate(config["price_levels"])],
                          id="alert-price-chips"),
            ], style={"display": "flex", "gap": "6px", "alignItems": "center",
                      "flexWrap": "wrap", "marginBottom": "4px"}),
            html.Div([
                _field("liq. 5 min (M$)", dcc.Input(
                    id="alert-liq", type="number", min=0.1, debounce=True,
                    value=config["liq_burst_musd"], style=_INPUT)),
                _field("financement (%/8 h)", dcc.Input(
                    id="alert-funding", type="number", min=0.001,
                    debounce=True, value=config["funding_pct"], style=_INPUT)),
                _field("news (score)", dcc.Input(
                    id="alert-news", type="number", min=1, max=100,
                    debounce=True, value=config["news_score"], style=_INPUT)),
                _field("arb. net (%)", dcc.Input(
                    id="alert-arb", type="number", min=0.01, debounce=True,
                    value=config["arb_net_pct"], style=_INPUT)),
                _field("écart MA200 (%)", dcc.Input(
                    id="alert-ma200", type="number", min=0.1, debounce=True,
                    value=config["ma200_gap_pct"], style=_INPUT)),
                _field("RSI ⩾", dcc.Input(
                    id="alert-rsi-hi", type="number", min=1, max=100,
                    debounce=True, value=config["rsi_overbought"],
                    style=_INPUT)),
                _field("⩽", dcc.Input(
                    id="alert-rsi-lo", type="number", min=1, max=100,
                    debounce=True, value=config["rsi_oversold"],
                    style=_INPUT)),
                _field("dominance 24 h (pts)", dcc.Input(
                    id="alert-dominance", type="number", min=0.1,
                    debounce=True, value=config["dominance_shift_pts"],
                    style=_INPUT)),
                dcc.Checklist(
                    id="alert-signal",
                    options=[{"label": "signaux ±2", "value": "on"}],
                    value=["on"] if config["signal_strong"] else [],
                    inline=True, className="tf-check",
                    style={"fontSize": "9px", "display": "inline-block"}),
                dcc.Checklist(
                    id="alert-sound", options=[{"label": "son", "value": "on"}],
                    value=["on"] if config["sound"] else [],
                    inline=True, className="tf-check",
                    style={"fontSize": "9px", "display": "inline-block"}),
                html.Button("notifications navigateur", id="alert-notify",
                            className="layout-button",
                            style={"padding": "1px 8px", "fontSize": "9px"}),
            ], style={"display": "flex", "gap": "10px", "alignItems": "center",
                      "flexWrap": "wrap"}),
        ], style={"borderBottom": f"1px solid {C['border']}",
                  "paddingBottom": "6px", "marginBottom": "4px",
                  "flexShrink": "0"}),
        # ── Sonneries ───────────────────────────────────────
        html.Div(id="alerts-list", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _chip(index, item):
    sens = "↑" if item["dir"] == "above" else "↓"
    return html.Span([
        f"{sens} {item['level']:,.0f}",
        html.Span(" ×", id={"type": "alert-del", "index": index},
                  n_clicks=0, style={"cursor": "pointer", "color": C["red"]}),
    ], style={"border": f"1px solid {C['border']}", "borderRadius": "3px",
              "padding": "0 5px", "marginRight": "4px", "fontSize": "9px",
              "fontFamily": MONO, "color": C["yellow"],
              "whiteSpace": "nowrap"})


def _rows(feed):
    if not feed:
        return html.Div("aucune alerte — les seuils veillent",
                        style={"color": C["muted"], "fontFamily": MONO,
                               "fontSize": "11px", "padding": "12px"})
    rows = []
    for when, kind, message in feed:
        rows.append(html.Div([
            html.Span(time.strftime("%H:%M:%S", time.localtime(when)),
                      style={"color": C["muted"], "marginRight": "8px"}),
            html.Span(kind, style={
                "color": KIND_COLORS.get(kind, C["text"]),
                "marginRight": "8px", "fontSize": "9px",
                "textTransform": "uppercase"}),
            html.Span(message),
        ], style={"fontFamily": MONO, "fontSize": "11px",
                  "color": C["text"], "padding": "2px 0",
                  "borderBottom": f"1px solid {C['border']}22"}))
    return rows


def register(app, hub):
    global _hub
    _hub = hub

    # ── Le fil, global : bandeau et Store, panneau visible ou non ──
    @app.callback(
        Output("alerts-feed", "data"),
        Output("hdr-alerts", "children"),
        Output("hdr-alerts", "style"),
        Input("tick-slow", "n_intervals"),
        State("alerts-feed", "data"),
    )
    def _feed(_tick, previous):
        feed = [[a.time, a.kind, a.message] for a in hub.alerts.recent(ROWS)]
        count = hub.alerts.count_since(3600)
        badge = f"🔔 {count}" if count else "🔔"
        style = {"fontFamily": MONO, "fontSize": "11px", "marginRight": "18px",
                 "color": C["yellow"] if count else C["muted"]}
        return (dash.no_update if feed == previous else feed), badge, style

    # ── Le moteur suit le Store — au chargement comme au changement ──
    @app.callback(
        Output("alert-config-sink", "data"),
        Input("alert-config", "data"),
    )
    def _apply(config):
        hub.alerts.configure(config)
        return dash.no_update

    # ── Les puces suivent le Store (pose et retrait des seuils) ──
    @app.callback(
        Output("alert-price-chips", "children"),
        Input("alert-config", "data"),
        prevent_initial_call=True,
    )
    def _chips(config):
        config = normalize_config(config)
        return [_chip(i, item) for i, item in
                enumerate(config["price_levels"])]

    # ── Store ← réglages modifiés ──
    @app.callback(
        Output("alert-config", "data"),
        Input("alert-price-add", "n_clicks"),
        Input({"type": "alert-del", "index": ALL}, "n_clicks"),
        Input("alert-liq", "value"),
        Input("alert-funding", "value"),
        Input("alert-news", "value"),
        Input("alert-arb", "value"),
        Input("alert-ma200", "value"),
        Input("alert-rsi-hi", "value"),
        Input("alert-rsi-lo", "value"),
        Input("alert-dominance", "value"),
        Input("alert-signal", "value"),
        Input("alert-sound", "value"),
        State("alert-price-input", "value"),
        State("alert-config", "data"),
        prevent_initial_call=True,
    )
    def _edit(_add, _dels, liq, funding, news_score, arb,
              ma200, rsi_hi, rsi_lo, dominance, signal, sound, level, stored):
        trigger = dash.ctx.triggered_id
        # Un composant qui vient d'être monté déclenche aussi ce
        # callback (n_clicks 0, valeurs du Store) : sans valeur de
        # déclenchement, rien à écrire.
        if trigger is None:
            return dash.no_update
        config = normalize_config(stored)

        if trigger == "alert-price-add":
            # Au montage du panneau, Dash rejoue ce callback avec le
            # bouton pour déclencheur et n_clicks à None — ce n'est pas
            # un clic, même garde que pour les onglets.
            if not dash.ctx.triggered[0]["value"]:
                return dash.no_update
            # Le sens est figé à la pose, par rapport au cours du
            # moment — ou au dernier connu : poser un seuil ne doit pas
            # exiger un flux vivant à l'instant du clic.
            price = hub.reference_price() or next(
                (book.mid for book in hub.books.values() if book.mid), None)
            if not level or not price or level <= 0:
                return dash.no_update
            item = {"level": float(level),
                    "dir": "above" if level >= price else "below"}
            if item not in config["price_levels"]:
                config["price_levels"].append(item)
        elif isinstance(trigger, dict) and trigger.get("type") == "alert-del":
            if not dash.ctx.triggered[0]["value"]:
                return dash.no_update
            index = trigger["index"]
            if 0 <= index < len(config["price_levels"]):
                config["price_levels"].pop(index)
        else:
            merged = {**config,
                      "liq_burst_musd": liq, "funding_pct": funding,
                      "news_score": news_score, "arb_net_pct": arb,
                      "ma200_gap_pct": ma200, "rsi_overbought": rsi_hi,
                      "rsi_oversold": rsi_lo,
                      "dominance_shift_pts": dominance,
                      "signal_strong": bool(signal),
                      "sound": bool(sound)}
            config = normalize_config(merged)

        if config == normalize_config(stored):
            return dash.no_update
        return config

    # ── La liste (rendue seulement quand le panneau est monté) ──
    @app.callback(
        Output("alerts-list", "children"),
        Output("alerts-badge", "children"),
        Input("alerts-feed", "data"),
    )
    def _list(feed):
        count = len(feed or [])
        return _rows(feed), (f"{count} sonnerie(s) gardée(s)" if count
                             else "rien n'a sonné")

    # ── Notifications : la permission se demande d'un geste ──
    app.clientside_callback(
        """
        async function (clicks) {
            if (!("Notification" in window)) {
                return "notifications indisponibles";
            }
            if (clicks) { await Notification.requestPermission(); }
            const state = Notification.permission;
            return state === "granted" ? "notifications ✓"
                 : state === "denied" ? "notifications refusées"
                 : "notifications navigateur";
        }
        """,
        Output("alert-notify", "children"),
        Input("alert-notify", "n_clicks"),
    )

    # ── La sonnerie : bip et notification sur les alertes fraîches ──
    # Sur le Store global : elle sonne même panneau replié. Le premier
    # passage arme sans sonner — recharger la page ne rejoue rien.
    app.clientside_callback(
        """
        function (feed, config) {
            const no = window.dash_clientside.no_update;
            if (!feed || !feed.length) { return no; }
            if (window._alertsLast === undefined) {
                window._alertsLast = feed[0][0];
                return no;
            }
            const fresh = feed.filter(a => a[0] > window._alertsLast);
            if (!fresh.length) { return no; }
            window._alertsLast = feed[0][0];
            if (!config || config.sound !== false) {
                try {
                    const Audio = window.AudioContext
                        || window.webkitAudioContext;
                    const ctx = window._alertsAudio
                        = window._alertsAudio || new Audio();
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.frequency.value = 880;
                    gain.gain.value = 0.04;
                    osc.start();
                    osc.stop(ctx.currentTime + 0.18);
                } catch (e) {}
            }
            if (window.Notification && Notification.permission === "granted") {
                for (const alert of fresh.slice(0, 3)) {
                    new Notification("\\u20bf BTC Terminal", {body: alert[2]});
                }
            }
            return no;
        }
        """,
        Output("alerts-feed-sink", "data"),
        Input("alerts-feed", "data"),
        State("alert-config", "data"),
    )
