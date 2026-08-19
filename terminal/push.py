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

Une trame n'emporte que les panneaux dont le rendu a changé depuis la
précédente : la cadence peut donc être plus serrée que l'horloge
qu'elle remplace sans coûter davantage — un carnet immobile ne transmet
rien du tout.
"""

from __future__ import annotations

import json
import time

from dash import Input, Output
from flask_sock import ConnectionClosed, Sock
from plotly.utils import PlotlyJSONEncoder

from .panels import arbitrage, liquidations, orderbook

#: Cadence du pousseur, plus serrée que REFRESH_FAST_MS : c'est la
#: raison d'être du canal — descendre sous 100 ms sans payer un
#: aller-retour HTTP par tour.
PUSH_INTERVAL = 0.1

#: L'état que le navigateur annonce, et ses valeurs tant qu'il n'a rien
#: dit — les mêmes défauts que la page au chargement.
DEFAULT_STATE = {"exchange": "Binance", "expanded": None}


def _frame(hub, state: dict) -> dict[str, dict]:
    """Le rendu des panneaux rapides, ciblé par identifiant de composant.

    Exactement ce que les callbacks Dash produiraient pour le même état :
    les fonctions `render` sont partagées, seul le canal change. Les six
    cibles sont toujours rendues — c'est la trame qui écarte celles qui
    n'ont pas changé, et le navigateur celles qui ne sont pas à l'écran.
    """
    arb_table, arb_count = arbitrage.render(hub)
    liq_table, liq_badges = liquidations.render(hub, state["expanded"] == "liq")
    return {
        "book-table": {"children": orderbook.render_book(
            hub, state["exchange"], state["expanded"] == "book")},
        "depth-chart": {"figure": orderbook.render_depth(hub)},
        "arb-table": {"children": arb_table},
        "arb-count": {"children": arb_count},
        "liq-table": {"children": liq_table},
        "liq-badges": {"children": liq_badges},
    }


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

    sock = Sock(app.server)

    @sock.route("/push")
    def _push(ws):
        state = dict(DEFAULT_STATE)
        # Dernière sérialisation envoyée, par cible : c'est la comparaison
        # de chaînes qui décide qu'un panneau repart — pas de comparaison
        # d'arbres de composants, qui n'ont pas d'égalité utile.
        sent: dict[str, str] = {}
        next_send = time.monotonic()
        try:
            while True:
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
                    continue

                changed = {
                    target: blob
                    for target, props in _frame(hub, state).items()
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
