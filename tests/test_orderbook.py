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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCarnets et connecteurs — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Les carnets ne gardent plus de niveau fantôme.\n")
