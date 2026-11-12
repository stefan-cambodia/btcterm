#!/usr/bin/env python3
"""
Carnets et connecteurs : niveaux fantômes, carnets croisés, resynchronisation.

Le journal de séance a montré des « épisodes d'arbitrage rentables » de
plusieurs heures — Coinbase à 78 880 $ pendant que le marché cotait
80 600 $. Un carnet nourri par deltas ne se corrige jamais seul : une
suppression manquée laisse un niveau fantôme que `best_ask` remonte à
chaque lecture. Deux causes, deux parades, vérifiées ici sans réseau :

- le snapshot que Coinbase Advanced Trade envoie à chaque resouscription
  était appliqué comme une mise à jour, par-dessus l'ancien carnet — il
  le **remplace** désormais ;
- un carnet croisé (meilleur bid ≥ meilleur ask) porte forcément un
  fantôme : toléré le temps qu'un message de l'autre côté arrive, il
  vaut resynchronisation s'il persiste (`BookDesync`), et le moteur
  d'arbitrage l'écarte en attendant.

Lancement :
    python tests/test_orderbook.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.arbitrage import ArbitrageEngine  # noqa: E402
from btcterm.exchanges import (  # noqa: E402
    BookDesync, BybitConnector, CoinbaseAdvancedConnector, KrakenConnector,
    OrderBook)


def kraken(**sides):
    """Message `book` v1 : `[canal, {côtés}, "book-25", "XBT/USDT"]`."""
    return [42, {k: [[str(p), str(q), "0"] for p, q in v]
                 for k, v in sides.items()}, "book-25", "XBT/USDT"]


def coinbase(kind, *updates):
    """Événement `level2` Advanced Trade : snapshot ou update, même forme."""
    return {"channel": "l2_data", "events": [{
        "type": kind, "product_id": "BTC-USDT",
        "updates": [{"side": side, "price_level": str(p), "new_quantity": str(q)}
                    for side, p, q in updates]}]}


def bybit(kind, b=(), a=()):
    return {"topic": "orderbook.50.BTCUSDT", "type": kind,
            "data": {"b": [[str(p), str(q)] for p, q in b],
                     "a": [[str(p), str(q)] for p, q in a]}}


def test_carnet_croise():
    book = OrderBook(exchange="X")
    assert not book.crossed, "un carnet vide n'est pas croisé"
    book.replace({80000: 1, 79990: 2}, {80010: 1, 80020: 1})
    assert not book.crossed
    book.apply(bid_updates=[(80015, 1)])
    assert book.crossed
    book.apply(bid_updates=[(80015, 0)])
    assert not book.crossed
    print("  ✓ un carnet dont le bid atteint l'ask se dit croisé")


def test_coinbase_le_snapshot_remplace():
    book = OrderBook(exchange="Coinbase")
    connector = CoinbaseAdvancedConnector(book)
    connector._handle(coinbase("snapshot", ("bid", 80000, 1), ("ask", 80010, 1)))
    connector._handle(coinbase("update", ("bid", 80005, 1)))
    assert book.best_bid == 80005 and book.best_ask == 80010

    # Reconnexion : le marché a monté, l'ask à 80 010 n'existe plus.
    # Appliqué comme une mise à jour, il resterait — le fantôme.
    connector._handle(coinbase("snapshot", ("bid", 80600, 1), ("ask", 80610, 1)))
    assert book.best_ask == 80610 and book.best_bid == 80600, (
        book.best_bid, book.best_ask)
    assert not book.crossed
    print("  ✓ le snapshot Coinbase Advanced remplace le carnet")


def test_croisement_fugace_tolere_persistant_resynchronise():
    book = OrderBook(exchange="Kraken")
    connector = KrakenConnector(book)
    connector._handle(kraken(**{"as": [(80010, 1)], "bs": [(80000, 1)]}))
    assert book.best_bid == 80000 and book.best_ask == 80010

    # Les deux côtés d'un mouvement dans deux messages : croisé entre les
    # deux, décroisé au second. Rien ne doit se resynchroniser.
    connector._handle(kraken(b=[(80015, 1)]))
    assert book.crossed and connector._crossed_since is not None
    connector._handle(kraken(a=[(80010, 0)]))
    assert not book.crossed and connector._crossed_since is None

    # Un fantôme : un ask sous le bid qui ne s'en va pas. Toléré au
    # premier message, il vaut panne une fois la grâce écoulée.
    connector._handle(kraken(a=[(79000, 1)]))
    assert book.crossed
    connector._crossed_since -= connector.CROSSED_GRACE   # le temps passe
    try:
        connector._handle(kraken(b=[(80016, 1)]))
    except BookDesync as exc:
        assert "croisé" in str(exc)
    else:
        raise AssertionError("un carnet croisé qui persiste doit lever BookDesync")
    assert connector._crossed_since is None, "compteur rendu pour la reconnexion"
    print("  ✓ croisement fugace toléré, croisement persistant resynchronisé")


def test_bybit_aussi():
    book = OrderBook(exchange="Bybit")
    connector = BybitConnector(book)
    connector._handle(bybit("snapshot", b=[(80000, 1)], a=[(80010, 1)]))
    connector._handle(bybit("delta", a=[(79000, 1)]))
    connector._crossed_since -= connector.CROSSED_GRACE
    try:
        connector._handle(bybit("delta", b=[(80001, 1)]))
    except BookDesync:
        pass
    else:
        raise AssertionError("Bybit : carnet croisé persistant non détecté")
    print("  ✓ même garde-fou sur les deltas Bybit")


def test_kraken_ne_garde_pas_ce_qui_sort_de_la_fenetre():
    """Kraken décrit une fenêtre de `depth` niveaux : ce qu'une insertion
    en chasse en sort sans message, et le flux n'en dira plus rien — pas
    même sa disparition. Un carnet qui le garderait finirait croisé."""
    book = OrderBook(exchange="Kraken")
    assert book.max_levels > 3
    connector = KrakenConnector(book, depth=3)
    assert book.max_levels == 3, "le carnet est borné à la fenêtre"

    connector._handle(kraken(**{"as": [(100, 1), (101, 1), (102, 1)],
                                "bs": [(99, 1), (98, 1), (97, 1)]}))
    # Un ask s'insère dans la fenêtre : 102 en sort, chez Kraken comme ici.
    connector._handle(kraken(a=[(100.5, 1)]))
    assert sorted(book.asks) == [100, 100.5, 101], "102 est sorti de la fenêtre"

    # Le marché monte : les asks de la fenêtre partent, d'autres y
    # entrent (republiés). 102, annulé entre-temps hors fenêtre, n'est
    # mentionné nulle part — c'est le contrat du flux.
    connector._handle(kraken(a=[(100, 0), (100.5, 0), (101, 0),
                                (103, 1), (104, 1), (105, 1)]))
    connector._handle(kraken(b=[(102.5, 1), (102.2, 1), (102.1, 1)]))
    assert book.best_bid == 102.5 and book.best_ask == 103
    assert not book.crossed, "le fantôme à 102 aurait croisé le carnet"
    print("  ✓ Kraken : le carnet ne garde pas ce qui sort de la fenêtre")


def test_le_moteur_ecarte_le_carnet_croise():
    books = {"A": OrderBook(exchange="A"), "B": OrderBook(exchange="B")}
    for book in books.values():
        book.connected = True
    # A porte un fantôme : ask à 78 880 sous son propre bid.
    books["A"].replace({80600: 1}, {78880: 1})
    # B cote sous A : acheter B, vendre A serait l'écart à évaluer.
    books["B"].replace({80480: 1}, {80490: 1})
    engine = ArbitrageEngine(books, min_profit_pct=0.1,
                             fees={"A": 0.0, "B": 0.0})
    assert engine.scan() == [], "l'écart d'un carnet croisé n'existe pas"

    # Carnet sain : la paire s'évalue de nouveau.
    books["A"].replace({80600: 1}, {80610: 1})
    assert engine.scan(), "un carnet sain doit être évalué"
    print("  ✓ le moteur d'arbitrage ignore un carnet croisé")


def test_le_rendu_ne_compte_pas_les_opportunites():
    """Le compteur « détectées » avance au balayage du hub, pas au rendu.

    Le panneau appelait `scan`, qui compte : le pousseur rendant
    l'arbitrage à chaque trame, une paire rentable ajoutait dix au compteur
    par seconde et par onglet ouvert, en plus du balayage du hub.
    """
    from btcterm.hub import MarketHub
    from terminal.panels import arbitrage

    hub = MarketHub(collect_news=False, keep_journal=False)
    # Kraken cote 2 % au-dessus : acheter Binance, revendre Kraken reste
    # rentable une fois payés les frais taker (0,10 % et 0,80 %).
    for name, (bid, ask) in {"Binance": (80_000, 80_001),
                             "Kraken": (81_600, 81_601)}.items():
        hub.books[name].replace({bid: 1}, {ask: 1})
        hub.books[name].connected = True

    for _ in range(50):
        table, badge = arbitrage.render(hub, "USD")
    assert hub.engine.stats["total_opportunities"] == 0, badge
    assert "Binance" in str(table), "le rendu montre la paire sans la compter"

    assert hub.engine.scan()[0].is_profitable
    assert hub.engine.stats["total_opportunities"] == 1
    assert arbitrage.render(hub, "USD")[1].startswith("1 détectées")
    print("  ✓ cinquante rendus, aucun compté ; un balayage, un compté")


def test_la_pression_acheteuse_se_lit_sur_le_spread():
    """La part des acheteurs dans le volume des niveaux affichés."""
    from btcterm.hub import MarketHub
    from terminal.panels.orderbook import book_payload

    hub = MarketHub(collect_news=False, keep_journal=False)
    hub.books["Binance"].replace(bids={99.0: 3.0, 98.0: 3.0},
                                 asks={101.0: 1.0, 102.0: 1.0})
    carnet = book_payload(hub, "Binance", False)
    assert "achat 75 %" in carnet["spread"]
    print("  ✓ 6 BTC à l'achat contre 2 à la vente : achat 75 %")


def test_le_carnet_part_en_donnees():
    """Le carnet part en données formatées, dans l'ordre de l'écran.

    `assets/book.js` ne fait que poser les textes : tout ce qui se décide —
    ordre, devise, décimales, largeur des barres — se décide ici.
    """
    from btcterm.hub import MarketHub
    from terminal.panels.orderbook import DEPTH, DEPTH_MAX, book_payload

    hub = MarketHub(collect_news=False, keep_journal=False)
    assert book_payload(hub, "Binance", False) == {
        "message": "connexion en cours…"}
    hub.books["Binance"].replace(
        bids={60_000.0 - i: float(i) for i in range(1, 30)},
        asks={60_000.0 + i: float(i) for i in range(1, 30)})
    carnet = book_payload(hub, "Binance", False)
    #: Les ventes de la plus chère à la moins chère, puis les achats du
    #: meilleur au moins bon : le spread tombe au milieu.
    assert [r[0] for r in carnet["asks"]][-2:] == ["60,002.00", "60,001.00"]
    assert [r[0] for r in carnet["bids"]][:2] == ["59,999.00", "59,998.00"]
    assert carnet["asks"][-1][1] == "1.0000"
    #: La barre la plus longue est le plus gros niveau affiché.
    assert carnet["asks"][0][2] == 100 and carnet["asks"][-1][2] == 12
    assert len(carnet["asks"]) == len(carnet["bids"]) == DEPTH
    assert len(book_payload(hub, "Binance", True)["bids"]) == DEPTH_MAX
    print("  ✓ niveaux formatés, ordre de l'écran, barres, plein écran")


def test_les_murs_reunissent_les_plateformes():
    """Une muraille à cheval sur deux carnets se voit, un carnet coupé non.

    Les deux plateformes cotent à des prix différents, comme en vrai : en
    prix absolu, leurs carnets se croiseraient et le mur d'achat passerait
    au-dessus du mur de vente.
    """
    from btcterm.hub import MarketHub
    from terminal.fx import Display
    from terminal.panels.orderbook import book_payload, wall

    hub = MarketHub(collect_news=False, keep_journal=False)
    assert wall(hub.books, "bids") is None, "aucun carnet connecté"

    #: 3 BTC ici, 4 là, au même écart de leur prix médian respectif (100 et
    #: 110) : plus lourds ensemble que les 6 BTC isolés sur Binance.
    hub.books["Binance"].replace(bids={99.9: 3.0, 99.0: 6.0},
                                 asks={100.1: 1.0})
    hub.books["Kraken"].replace(bids={109.89: 4.0}, asks={110.11: 2.0})
    hub.books["Binance"].connected = hub.books["Kraken"].connected = True
    achat, vente = wall(hub.books, "bids"), wall(hub.books, "asks")
    assert abs(achat[0] + 0.1) < 1e-6 and achat[1] == 7.0, achat
    assert abs(vente[0] - 0.1) < 1e-6 and vente[1] == 3.0, vente

    #: Un mur d'achat reste sous le prix, même tout contre lui.
    hub.books["Bybit"].replace(bids={99.9999: 90.0}, asks={100.0001: 90.0})
    hub.books["Bybit"].connected = True
    assert wall(hub.books, "bids")[0] < 0 < wall(hub.books, "asks")[0]
    hub.books["Bybit"].connected = False

    #: Un carnet déconnecté garde ses niveaux, mais ne compte plus.
    hub.books["Kraken"].connected = False
    assert wall(hub.books, "bids")[1] == 6.0
    hub.books["Kraken"].connected = True

    murs = book_payload(hub, "Binance", False, None)["walls"]
    assert murs["bid"] == "achat 100 (-0.10 %) · 7.0 BTC"
    assert murs["ask"] == "vente 100 (+0.10 %) · 3.0 BTC"
    hub._cache.get("eur", hub.TTL_EUR, lambda: 0.5)
    murs = book_payload(hub, "Binance", False, "EUR")["walls"]
    assert murs["bid"].startswith("achat 50 (-0.10 %)")
    print("  ✓ tranches réunies, carnet coupé écarté, prix dans la devise")


def test_le_backoff_repart_apres_une_connexion_qui_a_tenu():
    """Une coupure après une longue connexion n'hérite pas du backoff.

    Le compteur ne se remettait à zéro qu'au retour normal du flux —
    qui ne revient jamais : après quelques pannes sur une séance, chaque
    reconnexion attendait le plafond de 30 s, réveil après veille compris.
    """
    import asyncio
    import time
    from btcterm import exchanges

    connector = KrakenConnector(OrderBook(exchange="Kraken"))
    connector.STABLE_AFTER = 0.05
    attentes = []
    coupures = iter([0.0, 0.0, 0.1, 0.0])   # deux courtes, une qui tient, une courte

    async def flux():
        duree = next(coupures, None)
        if duree is None:
            connector.stop()          # la séance s'arrête là
        elif duree:
            await sommeil_reel(duree)
        raise RuntimeError("coupé")

    sommeil_reel = asyncio.sleep

    async def sommeil_note(secondes):
        attentes.append(secondes)

    exchanges.asyncio.sleep = sommeil_note
    try:
        asyncio.run(connector._connect_with_retry(flux))
    finally:
        exchanges.asyncio.sleep = sommeil_reel
    # 2, 4 : le backoff monte ; la troisième connexion a tenu, la
    # quatrième coupure repart donc à 2 au lieu de monter à 16.
    assert attentes[:4] == [2, 4, 2, 4], attentes
    print("  ✓ le backoff repart de zéro après une connexion qui a tenu")


def test_les_murs_ne_se_cherchent_pas_a_chaque_envoi():
    """Le carnet part dix fois par seconde ; les murs, cherchés dans cinq
    carnets entiers, une fois par seconde."""
    from types import SimpleNamespace

    from terminal.panels import orderbook as panneau

    book = OrderBook("Binance")
    book.connected = True
    book.replace({99_990.0: 1.0, 99_900.0: 5.0}, {100_010.0: 1.0, 100_100.0: 7.0})
    hub = SimpleNamespace(books={"Binance": book},
                          eur_rate_cached=lambda: None)
    appels = []
    cherche = panneau.wall
    panneau.wall = lambda books, side: appels.append(side) or cherche(books, side)
    try:
        premier = panneau.book_payload(hub, "Binance", False)["walls"]
        assert premier and "5.0 BTC" in premier["bid"], premier
        for _ in range(5):
            panneau.book_payload(hub, "Binance", False)
        assert appels == ["bids", "asks"], appels
        # La durée de vie passée, ils sont recherchés ; un autre jeu de
        # carnets aussi, sans attendre.
        hub._walls_memo = (hub._walls_memo[0] - panneau.WALLS_TTL - 0.1,
                           *hub._walls_memo[1:])
        book.replace({99_990.0: 1.0, 99_900.0: 9.0}, {100_010.0: 1.0})
        assert "9.0 BTC" in panneau.book_payload(hub, "Binance", False)["walls"]["bid"]
        assert len(appels) == 4
        hub.books = dict(hub.books)
        panneau.book_payload(hub, "Binance", False)
        assert len(appels) == 6
    finally:
        panneau.wall = cherche
    print("  ✓ murs cherchés une fois par seconde, pas à chaque envoi")


def test_binance_snapshot_accorde_au_flux():
    """Binance : un snapshot REST, les deltas du flux par-dessus, sans trou
    ni redite — et une relecture qui ne ferme rien."""
    import asyncio

    from btcterm.exchanges import BinanceConnector

    def delta(first, last, bids=(), asks=()):
        return {"e": "depthUpdate", "U": first, "u": last,
                "b": [list(x) for x in bids], "a": [list(x) for x in asks]}

    snapshot = {"lastUpdateId": 100,
                "bids": [["100", "1"], ["99", "2"]],
                "asks": [["101", "1"], ["102", "2"]]}
    book = OrderBook("Binance")
    connector = BinanceConnector(book, depth=500, fetch=lambda: snapshot)
    assert book.max_levels == 500 and "@depth@100ms" in connector.url
    assert "limit=500" in connector.snapshot_url

    # Les messages gardés pendant la lecture : deux que le snapshot
    # contient déjà, un à cheval sur son numéro, un qui le suit.
    connector._load(snapshot, [
        delta(90, 95, bids=[("100", "9")]),
        delta(96, 100, bids=[("100", "8")]),
        delta(99, 103, bids=[("100", "3")], asks=[("101", "0")]),
        delta(104, 106, asks=[("101.5", "4")]),
    ])
    assert book.top("bids", 2) == [(100.0, 3.0), (99.0, 2.0)], book.top("bids", 2)
    assert book.top("asks", 2) == [(101.5, 4.0), (102.0, 2.0)]
    assert connector._last == 106

    connector._apply(delta(107, 110, bids=[("99", "0")]))
    assert book.top("bids", 5) == [(100.0, 3.0)]
    connector._apply(delta(105, 110, bids=[("100", "77")]))   # redite
    assert book.best_bid == 100 and book.top("bids", 1)[0][1] == 3.0
    try:
        connector._apply(delta(113, 115, bids=[("100", "5")]))
    except BookDesync as exc:
        assert "perdu" in str(exc)
    else:
        raise AssertionError("un trou dans la suite doit lever")
    # Un snapshot plus vieux que le premier message gardé : trou aussi.
    try:
        connector._load(snapshot, [delta(105, 108)])
    except BookDesync:
        pass
    else:
        raise AssertionError("un snapshot trop vieux doit lever")

    # Le flux entier, sur une fausse socket : le snapshot n'est demandé
    # qu'au premier message, le lien n'est dit ouvert qu'une fois le
    # carnet posé, et la relecture périodique garde le flux.
    lectures = []
    vu = [0]                               # dernier numéro livré par le flux

    class Socket:
        def __init__(self, messages):
            self.messages = messages
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self.messages:
                raise StopAsyncIteration
            await asyncio.sleep(0.02)      # laisse la lecture aboutir
            message = self.messages.pop(0)
            vu[0] = message.get("u", vu[0])
            return json.dumps(message)

    def lire():
        lectures.append(connector._last)
        return {"lastUpdateId": 200 if not lectures[:-1] else lectures[-1],
                "bids": [["100", "1"]],
                "asks": [["101", "1"]] if not lectures[:-1] else [["101", "6"]]}

    book = OrderBook("Binance")
    connector = BinanceConnector(book, fetch=lire)
    connector.REFRESH = 0.0                # relire dès que possible
    connector._connect = lambda url: Socket([
        {"result": None, "id": 1},
        delta(198, 201, bids=[("100", "2")]),
        delta(202, 203, bids=[("99", "1")]),
        delta(204, 204, bids=[("98", "1")]),
        delta(205, 206, bids=[("97", "1")]),
        delta(207, 207, bids=[("96", "1")]),
        delta(208, 208, bids=[("95", "1")]),
    ])
    assert not book.connected
    asyncio.run(connector._stream())
    assert book.connected and lectures[0] is None, lectures
    assert len(lectures) >= 2, "la relecture périodique a eu lieu"
    assert connector._last == 208
    # Le dernier snapshot remplace le carnet ; ce qui le suit s'y ajoute.
    prix = [p for p, _ in book.top("bids", 9)]
    assert prix[0] == 100.0 and prix[-1] == 95.0, prix
    assert book.top("asks", 1) == [(101.0, 6.0)], "le second snapshot est posé"

    # Un snapshot en retard sur le flux est relu, pas posé : le lien ne
    # s'ouvre qu'avec celui qui rejoint les messages gardés.
    retards = [150, 150]

    def lire_en_retard():
        return {"lastUpdateId": retards.pop() if retards else vu[0],
                "bids": [["100", "1"]], "asks": [["101", "1"]]}

    book = OrderBook("Binance")
    connector = BinanceConnector(book, fetch=lire_en_retard)
    connector.REFRESH, connector.RETRY = 60.0, 0.0
    connector._connect = lambda url: Socket(
        [delta(n, n, bids=[(str(300 - n), "1")]) for n in range(201, 209)])
    asyncio.run(connector._stream())
    assert book.connected and connector._last == 208
    assert not retards and book.best_bid == 100, book.top("bids", 20)

    # Une relecture qui échoue ne retire rien à un carnet qui marche ;
    # un premier snapshot qui échoue, lui, vaut panne.
    essais = []

    def lire_puis_tomber():
        essais.append(1)
        if len(essais) > 1:
            raise OSError("lecture impossible")
        return {"lastUpdateId": 200, "bids": [["100", "1"]], "asks": [["101", "1"]]}

    book = OrderBook("Binance")
    connector = BinanceConnector(book, fetch=lire_puis_tomber)
    connector.REFRESH = 0.0
    connector._connect = lambda url: Socket(
        [delta(n, n, bids=[(str(100 - n + 200), "1")]) for n in range(201, 207)])
    asyncio.run(connector._stream())
    assert len(essais) >= 2 and connector._last == 206 and book.best_bid == 100

    def tomber():
        raise OSError("lecture impossible")

    connector = BinanceConnector(OrderBook("Binance"), fetch=tomber)
    connector._connect = lambda url: Socket([delta(1, 1), delta(2, 2), delta(3, 3)])
    try:
        asyncio.run(connector._stream())
    except OSError:
        pass
    else:
        raise AssertionError("sans premier snapshot, le flux doit tomber")
    print("  ✓ Binance : snapshot accordé au flux, relu sans fermer, trou resynchronisé")


def test_okx_carnet_entier_et_suite_verifiee():
    """OKX livre son carnet entier, puis des deltas numérotés : un numéro
    qui ne suit pas vaut resynchronisation."""
    from btcterm.exchanges import BookDesync, OKXConnector

    book = OrderBook("OKX")
    connector = OKXConnector(book, depth=4)
    assert book.max_levels == 4 and ":8443" not in connector.url
    assert connector.subscription["args"][0]["channel"] == "books"

    def message(action, bids, asks, seq, prev):
        return {"arg": {"channel": "books"}, "action": action,
                "data": [{"bids": [[p, q, "0", "1"] for p, q in bids],
                          "asks": [[p, q, "0", "1"] for p, q in asks],
                          "seqId": seq, "prevSeqId": prev}]}

    # Acquittement et avis de la plateforme : rien à lire, rien à lever.
    connector._handle({"event": "subscribe", "arg": {"channel": "books"}})
    connector._handle({"event": "notice", "msg": "port 8443 will be retired"})
    assert book.mid is None

    connector._handle(message(
        "snapshot", [("100", "1"), ("99", "2"), ("98", "3")],
        [("101", "1"), ("102", "2")], seq=10, prev=-1))
    assert book.best_bid == 100 and book.best_ask == 101
    # Un delta : une quantité changée, un niveau retiré, un niveau neuf.
    connector._handle(message(
        "update", [("100", "0"), ("99", "5"), ("97", "1")],
        [("101.5", "4")], seq=12, prev=10))
    assert book.top("bids", 5) == [(99.0, 5.0), (98.0, 3.0), (97.0, 1.0)]
    assert book.top("asks", 5) == [(101.0, 1.0), (101.5, 4.0), (102.0, 2.0)]
    # Un battement sans changement garde le numéro : il ne rompt rien.
    connector._handle(message("update", [], [], seq=12, prev=12))
    # La fenêtre borne le carnet : le cinquième niveau n'y entre pas.
    connector._handle(message(
        "update", [("96", "1"), ("95", "1"), ("94", "1")], [], seq=13, prev=12))
    assert [p for p, _ in book.top("bids", 9)] == [99.0, 98.0, 97.0, 96.0]

    # Un message perdu : le carnet ne vaut plus rien.
    try:
        connector._handle(message("update", [("99", "9")], [], seq=20, prev=17))
    except BookDesync as exc:
        assert "perdu" in str(exc)
    else:
        raise AssertionError("un numéro qui ne suit pas doit lever")
    assert book.top("bids", 1) == [(99.0, 5.0)], "le delta douteux n'est pas appliqué"
    # Un nouveau snapshot repart de zéro, quel que soit son numéro.
    connector._handle(message("snapshot", [("90", "1")], [("91", "1")],
                              seq=3, prev=-1))
    assert book.best_bid == 90 and connector._seq == 3
    # Et un delta sans snapshot — une reconnexion mal engagée — lève aussi.
    neuf = OKXConnector(OrderBook("OKX"))
    try:
        neuf._handle(message("update", [("1", "1")], [], seq=2, prev=1))
    except BookDesync:
        pass
    else:
        raise AssertionError("un delta avant le snapshot doit lever")
    print("  ✓ OKX : carnet entier, deltas suivis, message perdu resynchronisé")


def test_profondeur_cadree_sur_le_carnet_median():
    """La profondeur comparée se cadre sur l'étendue médiane des carnets.

    Constaté le 11 novembre 2026 : Coinbase portait ses niveaux à 3 % du
    prix, les autres à quelques centièmes de pour cent, et l'axe étiré
    par le premier écrasait les quatre autres autour de zéro.
    """
    from terminal.charts import build_depth_chart, depth_window

    assert depth_window([]) is None and depth_window([0.0]) is None
    assert abs(depth_window([0.004, 0.057, 0.025, 0.002, 3.1]) - 0.03) < 1e-12
    assert abs(depth_window([0.02, 0.5]) - 0.3) < 1e-12, "à deux, la moyenne arrondie"
    # Jamais plus d'une fois et demie la médiane : le cadre colle aux carnets.
    assert all(m <= depth_window([m]) <= 1.5 * m * (1 + 1e-9)
               for m in (0.011, 0.051, 0.26, 0.51, 0.76, 1.6, 4.9))
    assert depth_window([0.05]) == 0.05, "un cran exact reste le sien"
    # Le cadre ne tremble pas quand l'étendue respire.
    assert len({depth_window([0.004, 0.057, e, 0.002, 3.1])
                for e in (0.021, 0.025, 0.028, 0.03)}) == 1

    def carnet(pas: float, volume: float, niveaux: int = 10) -> OrderBook:
        book = OrderBook("essai")
        for k in range(1, niveaux + 1):
            book.bids[100_000 * (1 - pas * k / 100)] = volume
            book.asks[100_000 * (1 + pas * k / 100)] = volume
        return book

    books = {"Serré": carnet(0.0005, 1.0), "Moyen": carnet(0.004, 0.5),
             "Large": carnet(0.3, 3.0), "Vide": OrderBook("vide")}
    figure = build_depth_chart(books)
    x, y = figure["layout"]["xaxis"], figure["layout"]["yaxis"]
    assert x["range"] == [-0.05, 0.05], x["range"]
    # Dans le cadre : tout le carnet serré (10 BTC), tout le moyen (5), et
    # aucun niveau du large, dont le premier est à 0,3 %.
    assert abs(y["range"][1] - 10 * 1.08) < 1e-9, y["range"]
    assert "1 carnet va au-delà" in x["title"]["text"], x["title"]
    # Les courbes restent entières : le zoom arrière montre ce qui dépasse.
    large = [t for t in figure["data"] if t["name"] == "Large"]
    assert len(large) == 2 and max(large[1]["x"]) > 2.9
    # Une entrée de légende par plateforme, pour ses deux côtés.
    assert [t["name"] for t in figure["data"] if t["showlegend"]] == \
        ["Serré", "Moyen", "Large"]

    # Des carnets de même portée : rien ne dépasse, le titre ne dit rien.
    pareils = build_depth_chart({"A": carnet(0.004, 1.0),
                                 "B": carnet(0.004, 2.0)})
    assert "au-delà" not in pareils["layout"]["xaxis"]["title"]["text"]
    # Et sans aucun carnet, la figure reste vide et sans cadre imposé.
    vide = build_depth_chart({"Vide": OrderBook("vide")})
    assert vide["data"] == [] and "range" not in vide["layout"]["xaxis"]
    print("  ✓ profondeur cadrée sur le carnet médian, volume ajusté au cadre")


def test_la_liquidite_a_une_memoire():
    """Le volume à portée du prix s'échantillonne, se moyenne, se
    journalise par plateforme, et se relit en une somme qui ne change pas
    d'ensemble en cours de route."""
    import tempfile
    import time

    import pandas as pd

    from btcterm.hub import MarketHub
    from btcterm.journal import Journal
    from terminal.charts import build_liquidity_chart, liquidity_series
    from terminal.panels import orderbook as panneau

    # ── Le carnet : ce qui est à moins de 0,1 % du prix médian.
    book = OrderBook("essai")
    assert book.depth_within(0.1) is None, "un carnet vide ne dit rien"
    book.replace({99_990.0: 1.0, 99_950.0: 2.0, 99_800.0: 5.0},
                 {100_010.0: 0.5, 100_090.0: 1.5, 100_300.0: 7.0})
    assert book.depth_within(0.1) == (3.0, 2.0)
    assert book.depth_within(1.0) == (8.0, 9.0)
    book.replace({100_020.0: 1.0}, {100_010.0: 1.0})
    assert book.depth_within(0.1) is None, "un carnet croisé non plus"

    # ── Le hub : dix échantillons, une moyenne, une ligne par plateforme.
    with tempfile.TemporaryDirectory() as tmp:
        hub = MarketHub(collect_news=False, keep_journal=False)
        hub.journal = Journal(Path(tmp) / "journal.db")
        maintenant = time.time()
        for nom, volume in (("Binance", 10.0), ("Kraken", 2.0)):
            hub.books[nom].connected = True
        for k in range(10):
            hub.books["Binance"].replace({99_990.0: 10.0 + k}, {100_010.0: 20.0})
            hub.books["Kraken"].replace({99_990.0: 2.0}, {100_010.0: 4.0})
            if k >= 6:          # Kraken se coupe : quatre échantillons en moins
                hub.books["Kraken"].connected = False
            hub._sample_depth()
        hub._flush_depth(now=maintenant - 600)
        lignes = hub.journal.depth_between(0, maintenant)
        assert [(r["exchange"], r["bid_btc"], r["ask_btc"]) for r in lignes] == \
            [("Binance", 14.5, 20.0), ("Kraken", 2.0, 4.0)], [tuple(r) for r in lignes]
        assert hub._depth_acc == {}, "la période repart de zéro"
        # Trop peu d'échantillons : la plateforme n'écrit rien.
        for _ in range(hub.DEPTH_MIN_SAMPLES - 1):
            hub._sample_depth()
        hub._flush_depth(now=maintenant - 300)
        assert len(hub.journal.depth_between(0, maintenant)) == 2
        # Un carnet figé depuis plus de dix secondes ne compte pas.
        hub.books["Binance"].timestamp = time.time() - 60
        hub._sample_depth()
        assert hub._depth_acc == {}

        frame = hub.book_depth_history(days=1)
        assert list(frame.columns) == ["time", "exchange", "bid", "ask"]
        assert len(frame) == 2
        # La purge garde la liquidité aussi longtemps que les instantanés.
        hub.journal.record_book_depth(maintenant - 500 * 86_400,
                                      [("Binance", 1.0, 1.0)])
        hub.journal.record_book_depth(maintenant - 100 * 86_400,
                                      [("Binance", 1.0, 1.0)])
        hub.journal.purge()
        assert len(hub.journal.depth_between(0, maintenant)) == 3

        # Le panneau : en constitution tant qu'il n'y a pas deux relevés.
        figure, badge = panneau.render_liquidity(hub)
        assert figure["data"] == [] and "constitution" in str(figure["layout"])
        hub.journal.close()

    # ── La somme : un ensemble de plateformes qui ne change pas.
    t0 = pd.Timestamp("2026-11-11 00:00")
    lignes = []
    for k in range(40):
        moment = t0 + pd.Timedelta(minutes=5 * k)
        if k >= 30:                       # une heure de veille avant la fin
            moment += pd.Timedelta(hours=1)
        lignes.append((moment, "Binance", 100.0, 80.0))
        if k != 7:                        # OKX manque un relevé
            lignes.append((moment, "OKX", 50.0, 40.0))
        if k < 10:                        # Coinbase n'est là qu'un quart du temps
            lignes.append((moment, "Coinbase", 5.0, 5.0))
    history = pd.DataFrame(lignes, columns=["time", "exchange", "bid", "ask"])
    serie, gardees, ecartees = liquidity_series(history)
    assert gardees == ["Binance", "OKX"] and ecartees == ["Coinbase"]
    assert len(serie) == 39, "le relevé sans OKX est écarté, pas additionné à moitié"
    assert set(serie["bid"]) == {150.0} and set(serie["ask"]) == {120.0}
    assert liquidity_series(history.iloc[0:0])[0].empty

    figure = build_liquidity_chart(serie, gap=900)
    achats, ventes = figure["data"]
    assert achats["name"] == "achats" and ventes["name"] == "ventes"
    assert achats["y"].count(None) == 1, "le trait se rompt sur l'heure de veille"
    assert len(achats["x"]) == 40

    # ── En plein écran : ce que chaque plateforme offre, empilé.
    gardes = history[history["exchange"].isin(gardees)
                     & history["time"].isin(serie["time"])]
    empile = build_liquidity_chart(serie, gap=900, by_exchange=gardes)
    assert not any("stackgroup" in t for t in empile["data"]), \
        "l'empilement de Plotly comble les trous : il est fait ici"
    # Deux séances — l'heure de veille les sépare —, deux plateformes :
    # quatre traces, et la légende n'en nomme qu'une par plateforme.
    assert [t["name"] for t in empile["data"]] == ["Binance", "OKX"] * 2
    assert [t["showlegend"] for t in empile["data"]] == [True, True, False, False]
    assert [t["fill"] for t in empile["data"]] == ["tozeroy", "tonexty"] * 2
    assert sum(len(t["x"]) for t in empile["data"][::2]) == 39
    assert all(None not in t["y"] for t in empile["data"]), \
        "une séance est continue : la rupture est entre les traces"
    binance, okx = empile["data"][:2]
    assert set(binance["y"]) == {180.0}
    # OKX porte le cumul en ordonnée, et sa part au survol.
    assert set(okx["y"]) == {270.0} and set(okx["customdata"]) == {90.0}
    assert empile["data"][0]["x"][-1] < empile["data"][2]["x"][0]
    assert empile["layout"]["showlegend"] is True

    # ── Le badge : le dernier relevé, et son écart à la moyenne.
    class Hub:
        SNAPSHOT_GAP, DEPTH_BAND = 900, 0.1

        def book_depth_history(self, days):
            creux = history.copy()
            dernier = creux["time"] == creux["time"].max()
            creux.loc[dernier, ["bid", "ask"]] = creux.loc[dernier, ["bid", "ask"]] / 2
            return creux

    _, badge = panneau.render_liquidity(Hub())
    texte = str(badge)
    assert "135 BTC" in texte and "-49 %" in texte, texte
    assert "sans Coinbase" in texte and "2/3" in texte
    print("  ✓ liquidité : échantillonnée, moyennée, journalisée, sommée sans trou")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCarnets et connecteurs — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Les carnets ne gardent plus de niveau fantôme.\n")
