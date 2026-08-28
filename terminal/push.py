"""
Pousseur WebSocket des panneaux rapides.

L'interrogation à 250 ms convient à une boucle locale, mais chaque tour
paie un aller-retour HTTP complet : sur un tunnel SSH lointain, la
latence s'ajoute à l'intervalle et le carnet prend du retard. Ce module
inverse le sens du canal — le serveur pousse le rendu, le navigateur ne
demande plus rien.

Le contrat avec `assets/push.js` :

- le navigateur ouvre `ws://…/push`, coupe `tick-fast` et affiche
  « push » dans le bandeau ; si la connexion tombe, il rallume l'horloge
  (« poll ») et retente en arrière-plan — le terminal ne dépend jamais
  du WebSocket, il en profite quand il est là.
- le navigateur annonce son état — plateforme du carnet, panneau
  agrandi — à l'ouverture puis à chaque changement : le serveur rend
  exactement ce que l'interrogation aurait rendu pour le même état.
- le serveur envoie des trames `{id: {prop: valeur}}` que push.js
  applique par `dash_clientside.set_props` : le même chemin de mise à
  jour qu'une réponse de callback, sérialisé par le même encodeur —
  le navigateur ne voit pas la différence entre les deux canaux.
- une cible fait exception : `price-lwc`, la mutation du panneau prix
  en rendu Lightweight Charts. Elle n'existe que si le navigateur
  annonce `price_interval` (le rendu Plotly ne l'annonce pas), ne porte
  que la dernière bougie et les derniers points d'indicateurs, à sa
  propre cadence — et push.js la route vers window.lwcPrice au lieu de
  set_props.

Une trame n'emporte que les panneaux dont le rendu a changé depuis la
précédente : la cadence peut donc être plus serrée que l'horloge
qu'elle remplace sans coûter davantage — un carnet immobile ne transmet
rien du tout.
"""

from __future__ import annotations

import json
import time
import uuid

from dash import Input, Output
from flask import jsonify
from flask_sock import ConnectionClosed, Sock
from plotly.utils import PlotlyJSONEncoder

from btcterm.sources import KLINE_FREQ

from . import lwc
from .panels import arbitrage, liquidations, orderbook
from .panels.price import INTERVALS as PRICE_INTERVALS

#: Cadence du pousseur, plus serrée que REFRESH_FAST_MS : c'est la
#: raison d'être du canal — descendre sous 100 ms sans payer un
#: aller-retour HTTP par tour.
PUSH_INTERVAL = 0.1

#: Cadence de la cible prix — celle de l'horloge lente qu'elle double :
#: les chandeliers viennent d'un cache REST à quelques secondes de TTL,
#: les pousser à 100 ms n'apprendrait rien. Le différentiel fait le
#: reste : un point identique ne repart pas.
PUSH_PRICE_INTERVAL = 2.0

#: Cadence de la profondeur : une figure Plotly entière — cinq courbes
#: cumulées, construites puis sérialisées — que les carnets, jamais
#: immobiles, font repartir à chaque trame. À 100 ms, elle valait la
#: moitié du temps processeur d'un onglet ouvert (profil py-spy : 50
#: échantillons sur 101 dans build_depth_chart), pour une courbe que
#: l'œil ne lit pas dix fois par seconde. La figure a depuis perdu sa
#: validation Plotly (charts.py) et ne coûte plus qu'une milliseconde ;
#: la cadence reste, pour le repli par interrogation comme pour le
#: canal. Le carnet et l'arbitrage, eux, gardent la cadence du canal.
PUSH_DEPTH_INTERVAL = 1.0

#: L'état que le navigateur annonce, et ses valeurs tant qu'il n'a rien
#: dit — les mêmes défauts que la page au chargement. `price_interval`
#: n'a pas de défaut : seul le rendu Lightweight Charts l'annonce, et
#: sans lui le pousseur ne calcule rien pour le panneau prix.
#: Jeton du serveur en marche, tiré au chargement du module — un par
#: processus. La page l'emporte dans une balise meta, `/api/boot` dit
#: celui du serveur qui répond : un onglet chargé avant un redémarrage
#: les trouve différents et se recharge. Sans cela, il continue
#: d'appeler le nouveau serveur avec le graphe de callbacks de l'ancien
#: — le journal du service en a montré la rafale de tracebacks au réveil
#: de la machine, quatre heures après le redémarrage.
BOOT = uuid.uuid4().hex

DEFAULT_STATE = {"exchange": "Binance", "expanded": None,
                 "price_interval": None}


def _frame(hub, state: dict, depth: bool = True) -> dict[str, dict]:
    """Le rendu des panneaux rapides, ciblé par identifiant de composant.

    Exactement ce que les callbacks Dash produiraient pour le même état :
    les fonctions `render` sont partagées, seul le canal change. Les six
    cibles sont rendues — c'est la trame qui écarte celles qui n'ont pas
    changé, et le navigateur celles qui ne sont pas à l'écran — sauf la
    profondeur, que la boucle ne demande qu'à sa cadence (`depth`).
    """
    arb_table, arb_count = arbitrage.render(hub)
    liq_table, liq_badges = liquidations.render(hub, state["expanded"] == "liq")
    frame = {
        "book-table": {"children": orderbook.render_book(
            hub, state["exchange"], state["expanded"] == "book")},
        "arb-table": {"children": arb_table},
        "arb-count": {"children": arb_count},
        "liq-table": {"children": liq_table},
        "liq-badges": {"children": liq_badges},
    }
    if depth:
        frame["depth-chart"] = {"figure": orderbook.render_depth(hub)}
    return frame


def _price_target(hub, interval: str) -> dict:
    """La cible du panneau prix : la dernière bougie et les derniers
    points d'indicateurs, jamais la série entière.

    Le rendu Lightweight Charts tient la série côté navigateur ; le
    canal ne transporte que la mutation, que lwc-price.js applique par
    `series.update` — zéro re-rendu. Le calcul est mémoïsé sur le cache
    du hub (terminal/lwc.py) : la cadence du pousseur ne le paie pas.
    """
    return {"update": lwc.push_payload(
        hub, interval, PRICE_INTERVALS.get(interval, 365))}


def _merge(state: dict, raw: str | bytes) -> dict:
    """Applique un message d'état du navigateur, sans lui faire confiance.

    Seules les clés connues sont retenues ; un message malformé est
    ignoré — le pousseur continue avec l'état précédent plutôt que de
    fermer le canal.
    """
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return state
    if not isinstance(message, dict):
        return state
    merged = dict(state)
    if isinstance(message.get("exchange"), str):
        merged["exchange"] = message["exchange"]
    expanded = message.get("expanded")
    if expanded is None or isinstance(expanded, str):
        merged["expanded"] = expanded
    # L'intervalle du panneau prix : borné aux intervalles connus — une
    # valeur inventée partirait en paramètre d'appel vers la source.
    interval = message.get("price_interval")
    if "price_interval" in message and (
            interval is None
            or (isinstance(interval, str) and interval in KLINE_FREQ)):
        merged["price_interval"] = interval
    return merged


def register(app, hub) -> None:
    """Pose la route `/push` et le relais d'état qui va avec.

    L'état dont le rendu dépend vit côté Dash — le Store `expanded`, le
    sélecteur du carnet. Deux callbacks clientside le relaient à push.js,
    qui l'annonce au serveur : le pousseur n'invente rien, il suit les
    mêmes entrées que les callbacks qu'il double.

    Les sorties sont des Stores puits (`push-sink-…`) : un callback doit
    écrire quelque part, et personne n'a besoin de lire ces écritures.
    """
    app.clientside_callback(
        "function (expanded) {"
        " if (window.btcPush) { window.btcPush.state({expanded: expanded"
        " || null}); }"
        " return window.dash_clientside.no_update; }",
        Output("push-sink-expanded", "data"),
        Input("expanded", "data"),
    )
    # Ce callback ne tourne que lorsque le carnet est monté — c'est
    # suffisant : tant que le sélecteur n'est pas à l'écran, le serveur
    # rend la plateforme par défaut, la même que celle du layout.
    app.clientside_callback(
        "function (exchange) {"
        " if (window.btcPush) { window.btcPush.state({exchange: exchange}); }"
        " return window.dash_clientside.no_update; }",
        Output("push-sink-exchange", "data"),
        Input("book-exchange", "value"),
    )

    @app.server.get("/api/boot")
    def _boot():
        return jsonify({"boot": BOOT})

    sock = Sock(app.server)

    @sock.route("/push")
    def _push(ws):
        serve(hub, ws)


def serve(hub, ws) -> None:
    """Pousse les trames à un navigateur jusqu'à son départ — ou l'arrêt du hub.

    La seconde sortie compte autant que la première : sous gunicorn,
    chaque WebSocket occupe un thread du pool et compte comme une
    requête en cours. Au SIGTERM, le worker attend la fin de ses
    requêtes avant de sortir, et l'interpréteur joint ensuite les
    threads du pool — une boucle qui ne rendrait la main qu'au départ
    du navigateur bloquerait l'arrêt jusqu'au SIGKILL de systemd. La
    boucle lit donc `hub.stopping` à chaque tour, et une cadence de
    `PUSH_INTERVAL` borne le délai de sortie ; elle ferme la WebSocket
    elle-même, pour que push.js bascule sur l'horloge sans attendre.
    """
    state = dict(DEFAULT_STATE)
    # Dernière sérialisation envoyée, par cible : c'est la comparaison
    # de chaînes qui décide qu'un panneau repart — pas de comparaison
    # d'arbres de composants, qui n'ont pas d'égalité utile.
    sent: dict[str, str] = {}
    next_send = time.monotonic()
    # La cible prix a sa propre cadence, plus lente : ce jalon dit
    # quand la recalculer. Zéro pour qu'elle parte dès la première
    # trame — et reparte dès qu'un changement d'état l'exige.
    price_due = 0.0
    depth_due = 0.0
    try:
        while not hub.stopping.is_set():
            # Entre deux trames, la boucle dort sur la réception :
            # un changement d'état arrive ainsi sans retard, et un
            # canal silencieux ne coûte qu'un réveil par cadence.
            wait = next_send - time.monotonic()
            if wait > 0:
                raw = ws.receive(timeout=wait)
                if raw is not None:
                    previous = state
                    state = _merge(state, raw)
                    # Un état qui change rend les comparaisons
                    # précédentes caduques pour les cibles qui en
                    # dépendent ; tout effacer est plus simple et ne
                    # coûte qu'une trame pleine.
                    if state != previous:
                        sent.clear()
                        next_send = time.monotonic()
                        price_due = depth_due = 0.0
                continue

            depth = time.monotonic() >= depth_due
            frame = _frame(hub, state, depth=depth)
            if depth:
                depth_due = time.monotonic() + PUSH_DEPTH_INTERVAL
            if (state["price_interval"]
                    and time.monotonic() >= price_due):
                frame["price-lwc"] = _price_target(
                    hub, state["price_interval"])
                price_due = time.monotonic() + PUSH_PRICE_INTERVAL
            changed = {
                target: blob
                for target, props in frame.items()
                if sent.get(target)
                != (blob := json.dumps(props, cls=PlotlyJSONEncoder))
            }
            if changed:
                # La trame est assemblée à la main : chaque valeur est
                # déjà une chaîne JSON, la re-sérialiser l'échapperait.
                ws.send("{" + ",".join(
                    f"{json.dumps(target)}:{blob}"
                    for target, blob in changed.items()) + "}")
                sent.update(changed)
            next_send = time.monotonic() + PUSH_INTERVAL
    except ConnectionClosed:
        # Fin normale : le navigateur est parti, ou le repli sur
        # l'horloge a pris le relais.
        return
    # Le hub s'arrête : c'est le serveur qui prend congé.
    try:
        ws.close()
    except Exception:  # noqa: BLE001 — la connexion peut déjà être morte
        pass
