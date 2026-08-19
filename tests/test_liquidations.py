#!/usr/bin/env python3
"""
Fil des liquidations : lecture du flux et fenêtre glissante.

Le flux `!forceOrder@arr` de Binance est épisodique — il peut rester
silencieux plusieurs minutes —, ce qui le rend impossible à couvrir par
une simple observation : ce test lui injecte donc des messages au format
documenté et vérifie ce qu'on en déduit.

Le point qui compte est le sens : une **vente** forcée ferme une position
longue, un **achat** forcé ferme une position courte. L'inverser
donnerait un panneau qui raconte exactement le contraire de ce qui se
passe.

Aucun réseau n'est touché.

Lancement :
    python tests/test_liquidations.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.liquidations import LiquidationFeed  # noqa: E402
from terminal.panels import liquidations as panneau  # noqa: E402


def message(symbol="BTCUSDT", side="SELL", price="64000", qty="0.5",
            when=None):
    """Message `forceOrder` au format documenté par Binance."""
    when = int((when if when is not None else time.time()) * 1000)
    return {"e": "forceOrder", "E": when,
            "o": {"s": symbol, "S": side, "o": "LIMIT", "f": "IOC",
                  "q": qty, "p": price, "ap": price, "X": "FILLED",
                  "l": qty, "z": qty, "T": when}}


def test_sens_des_liquidations():
    feed = LiquidationFeed()
    feed._handle(message(side="SELL"))
    feed._handle(message(side="BUY"))

    sens = [event.side for event in feed.latest(2)]
    assert sens == ["short", "long"], sens  # le plus récent d'abord
    print("  ✓ vente forcée = long liquidé, achat forcé = short liquidé")


def test_totaux_par_cote_et_fenetre():
    feed = LiquidationFeed()
    feed._handle(message(side="SELL", price="64000", qty="1"))     # 64 000 $
    feed._handle(message(side="BUY", price="64000", qty="0.5"))    # 32 000 $
    feed._handle(message(symbol="ETHUSDT", side="SELL", price="3000", qty="2"))
    # Hors fenêtre : une heure plus tôt.
    feed._handle(message(side="SELL", price="60000", qty="10",
                         when=time.time() - 7200))

    totaux = feed.totals(window=3600)
    assert totaux["long"] == 70_000, totaux
    assert totaux["short"] == 32_000, totaux
    assert totaux["btc"] == 96_000, totaux
    assert totaux["count"] == 3, totaux
    print("  ✓ totaux par côté, part Bitcoin, et fenêtre glissante")


def test_messages_invalides_ignores():
    feed = LiquidationFeed()
    feed._handle({"e": "forceOrder"})                      # sans ordre
    feed._handle(message(price="0"))                       # prix nul
    feed._handle(message(qty="0"))                         # quantité nulle
    feed._handle({"o": {"s": "BTCUSDT", "S": "SELL", "q": "x", "ap": "y"}})
    assert not feed.events, "un message inexploitable a été retenu"
    print("  ✓ messages incomplets ou aberrants écartés")


def test_fenetre_bornee():
    """La mémoire est bornée : c'est un indicateur, pas un journal."""
    feed = LiquidationFeed(maxlen=5)
    for i in range(20):
        feed._handle(message(qty=str(i + 1)))
    assert len(feed.events) == 5
    assert feed.latest(1)[0].quantity == 20
    print("  ✓ fenêtre bornée à sa taille, les plus récentes conservées")


def test_etat_de_connexion():
    feed = LiquidationFeed()
    assert not feed.connected
    feed._mark_connected()
    assert feed.connected and feed.error is None
    feed._mark_disconnected(RuntimeError("flux coupé"))
    assert not feed.connected and "flux coupé" in feed.error
    assert feed.last_event_age() is None
    print("  ✓ état de connexion publié sans carnet")


def test_rendu_du_panneau():
    """Le flux étant épisodique, le rendu ne se voit pas à l'écran.

    Le contrôle Firefox trouve presque toujours le panneau vide : c'est
    ici que la mise en forme des lignes et des totaux est vérifiée.
    """
    feed = LiquidationFeed()
    feed._mark_connected()
    feed._handle(message(side="SELL", price="64000", qty="2"))
    feed._handle(message(symbol="ETHUSDT", side="BUY", price="3000", qty="4"))

    lignes = [panneau._row(event) for event in feed.latest(5)]
    assert len(lignes) == 2
    assert all(len(ligne.children) == 5 for ligne in lignes), "colonnes manquantes"

    assert panneau._montant(1_250_000) == "1.25 M$"
    assert panneau._montant(64_000) == "64 k$"
    assert panneau._montant(120) == "120 $"

    badges = panneau._badges(feed)
    textes = str(badges)
    assert "longs" in textes and "shorts" in textes and "BTC" in textes
    print("  ✓ lignes, montants abrégés et totaux mis en forme")


def test_panneau_sans_flux():
    """Un flux coupé et un marché calme ne doivent pas se confondre."""
    feed = LiquidationFeed()
    assert "coupé" in str(panneau._badges(feed))
    feed._mark_connected()
    assert "aucune liquidation" in str(panneau._badges(feed))
    print("  ✓ flux coupé et marché calme distingués")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nFil des liquidations — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le fil des liquidations lit correctement son flux.\n")
