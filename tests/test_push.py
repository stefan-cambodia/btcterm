#!/usr/bin/env python3
"""
Le pousseur WebSocket, contrôlé sans réseau ni navigateur.

`ui_smoke.py` vérifie le canal de bout en bout dans un vrai Firefox ;
ce test couvre ce qui n'a pas besoin d'un navigateur — et qui doit donc
pouvoir échouer sans en lancer un :

- `_merge` : l'état annoncé par le navigateur est une entrée non fiable,
  et un message malformé ne doit jamais fermer le canal ;
- `_frame` : le rendu poussé suit l'état (plateforme, panneau agrandi)
  et vise exactement les cibles que les callbacks pilotent ;
- la sérialisation : les trames différentielles reposent sur la
  comparaison de chaînes — un rendu qui changerait d'une sérialisation
  à l'autre sans raison renverrait tout à chaque cadence.

Le hub n'est jamais démarré : les carnets se remplissent par
`OrderBook.replace`, le fil de liquidations par `_handle`, comme dans
test_liquidations.py. Aucun réseau n'est touché.

Lancement :
    python tests/test_push.py
"""

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plotly.utils import PlotlyJSONEncoder  # noqa: E402

from btcterm.hub import MarketHub  # noqa: E402
from terminal.panels.liquidations import ROWS_MAX  # noqa: E402
from terminal.panels.orderbook import DEPTH, DEPTH_MAX  # noqa: E402
from terminal.push import (DEFAULT_STATE,  # noqa: E402
                           PUSH_BOOK_INTERVAL,
                           PUSH_DEPTH_INTERVAL, PUSH_INTERVAL,
                           PUSH_LIQ_INTERVAL,
                           PUSH_PRICE_INTERVAL, _frame, _merge,
                           _price_target)

CIBLES = {"book-data", "depth-chart", "arb-table", "arb-count",
          "liq-table", "liq-badges"}


def hub_garni(levels: int = DEPTH_MAX + 2) -> MarketHub:
    """Un hub jamais démarré, aux carnets remplis à la main.

    Binance et Kraken reçoivent des niveaux discernables (60 000 contre
    50 000) : les tests peuvent lire dans la trame quelle plateforme a
    été rendue.

    Sans journal, surtout : ce hub reçoit des liquidations fabriquées, et
    le journal par défaut est celui de l'utilisateur (~/.btcterm) — les
    faux événements s'y inscrivaient, et depuis que la fenêtre est relue
    au démarrage (§ `_warm_liquidations`) ils ressortaient dans le
    panneau du vrai terminal.
    """
    hub = MarketHub(collect_news=False, keep_journal=False)
    for name, base in (("Binance", 60_000.0), ("Kraken", 50_000.0)):
        hub.books[name].replace(
            bids={base - i: 1.0 + i for i in range(1, levels + 1)},
            asks={base + i: 1.0 + i for i in range(1, levels + 1)},
        )
    return hub


def serialise(frame: dict) -> dict[str, str]:
    return {cible: json.dumps(props, cls=PlotlyJSONEncoder)
            for cible, props in frame.items()}


def lignes(blob: str) -> int:
    """Nombre de lignes de table dans une cible sérialisée."""
    return blob.count('"Tr"')


def rangs(props) -> int:
    """Lignes que le carnet occupera à l'écran : niveaux et spread.

    Le carnet part en données (`book_payload`), plus en composants.
    """
    if isinstance(props, str):
        props = json.loads(props)
    data = (props or {}).get("data") or {}
    return len(data.get("asks", [])) + len(data.get("bids", [])) + 1


def test_merge_ne_fait_pas_confiance():
    etat = dict(DEFAULT_STATE)

    #: Les messages légitimes sont appliqués, clé par clé.
    assert _merge(etat, '{"exchange": "Kraken"}')["exchange"] == "Kraken"
    assert _merge(etat, '{"expanded": "book"}')["expanded"] == "book"
    assert _merge(etat, '{"expanded": null}')["expanded"] is None
    assert _merge(etat, '{"currency": "EUR"}')["currency"] == "EUR"

    #: Tout le reste laisse l'état intact au lieu de casser la boucle :
    #: JSON invalide, JSON qui n'est pas un objet, clés inconnues,
    #: valeurs du mauvais type.
    for hostile in ("n'importe quoi {", "[1, 2]", '"Kraken"', "null",
                    '{"autre": 1}', '{"exchange": 3}', '{"expanded": []}',
                    '{"currency": "GBP"}', '{"currency": 3}',
                    '{"currency": []}', '{"currency": null}', b"\xff\xfe"):
        assert _merge(etat, hostile) == etat, hostile

    #: L'état d'origine n'est jamais modifié en place — le pousseur
    #: compare l'ancien et le nouveau pour vider son cache d'envoi.
    _merge(etat, '{"exchange": "Kraken", "expanded": "book"}')
    assert etat == DEFAULT_STATE
    print("  ✓ messages légitimes appliqués, hostiles ignorés, état copié")


def test_etat_prix_annonce_et_borne():
    """`price_interval` suit le contrat : nul par défaut — le rendu
    Plotly ne l'annonce jamais — accepté s'il est connu, sinon ignoré :
    la valeur part en paramètre d'appel vers la source."""
    etat = dict(DEFAULT_STATE)
    assert DEFAULT_STATE["price_interval"] is None

    assert _merge(etat, '{"price_interval": "1h"}')["price_interval"] == "1h"
    assert _merge(etat, '{"price_interval": null}')["price_interval"] is None
    for hostile in ('{"price_interval": "7m"}', '{"price_interval": 3}',
                    '{"price_interval": []}'):
        assert _merge(etat, hostile) == etat, hostile
    print("  ✓ intervalle connu accepté, inventé ou mal typé ignoré")


def test_cible_prix_mutation_memoisee():
    """La cible prix ne porte que le dernier point, suit l'intervalle
    annoncé, et ne recalcule rien tant que le cache du hub tient."""
    from btcterm import sources
    from terminal import lwc

    db = sources.generate_demo_ohlcv(400, interval="1h", index=False)
    db["time"] = db["time"].astype("datetime64[ns]")
    db.attrs.clear()
    sources.fetch_klines = (
        lambda symbol="BTCUSDT", interval="1h", limit=350, index=False,
        end_time=None: db.iloc[-limit:].reset_index(drop=True))

    hub = MarketHub(collect_news=False, keep_journal=False)
    lwc._push_memo.clear()
    paquet = _price_target(hub, "1h")["update"]

    #: Une mutation, pas une série : un seul bar, un point par indicateur.
    assert set(paquet) == {"interval", "bar", "volume", "overlays",
                           "panes", "volume_ma", "signal", "demo", "stale"}
    assert paquet["interval"] == "1h"
    assert paquet["bar"]["time"] == int(
        db["time"].iloc[-1].value // 1_000_000_000)
    assert isinstance(paquet["overlays"]["ma200"], dict)
    assert paquet["demo"] is False

    #: Mémoïsation sur l'identité du cache : le même DataFrame ne se
    #: recalcule pas — c'est ce qui rend la cadence du pousseur gratuite.
    assert _price_target(hub, "1h")["update"] is paquet

    #: Et sérialisation stable au recalcul forcé : le différentiel
    #: compare des chaînes.
    lwc._push_memo.clear()
    refait = _price_target(hub, "1h")["update"]
    assert json.dumps(refait, sort_keys=True) == json.dumps(
        paquet, sort_keys=True), "le recalcul fluctue à données constantes"

    #: L'intervalle annoncé choisit la série servie.
    assert _price_target(hub, "15m")["update"]["interval"] == "15m"
    print("  ✓ dernier point seul, intervalle suivi, mémo sur le cache du hub")


def test_frame_vise_les_cibles_des_callbacks():
    frame = _frame(hub_garni(), dict(DEFAULT_STATE))
    assert set(frame) == CIBLES, set(frame) ^ CIBLES
    props_attendues = {"depth-chart": "figure", "book-data": "data"}
    for cible, props in frame.items():
        prop = props_attendues.get(cible, "children")
        assert set(props) == {prop}, f"{cible} : {set(props)}"
    print(f"  ✓ {len(CIBLES)} cibles : la figure, les données du carnet, "
          "children ailleurs")


def test_frame_suit_l_etat():
    hub = hub_garni()

    #: La plateforme annoncée choisit le carnet rendu.
    binance = serialise(_frame(hub, dict(DEFAULT_STATE)))
    kraken = serialise(_frame(hub, {**DEFAULT_STATE, "exchange": "Kraken"}))
    assert "60,001" in binance["book-data"], "carnet Binance attendu"
    assert "50,001" in kraken["book-data"], "carnet Kraken attendu"
    assert "60,001" not in kraken["book-data"]

    #: L'agrandissement du carnet passe de 8 à 20 niveaux par côté
    #: (2 × niveaux + la ligne de spread) — et ne touche que lui.
    plein = serialise(_frame(hub, {**DEFAULT_STATE, "expanded": "book"}))
    assert rangs(binance["book-data"]) == 2 * DEPTH + 1
    assert rangs(plein["book-data"]) == 2 * DEPTH_MAX + 1
    assert plein["arb-table"] == binance["arb-table"]

    #: Même logique pour les liquidations, avec un fil injecté plus
    #: fourni que la vue repliée.
    from test_liquidations import message
    for i in range(ROWS_MAX + 4):
        hub.liquidations._handle(message(price=str(64_000 + i)))
    replie = serialise(_frame(hub, dict(DEFAULT_STATE)))
    agrandi = serialise(_frame(hub, {**DEFAULT_STATE, "expanded": "liq"}))
    assert lignes(agrandi["liq-table"]) == ROWS_MAX
    assert lignes(replie["liq-table"]) < lignes(agrandi["liq-table"])
    print("  ✓ plateforme suivie, agrandissements carnet et liquidations")


def test_le_carnet_suit_la_devise():
    """Le carnet passe en euros avec le panneau prix, sans toucher au réseau.

    Il restait en dollars : ni le callback ni le pousseur ne connaissaient
    la devise. Le taux vient du cache, jamais de la source : le pousseur
    rend ce carnet quatre fois par seconde et dormirait le temps de l'appel.
    """
    from btcterm import sources

    def appel_interdit():
        raise AssertionError("le carnet a appelé la source de change")

    def carnet(etat):
        return json.dumps(_frame(hub, etat)["book-data"],
                          cls=PlotlyJSONEncoder, ensure_ascii=False)

    original = sources.fetch_eur_rate
    sources.fetch_eur_rate = appel_interdit
    hub = hub_garni()
    euros = {**DEFAULT_STATE, "currency": "EUR"}
    try:
        #: À froid : le taux de repli, dit de secours.
        assert "⚠" in carnet(euros), "taux de repli non signalé"

        #: Le taux lu par le bandeau s'applique aux prix comme au spread.
        hub._cache.get("eur", hub.TTL_EUR, lambda: 0.5)
        dollars, converti = carnet(dict(DEFAULT_STATE)), carnet(euros)
        assert "60,001.00" in dollars and "spread 2.00 $" in dollars
        assert "30,000.500" in converti and "60,001.00" not in converti
        assert "spread 1.00 €" in converti and " $ ·" not in converti
        assert "⚠" not in converti and "1 $ = 0.5000 €" in converti

        #: Deux niveaux à un tick d'écart restent distincts en euros.
        hub._cache._entries["eur"] = (time.time(), 0.896)
        hub.books["Binance"].replace(bids={69_999.99: 1.0, 69_999.98: 1.0},
                                     asks={70_000.00: 1.0, 70_000.01: 1.0})
        ecran = carnet(euros)
        assert "62,720.000" in ecran and "62,719.991" in ecran
        hub.books["Binance"].replace(
            bids={60_000.0 - i: 1.0 + i for i in range(1, 22)},
            asks={60_000.0 + i: 1.0 + i for i in range(1, 22)})
        hub._cache._entries["eur"] = (time.time(), 0.5)
        converti = carnet(euros)

        #: Les quantités ne sont pas des prix : elles ne bougent pas.
        assert converti.count("2.0000") == dollars.count("2.0000") > 0
    finally:
        sources.fetch_eur_rate = original
    print("  ✓ prix et spread convertis, quantités intactes, aucun appel")


def test_les_panneaux_rapides_suivent_la_devise():
    """L'arbitrage et les liquidations passent en euros avec le carnet.

    L'arbitrage affichait ses prix sans unité : en euros, c'étaient des
    dollars que rien ne distinguait. Les pourcentages, eux, ne bougent pas.
    """
    from test_liquidations import message

    hub = hub_garni()
    hub._cache.get("eur", hub.TTL_EUR, lambda: 0.5)
    hub.liquidations._handle(message(price="64000"))
    for nom in ("Binance", "Kraken"):
        hub.books[nom].connected = True

    def texte(etat):
        return json.dumps(_frame(hub, etat), cls=PlotlyJSONEncoder,
                          ensure_ascii=False)

    dollars = texte(dict(DEFAULT_STATE))
    euros = texte({**DEFAULT_STATE, "currency": "EUR"})

    assert "Acheter ($)" in dollars and "(€)" not in dollars
    assert "Acheter (€)" in euros and "Vendre (€)" in euros
    assert "($)" not in euros

    #: Prix de l'événement et montants des badges, convertis au taux.
    assert "64,000" in dollars and "32,000" in euros and "64,000" not in euros
    assert "k€" in euros and "k$" not in euros

    #: Prix de l'arbitrage : 60 000 et 50 000 $ devenus 30 000 et 25 000 €.
    assert "Binance 60,001.00" in dollars or "Kraken 50,001.00" in dollars
    assert "Binance 30,000.50" in euros or "Kraken 25,000.50" in euros
    print("  ✓ arbitrage et liquidations convertis, unité dite")


def test_carnets_vides_rendus_dits():
    """Un hub sans données doit le dire, pas produire une trame vide."""
    frame = serialise(_frame(MarketHub(collect_news=False,
                                       keep_journal=False),
                             dict(DEFAULT_STATE)))
    assert set(json.loads("{" + ",".join(
        f'"{c}":{b}' for c, b in frame.items()) + "}")) == CIBLES
    assert "connexion en cours" in frame["book-data"]
    assert "en attente" in frame["arb-table"]
    print("  ✓ six cibles même à vide, chacune disant son état")


def test_serialisation_stable():
    """Deux rendus du même état doivent se sérialiser à l'identique.

    C'est l'hypothèse des trames différentielles : le pousseur compare
    des chaînes, et une cible dont la sérialisation fluctuerait sans
    changement de données repartirait à chaque cadence. Seul le carnet
    y échappe — il affiche l'âge de son flux, qui avance réellement.
    """
    hub = hub_garni()
    etat = dict(DEFAULT_STATE)
    un, deux = serialise(_frame(hub, etat)), serialise(_frame(hub, etat))
    for cible in CIBLES - {"book-data"}:
        assert un[cible] == deux[cible], f"{cible} fluctue sans raison"
    print("  ✓ cibles stables à données constantes — le différentiel a un sens")


def test_l_age_du_carnet_avance():
    """Le pendant du test précédent : l'âge affiché doit vivre.

    Si le carnet se sérialisait à l'identique alors que le temps passe,
    c'est que l'âge du flux — le témoin de fraîcheur du panneau — serait
    figé au moment du snapshot.
    """
    hub = hub_garni()
    etat = dict(DEFAULT_STATE)
    un = serialise(_frame(hub, etat))["book-data"]
    time.sleep(0.05)
    deux = serialise(_frame(hub, etat))["book-data"]
    assert un != deux, "l'âge du flux n'avance pas"
    print("  ✓ l'âge du flux avance d'un rendu à l'autre")


def test_le_carnet_part_a_sa_cadence():
    """Le carnet part à chaque trame du canal, et un changement d'état aussitôt.

    Rendu par Dash, il devait partir à 250 ms : la page ne suivait pas à
    100. Écrit en place par assets/book.js, il reprend la cadence du canal,
    et le changement de plateforme se voit sans attendre.
    """
    from terminal.push import serve

    assert PUSH_BOOK_INTERVAL <= PUSH_INTERVAL

    class WsFactice:
        def __init__(self):
            self.trames = []
            self.a_lire = []

        def receive(self, timeout=None):
            time.sleep(min(timeout or 0, 0.02))
            return self.a_lire.pop() if self.a_lire else None

        def send(self, data):
            self.trames.append((time.monotonic(), json.loads(data)))

    hub = hub_garni()
    ws = WsFactice()
    threading.Timer(0.5, lambda: ws.a_lire.append(
        '{"exchange": "Kraken"}')).start()
    threading.Timer(1.0, hub.stop).start()
    serve(hub, ws)

    carnets = [(t, f) for t, f in ws.trames if "book-data" in f]
    avant = [t for t, f in carnets if "50,001" not in json.dumps(f)]
    ecarts = [b - a for a, b in zip(avant, avant[1:])]
    assert len(avant) >= 3 and min(ecarts) >= PUSH_BOOK_INTERVAL * 0.9, ecarts
    #: Au pas du canal, pas au double.
    assert sorted(ecarts)[len(ecarts) // 2] < PUSH_BOOK_INTERVAL * 1.5, ecarts
    assert len(carnets) <= 1 / PUSH_BOOK_INTERVAL + 3, len(carnets)

    #: La plateforme annoncée part sans attendre la cadence.
    apres = next(t for t, f in carnets if "50,001" in json.dumps(f))
    assert apres - avant[-1] < PUSH_BOOK_INTERVAL + 0.15
    print(f"  ✓ {len(carnets)} carnets en une seconde, changement d'état immédiat")


def test_le_plein_ecran_garde_la_cadence():
    """41 lignes au lieu de 17, à la même cadence.

    Rendu par Dash, le plein écran devait ralentir à 500 ms, et la page
    gelait encore ; écrit en place, il ne coûte pas plus que la vue réduite.
    """
    from terminal.push import serve

    class WsFactice:
        def __init__(self):
            self.trames = []
            self.a_lire = ['{"expanded": "book"}']

        def receive(self, timeout=None):
            time.sleep(min(timeout or 0, 0.02))
            return self.a_lire.pop() if self.a_lire else None

        def send(self, data):
            self.trames.append((time.monotonic(), json.loads(data)))

    hub = hub_garni()
    ws = WsFactice()
    threading.Timer(1.0, hub.stop).start()
    serve(hub, ws)

    #: La première trame part avant l'annonce : elle ne compte pas.
    pleins = [t for t, f in ws.trames
              if rangs(f.get("book-data")) == 2 * DEPTH_MAX + 1]
    ecarts = [b - a for a, b in zip(pleins, pleins[1:])]
    assert len(pleins) >= 5, len(pleins)
    assert sorted(ecarts)[len(ecarts) // 2] < PUSH_BOOK_INTERVAL * 1.5, ecarts
    print(f"  ✓ plein écran : {len(pleins)} carnets de "
          f"{2 * DEPTH_MAX + 1} lignes en une seconde")


class WsDeSecours:
    """Une WebSocket sans navigateur : retient ce qui lui est envoyé."""

    def __init__(self, a_lire=()):
        self.trames = []
        self.a_lire = list(a_lire)
        self.closed = False

    def receive(self, timeout=None):
        time.sleep(min(timeout or 0, 0.02))
        return self.a_lire.pop(0) if self.a_lire else None

    def send(self, data):
        self.trames.append((time.monotonic(), json.loads(data)))

    def close(self):
        self.closed = True


def test_le_carnet_n_attend_pas_le_reseau():
    """Binance qui traîne ne fige pas le carnet.

    La cible prix peut coûter un appel réseau, jusqu'au délai de connexion
    entier ; lue dans la boucle, c'était autant de carnet immobile.
    """
    from terminal.push import serve

    hub = hub_garni()
    bloque = threading.Event()
    lectures = []

    def lente(hub_, interval):
        lectures.append(interval)
        bloque.wait(5)
        return {"update": {"interval": interval}}

    import terminal.push as push
    original = push._price_target
    push._price_target = lente
    ws = WsDeSecours(['{"price_interval": "1h"}'])
    threading.Timer(1.0, hub.stop).start()
    try:
        serve(hub, ws)
    finally:
        bloque.set()
        push._price_target = original

    carnets = [f for _, f in ws.trames if "book-data" in f]
    assert len(carnets) >= 3, f"carnet figé : {len(carnets)} trames en 1 s"
    assert lectures == ["1h"], f"lectures empilées : {lectures}"
    print(f"  ✓ {len(carnets)} carnets pendant une lecture de 5 s, une seule lecture")


def test_la_cible_prix_arrive_quand_elle_est_prete():
    from terminal.push import serve
    import terminal.push as push

    hub = hub_garni()
    original = push._price_target
    push._price_target = lambda hub_, interval: {"update": {"i": interval}}
    ws = WsDeSecours(['{"price_interval": "1h"}'])
    threading.Timer(0.5, hub.stop).start()
    try:
        serve(hub, ws)
    finally:
        push._price_target = original
    prix = [f["price-lwc"] for _, f in ws.trames if "price-lwc" in f]
    assert prix == [{"update": {"i": "1h"}}], prix
    print("  ✓ la cible prix part une fois, dès qu'elle est lue")


def test_un_ancien_intervalle_n_est_pas_pousse():
    """La lecture de l'intervalle d'avant n'atteint pas le navigateur."""
    from terminal.push import serve
    import terminal.push as push

    hub = hub_garni()
    libere = threading.Event()

    def lente(hub_, interval):
        if interval == "1h":
            libere.wait(2)
        return {"update": {"interval": interval}}

    original = push._price_target
    push._price_target = lente
    ws = WsDeSecours(['{"price_interval": "1h"}'])
    threading.Timer(0.2, lambda: ws.a_lire.append(
        '{"price_interval": "4h"}')).start()
    threading.Timer(0.4, libere.set).start()
    threading.Timer(0.8, hub.stop).start()
    try:
        serve(hub, ws)
    finally:
        push._price_target = original
    vus = [f["price-lwc"]["update"]["interval"] for _, f in ws.trames
           if "price-lwc" in f]
    assert vus == ["4h"], vus
    print("  ✓ seul l'intervalle affiché part")


def test_une_socket_morte_ne_fait_pas_de_trace():
    """Un onglet tué : `OSError` au lieu du paquet de fermeture.

    L'exception sortait de `serve`, et le journal du service en gardait deux
    traces — la nôtre, puis gunicorn répondant sur la même socket.
    """
    from terminal.push import serve

    class WsMorte(WsDeSecours):
        def send(self, data):
            raise OSError(9, "Bad file descriptor")

    hub = hub_garni()
    serve(hub, WsMorte())   # lèverait si l'erreur n'était pas retenue
    hub.stop()
    print("  ✓ socket morte : fin de boucle, sans exception")


def test_les_liquidations_suivent_leur_fil():
    """Table et badges des liquidations ne se rebâtissent qu'au changement du
    fil, ou une fois par seconde pour ce que seul le temps change — et une
    liquidation arrivée part à la trame suivante, pas à la seconde suivante.

    Rebâties à chaque trame, elles valaient près de la moitié d'une trame,
    pour une table qui changeait une fois en dix secondes.
    """
    from terminal import push
    from test_liquidations import message

    rendus = []
    vrai = push.liquidations.render

    def compte(*args, **kwargs):
        rendus.append(time.monotonic())
        return vrai(*args, **kwargs)

    hub = hub_garni()
    ws = WsDeSecours()
    arrivee = []

    def liquide():
        arrivee.append(time.monotonic())
        hub.liquidations._handle(message(price="64321"))

    push.liquidations.render = compte
    threading.Timer(1.25, liquide).start()
    threading.Timer(2.0, hub.stop).start()
    try:
        push.serve(hub, ws)
    finally:
        push.liquidations.render = vrai

    #: Deux secondes à dix trames : vingt rendus jusqu'ici.
    assert len(rendus) <= 2 / PUSH_LIQ_INTERVAL + 3, len(rendus)
    vue = next(t for t, f in ws.trames
               if "64,321" in json.dumps(f.get("liq-table", "")))
    assert vue - arrivee[0] < PUSH_INTERVAL + 0.15, vue - arrivee[0]
    print(f"  ✓ {len(rendus)} rendus en deux secondes, la liquidation "
          f"à l'écran {1000 * (vue - arrivee[0]):.0f} ms après son arrivée")


def test_la_boucle_sort_a_l_arret():
    """Le hub qui s'arrête fait sortir la boucle sans attendre le navigateur.

    Sous gunicorn, une boucle qui ne rendrait la main qu'au départ du
    navigateur retiendrait le worker jusqu'au SIGKILL de systemd.
    """
    from terminal.push import serve

    class WsFactice:
        def __init__(self):
            self.sent = []
            self.closed = False

        def receive(self, timeout=None):
            time.sleep(min(timeout or 0, 0.05))
            return None

        def send(self, data):
            self.sent.append(data)

        def close(self):
            self.closed = True

    hub = MarketHub(collect_news=False, keep_journal=False)
    ws = WsFactice()
    threading.Timer(0.3, hub.stop).start()   # `stop` lève `stopping`
    debut = time.monotonic()
    serve(hub, ws)
    duree = time.monotonic() - debut
    assert 0.3 <= duree < 2, f"la boucle a mis {duree:.1f} s à sortir"
    assert ws.sent, "une première trame est partie avant l'arrêt"
    assert ws.closed, "c'est le serveur qui prend congé du navigateur"
    print("  ✓ la boucle /push sort à l'arrêt du hub et ferme la WebSocket")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nPousseur WebSocket — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le pousseur rend ce que les callbacks rendraient.\n")
