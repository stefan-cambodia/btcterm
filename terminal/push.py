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
  agrandi, devise — à l'ouverture puis à chaque changement : le serveur
  rend exactement ce que l'interrogation aurait rendu pour le même état.
- le serveur envoie des trames `{id: {prop: valeur}}` que push.js
  applique par `dash_clientside.set_props` : le même chemin de mise à
  jour qu'une réponse de callback, sérialisé par le même encodeur —
  le navigateur ne voit pas la différence entre les deux canaux.
- le carnet, `book-data`, porte des données et non des composants :
  push.js les remet à `window.btcBook`, qui les écrit dans la page, au
  lieu de passer par set_props (`panels/orderbook.py`).
- une autre cible fait exception : `price-lwc`, la mutation du panneau prix
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
import logging
import threading
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

log = logging.getLogger("terminal.push")

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
#: canal. L'arbitrage, lui, garde la cadence du canal.
PUSH_DEPTH_INTERVAL = 1.0

#: Les liquidations repartent dès que leur fil change (`version` : un
#: événement, un lien qui tombe ou revient), et sinon à ce pas, pour ce que
#: seul le temps fait bouger — totaux de l'heure, liens muets. Rebâties à
#: chaque trame, table et badges valaient 2,2 ms sur 4,8 (hub réel, machine
#: chargée, le 7 novembre 2026), pour une table qui changeait une fois en
#: dix secondes.
PUSH_LIQ_INTERVAL = 1.0

#: Cadence du carnet : celle du canal, plein écran compris. Rendu par Dash,
#: il devait partir à 250 ms, et à 500 ms en plein écran : chaque trame
#: était un arbre de composants à réconcilier, et la page ne tenait plus que
#: la moitié de ses minuteries, avec des gels jusqu'à neuf secondes en plein
#: écran. Écrit en place par assets/book.js, à 100 ms, elle en tient 370
#: sur 400 en taille normale et 348 en plein écran (Firefox headless, le
#: 6 novembre 2026) — autant que sans carnet du tout. La trame pèse 0,9 Ko
#: au lieu de 11, ce qu'un tunnel SSH sent aussi.
PUSH_BOOK_INTERVAL = PUSH_INTERVAL

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
                 "currency": "USD", "price_interval": None}


def _frame(hub, state: dict, depth: bool = True,
           book: bool = True, liq: bool = True) -> dict[str, dict]:
    """Le rendu des panneaux rapides, ciblé par identifiant de composant.

    Exactement ce que les callbacks Dash produiraient pour le même état :
    les fonctions `render` sont partagées, seul le canal change. Les six
    cibles sont rendues — c'est la trame qui écarte celles qui n'ont pas
    changé, et le navigateur celles qui ne sont pas à l'écran — sauf le
    carnet, la profondeur et les liquidations, que la boucle ne demande
    qu'à leur cadence ou à leur changement (`book`, `depth`, `liq`).
    """
    arb_table, arb_count = arbitrage.render(hub, state["currency"])
    frame = {
        "arb-table": {"children": arb_table},
        "arb-count": {"children": arb_count},
    }
    if liq:
        liq_table, liq_badges = liquidations.render(
            hub, state["expanded"] == "liq", state["currency"])
        frame["liq-table"] = {"children": liq_table}
        frame["liq-badges"] = {"children": liq_badges}
    if book:
        frame["book-data"] = {"data": orderbook.book_payload(
            hub, state["exchange"], state["expanded"] == "book",
            state["currency"])}
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


class _PriceRead:
    """Une lecture de la cible prix, hors de la boucle du pousseur.

    La cible prix peut demander à Binance la fin de la série : 140 à 350 ms
    toutes les cinq secondes, le délai de connexion entier quand il se tait.
    Lue dans la boucle, c'était autant de carnet figé pour un panneau qui se
    veut sans réseau. La boucle lance la lecture, continue, et ramasse le
    résultat au tour où il est prêt. Thread démon : à l'arrêt du service, un
    appel en cours ne retient pas le processus.
    """

    def __init__(self, hub, interval: str):
        self.value: dict | None = None
        self.done = threading.Event()
        threading.Thread(target=self._read, args=(hub, interval),
                         name="push-price", daemon=True).start()

    def _read(self, hub, interval: str) -> None:
        try:
            self.value = _price_target(hub, interval)
        except Exception:  # noqa: BLE001 — retenté à la cadence suivante
            log.exception("lecture de la cible prix (%s)", interval)
        finally:
            self.done.set()


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
    if message.get("currency") in ("USD", "EUR"):
        merged["currency"] = message["currency"]
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

    L'état dont le rendu dépend vit côté Dash — les Stores `expanded` et
    `currency`, le sélecteur du carnet. Trois callbacks clientside le
    relaient à push.js, qui l'annonce au serveur : le pousseur n'invente
    rien, il suit les mêmes entrées que les callbacks qu'il double.

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
    app.clientside_callback(
        "function (currency) {"
        " if (window.btcPush) { window.btcPush.state({currency: currency"
        " || 'USD'}); }"
        " return window.dash_clientside.no_update; }",
        Output("push-sink-currency", "data"),
        Input("currency", "data"),
    )

    @app.server.get("/api/boot")
    def _boot():
        return jsonify({"boot": BOOT})

    @app.server.get("/api/health")
    def _health():
        """L'état du terminal en une requête, pour qui le surveille de
        dehors : `ok` est faux dès qu'un lien est coupé ou qu'une source
        est en panne, `failing` les nomme, `sources` détaille tout
        (§ hub.sources_status). Toujours 200 : c'est le terminal qui
        répond, même mal entouré."""
        status = hub.sources_status()
        failing = [row["name"] for rows in status.values() for row in rows
                   if row["state"] in ("coupé", "panne")]
        return jsonify({"ok": not failing, "failing": failing,
                        "uptime": hub.uptime_seconds, "boot": BOOT,
                        "sources": status})

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
    book_due = 0.0
    # Ce que valaient le fil des liquidations et le pas de temps à leur
    # dernier rendu : `None` les fait repartir.
    liq_seen = None
    # La lecture de la cible prix en cours, s'il y en a une.
    price_read: _PriceRead | None = None
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
                        price_due = depth_due = book_due = 0.0
                        liq_seen = None
                        # Elle portait peut-être sur l'intervalle d'avant.
                        price_read = None
                continue

            depth = time.monotonic() >= depth_due
            book = time.monotonic() >= book_due
            liq_now = (hub.liquidations.version,
                       int(time.monotonic() / PUSH_LIQ_INTERVAL))
            liq = liq_now != liq_seen
            frame = _frame(hub, state, depth=depth, book=book, liq=liq)
            liq_seen = liq_now
            if depth:
                depth_due = time.monotonic() + PUSH_DEPTH_INTERVAL
            if book:
                book_due = time.monotonic() + PUSH_BOOK_INTERVAL
            if (state["price_interval"] and price_read is None
                    and time.monotonic() >= price_due):
                price_read = _PriceRead(hub, state["price_interval"])
                price_due = time.monotonic() + PUSH_PRICE_INTERVAL
            if price_read is not None and price_read.done.is_set():
                if price_read.value is not None:
                    frame["price-lwc"] = price_read.value
                price_read = None
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
            # Le réveil se cale sur le carnet : si sa cadence s'écarte un
            # jour de celle du canal, elle ne s'arrondira pas à son pas.
            next_send = min(time.monotonic() + PUSH_INTERVAL, book_due)
    except (ConnectionClosed, OSError):
        # Fin normale : le navigateur est parti, ou le repli sur
        # l'horloge a pris le relais. `OSError` : la socket est morte avant
        # que le paquet de fermeture ne soit lu — un onglet tué, une machine
        # qui s'endort. Laissée filer, elle coûtait deux traces au journal
        # du service, gunicorn tentant ensuite de répondre sur la même socket.
        return
    # Le hub s'arrête : c'est le serveur qui prend congé.
    try:
        ws.close()
    except Exception:  # noqa: BLE001 — la connexion peut déjà être morte
        pass
