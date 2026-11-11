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

La seconde source, Bybit, est vérifiée de la même façon : son champ
`S` est le côté de la **position** — `Buy` pour un long liquidé —, à
l'inverse de Binance ; ses événements vont au même magasin, marqués de
leur plateforme ; et l'état du fil se compose de ses deux liens. Un lien
qui tient sans rien livrer — Binance depuis certains pays — doit se
lire dans le badge, sans qu'un lien fraîchement rouvert passe pour muet.

La relecture du journal au démarrage (`restore`, `_warm_liquidations`)
est vérifiée sur une base temporaire : elle doit rendre la fenêtre sans
rien réécrire — un événement relu qui repasserait par `on_event`
s'inscrirait au journal une seconde fois, et chaque redémarrage
doublerait l'historique.

Aucun réseau n'est touché.

Lancement :
    python tests/test_liquidations.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.hub import MarketHub  # noqa: E402
from btcterm.journal import Journal  # noqa: E402
from btcterm.liquidations import (  # noqa: E402
    BybitLiquidationConnector, Liquidation, LiquidationFeed)
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
    assert all(len(ligne.children) == 6 for ligne in lignes), "colonnes manquantes"

    # Le prix suit sa taille : un bitcoin ne s'écrit pas 6.4e+04.
    textes = str(lignes)
    assert "e+" not in textes and "64,000" in textes and "3,000" in textes
    assert panneau._prix(79090.5) == "79,090"
    assert panneau._prix(4321.5) == "4,322"
    assert panneau._prix(180.254) == "180.25"
    assert panneau._prix(0.23456) == "0.2346"

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


def message_bybit(symbol="BTCUSDT", side="Buy", price="79000", qty="0.5",
                  when=None):
    """Message `allLiquidation` au format documenté par Bybit."""
    when = int((when if when is not None else time.time()) * 1000)
    return {"topic": f"allLiquidation.{symbol}", "type": "snapshot",
            "ts": when,
            "data": [{"T": when, "s": symbol, "S": side, "v": qty, "p": price}]}


def test_bybit_sens_et_plateforme():
    feed = LiquidationFeed()
    bybit = BybitLiquidationConnector(feed)
    bybit._handle(message_bybit(side="Buy"))
    bybit._handle(message_bybit(side="Sell"))
    # Un événement Binance dans le même magasin.
    feed._handle(message(side="SELL"))

    derniers = feed.latest(3)
    assert [e.side for e in derniers] == ["long", "short", "long"], derniers
    assert [e.exchange for e in derniers] == ["Binance", "Bybit", "Bybit"]
    assert derniers[1].notional == 79000 * 0.5
    print("  ✓ Bybit : Buy = long liquidé, Sell = short ; plateforme marquée")


def test_bybit_ignore_le_reste_et_refuse_l_abonnement_rate():
    feed = LiquidationFeed()
    bybit = BybitLiquidationConnector(feed, symbols=("BTCUSDT", "ETHUSDT"))
    assert bybit.subscription["args"] == ["allLiquidation.BTCUSDT",
                                          "allLiquidation.ETHUSDT"]
    bybit._handle({"success": True, "op": "subscribe"})
    bybit._handle({"topic": "tickers.BTCUSDT", "data": {"p": "1"}})
    bybit._handle({"topic": "allLiquidation.BTCUSDT", "data": [{"s": "BTCUSDT"}]})
    bybit._handle({"topic": "allLiquidation.BTCUSDT",
                   "data": [{"T": 1, "s": "BTCUSDT", "S": "Buy", "v": "0", "p": "1"}]})
    assert not feed.events, "acquittement, autre canal, données incomplètes : rien"
    try:
        bybit._handle({"success": False, "ret_msg": "handler not found"})
    except RuntimeError as exc:
        assert "handler not found" in str(exc)
    else:
        raise AssertionError("un abonnement refusé doit valoir panne")
    print("  ✓ acquittements et autres canaux ignorés, refus signalé")


def test_etat_par_lien():
    feed = LiquidationFeed()
    bybit = BybitLiquidationConnector(feed)
    assert feed.missing() == ["Binance", "Bybit"] and not feed.connected

    bybit._mark_connected()
    assert feed.connected and feed.missing() == ["Binance"]
    assert feed.error is None, "aucun lien tombé en erreur : pas d'erreur"

    feed._mark_connected()
    assert feed.missing() == []

    bybit._mark_disconnected(RuntimeError("abonnement refusé"))
    assert feed.connected, "Binance tient encore"
    assert feed.missing() == ["Bybit"] and "refusé" in feed.error

    badges = str(panneau._badges(feed))
    assert "sans Bybit" in badges and "coupé" not in badges
    feed._mark_disconnected(RuntimeError("flux coupé"))
    assert "coupé" in str(panneau._badges(feed))
    print("  ✓ un lien suffit au fil ; le panneau nomme celui qui manque")


def test_lien_ouvert_mais_muet():
    """Un lien qui tient sans rien livrer se nomme ; un lien neuf, non."""
    feed = LiquidationFeed()
    bybit = BybitLiquidationConnector(feed)
    maintenant = time.time()

    # Les deux liens tiennent, mais seul Bybit livre : Binance s'est
    # ouvert il y a vingt minutes et n'a rien dit depuis.
    feed._mark_connected()
    feed.since["Binance"] = maintenant - 1200
    bybit._mark_connected()
    bybit._handle(message_bybit())

    assert feed.last_event_age("Bybit") < 5
    assert 1195 < feed.last_event_age("Binance") < 1205
    muets = feed.silent(900)
    assert [nom for nom, _ in muets] == ["Binance"], muets
    badges = str(panneau._badges(feed))
    assert "Binance muet depuis 20 min" in badges, badges
    assert "sans Binance" not in badges and "sans Bybit" not in badges

    # Un lien qui vient de se rouvrir n'est pas muet, même si son
    # dernier événement est vieux : le silence se compte depuis l'ouverture.
    feed._mark_disconnected(RuntimeError("coupé"))
    assert feed.last_event_age("Binance") is None, "un lien tombé n'a pas d'âge"
    feed._mark_connected()
    assert feed.last_event_age("Binance") < 5
    assert feed.silent(900) == []

    # Un événement Binance vieux d'une heure, relu du journal, ne rend pas
    # le lien plus muet qu'il n'est — mais un lien ouvert depuis une heure
    # dont le dernier mot a une heure, oui.
    feed.restore([Liquidation(time=maintenant - 3600, symbol="BTCUSDT",
                              side="long", price=64000, quantity=0.5)])
    assert feed.last_seen["Binance"] == maintenant - 3600
    assert feed.silent(900) == [], "le lien vient de se rouvrir"
    feed.since["Binance"] = maintenant - 3600
    assert [nom for nom, _ in feed.silent(900)] == ["Binance"]
    # L'événement vieux ne rajeunit pas un dernier mot plus récent.
    bybit._handle(message_bybit(when=maintenant - 7200))
    assert feed.last_event_age("Bybit") < 5

    # Un lien muet reste nommé quand la fenêtre est vide.
    vide = LiquidationFeed()
    vide._mark_connected()
    vide.since["Binance"] = maintenant - 1000
    assert "aucune liquidation" in str(panneau._badges(vide))
    assert "Binance muet depuis 16 min" in str(panneau._badges(vide))
    print("  ✓ un lien ouvert mais muet se nomme ; un lien neuf, non")


def _texte(composant) -> str:
    """Le texte qu'un composant Dash met à l'écran, enfants compris."""
    if composant is None:
        return ""
    if isinstance(composant, (str, int, float)):
        return str(composant)
    if isinstance(composant, (list, tuple)):
        return "".join(_texte(c) for c in composant)
    return _texte(getattr(composant, "children", None))


def test_badge_de_grille_tient_dans_sa_cellule():
    """Dans la grille, le badge ne dépasse jamais `COMPACT_MAX` caractères.

    Constaté au contrôle d'interface le 11 novembre 2026 : Binance et
    Bybit muets ensemble, « BIN muet · BYB muet » débordait de 96 px.
    """
    court = panneau._montant_court
    assert [court(v) for v in (950, 999.6, 288_400, 999_600, 1_250_000,
                               12_540_000, 99_960_000, 125_400_000,
                               1_250_000_000)] == \
        ["950 $", "1 k$", "288 k$", "1.0 M$", "1.2 M$", "12.5 M$",
         "100 M$", "125 M$", "1.2 Md$"]
    # Sept caractères jusqu'à dix milliards en une heure — la pire journée
    # connue du marché n'en a pas liquidé le double en vingt-quatre.
    assert all(len(court(10 ** e * m)) <= 7
               for e in range(0, 10) for m in (1, 2.5, 9.9, 9.996, 9.9996)
               if 10 ** e * m < 9.9e9)

    maintenant = time.time()

    def fil(absents=(), muets=(), montant=0.0):
        feed = LiquidationFeed()
        for nom in ("Bybit", "OKX"):
            feed.attach(nom)
        for nom in ("Binance", "Bybit", "OKX"):
            if nom in absents:
                continue
            feed.mark(nom, True)
            if nom in muets:
                feed.since[nom] = maintenant - 1200
            else:
                feed.last_seen[nom] = maintenant
        if montant:
            for cote in ("long", "short"):
                feed.events.append(Liquidation(
                    time=maintenant, symbol="ETHUSDT", side=cote,
                    price=1.0, quantity=montant, exchange="OKX"))
        return feed

    attendus = {
        ((), ()): None,
        ((), ("Binance",)): "BIN muet",
        (("Bybit",), ()): "sans BYB",
        ((), ("Binance", "Bybit")): "2 muets",
        (("Binance", "Bybit"), ()): "2 coupés",
        (("Binance",), ("Bybit",)): "1 lien/3",
    }
    for (absents, muets), mot in attendus.items():
        for montant in (0.0, 288_000.0, 12_540_000.0, 125_400_000.0):
            badge = panneau._badges(fil(absents, muets, montant), verbose=False)
            texte = _texte(badge)
            assert len(texte) <= panneau.COMPACT_MAX, (texte, len(texte))
            if mot is None:
                assert "muet" not in texte and "sans" not in texte, texte
            else:
                assert texte.endswith(" · " + mot), texte
            if montant:
                assert ("1 h" in texte) == (mot is None), texte

    # Le survol nomme ce que le badge ne fait que compter.
    deux = str(panneau._badges(fil((), ("Binance", "Bybit")), verbose=False))
    assert "lien Binance ouvert" in deux and "lien Bybit ouvert" in deux
    assert "depuis 20 min" in deux
    # Et le plein écran garde ses noms en toutes lettres.
    plein = _texte(panneau._badges(fil((), ("Binance", "Bybit"))))
    assert "Binance muet depuis 20 min" in plein and "Bybit muet" in plein
    print("  ✓ le badge de la grille tient en "
          f"{panneau.COMPACT_MAX} caractères, liens muets compris")


def test_panneau_etiquette_la_plateforme():
    feed = LiquidationFeed()
    BybitLiquidationConnector(feed)._handle(message_bybit())
    feed._handle(message(symbol="ETHUSDT", side="SELL", price="3000", qty="2"))
    ligne_bybit, ligne_binance = str(panneau._row(feed.latest(2)[1])), \
        str(panneau._row(feed.latest(2)[0]))
    assert "BYB" in ligne_bybit and "BIN" in ligne_binance
    print("  ✓ chaque ligne dit sa plateforme")


def test_restore_ne_resignale_rien():
    """Les événements relus du journal ne repassent pas par `on_event`."""
    feed = LiquidationFeed()
    vus = []
    feed.on_event = vus.append
    maintenant = time.time()
    rendus = feed.restore([
        Liquidation(time=maintenant - 120, symbol="BTCUSDT", side="long",
                    price=64000, quantity=0.5, exchange="Binance"),
        # Un événement sans taille est écarté, comme dans `record`.
        Liquidation(time=maintenant - 60, symbol="ETHUSDT", side="short",
                    price=3000, quantity=0, exchange="Bybit"),
        Liquidation(time=maintenant - 30, symbol="SOLUSDT", side="long",
                    price=150, quantity=4, exchange="Bybit"),
    ])
    assert rendus == 2, rendus
    assert vus == [], "un événement relu a été réécrit au journal"
    assert feed.totals(3600)["count"] == 2
    # L'ordre chronologique est celui de la fenêtre : la plus récente
    # d'abord dans `latest`.
    assert [e.symbol for e in feed.latest(2)] == ["SOLUSDT", "BTCUSDT"]
    print("  ✓ fenêtre rendue, journal intact, ordre préservé")


def test_le_hub_relit_la_derniere_heure():
    """Un redémarrage retrouve la fenêtre au lieu d'un panneau vide."""
    import tempfile

    with tempfile.TemporaryDirectory() as dossier:
        journal = Journal(Path(dossier) / "journal.db")
        maintenant = time.time()
        recents = [
            Liquidation(time=maintenant - 300, symbol="BTCUSDT", side="long",
                        price=64000, quantity=0.5, exchange="Binance"),
            Liquidation(time=maintenant - 100, symbol="SOLUSDT", side="short",
                        price=150, quantity=4, exchange="Bybit"),
        ]
        # Une liquidation d'avant-hier : hors fenêtre, elle ne revient pas.
        vieille = Liquidation(time=maintenant - 200_000, symbol="XRPUSDT",
                              side="long", price=2, quantity=1000,
                              exchange="Bybit")
        for event in recents + [vieille]:
            journal.record_liquidation(event)

        hub = MarketHub(keep_journal=False)
        hub.journal = journal
        assert hub._warm_liquidations() == 2
        assert [e.symbol for e in hub.liquidations.latest(3)] == [
            "SOLUSDT", "BTCUSDT"]
        assert hub.liquidations.totals(3600)["count"] == 2
        # Rien n'a été réécrit : le journal tient toujours trois lignes.
        assert len(journal.liquidations_between(0, maintenant + 1)) == 3
        journal.close()
    print("  ✓ dernière heure rendue, plus ancien laissé au journal")


def test_sans_journal_le_demarrage_ne_rend_rien():
    """Un hub sans journal démarre sur une fenêtre vide, sans erreur."""
    hub = MarketHub(keep_journal=False)
    assert hub.journal is None
    assert hub._warm_liquidations() == 0
    assert hub.liquidations.totals(3600)["count"] == 0
    print("  ✓ pas de journal, pas de relecture, pas d'erreur")



def test_la_carte_des_liquidations():
    """Le notionnel liquidé par tranche de prix, longs et shorts à part, la
    seule paire demandée, et les plateformes présentes dites."""
    from btcterm.liquidations import price_map

    def evenement(prix, notionnel, cote, plateforme="Bybit", paire="BTCUSDT"):
        return {"symbol": paire, "side": cote, "price": prix,
                "notional": notionnel, "exchange": plateforme, "ts": prix / 1000}

    evenements = [evenement(70_000, 1e6, "long"),
                  evenement(70_050, 2e6, "long", "Binance"),
                  evenement(80_000, 5e5, "short"),
                  evenement(3_000, 9e9, "long", paire="ETHUSDT"),
                  # Une base antérieure à la colonne `exchange` : Binance.
                  evenement(75_000, 1e5, "short", None)]
    carte = price_map(evenements, bins=10)
    assert carte["count"] == 4 and carte["notional"] == 1e6 + 2e6 + 5e5 + 1e5
    assert carte["exchanges"] == ["Binance", "Bybit"]
    assert abs(carte["width"] - 1_000) < 1e-9
    assert carte["longs"][0] == 3e6 and carte["shorts"][0] == 0
    assert carte["shorts"][-1] == 5e5, "le prix le plus haut tombe dans la dernière tranche"
    assert carte["shorts"][5] == 1e5
    assert len(carte["centers"]) == 10 and carte["centers"][0] == 70_500
    assert price_map([evenement(3_000, 1, "long", paire="ETHUSDT")]) is None
    assert price_map([]) is None
    # Un seul prix : une tranche, pas une division par zéro.
    seul = price_map([evenement(70_000, 1e6, "long")], bins=4)
    assert sum(seul["longs"]) == 1e6
    print("  ✓ carte par tranche, côtés séparés, paire filtrée, plateformes dites")


def test_okx_sans_actions_ni_matieres_premieres():
    """Près de la moitié des perpétuels d'OKX ne sont pas des
    cryptomonnaies : ils sont connus, mais hors du fil."""
    import time as horloge

    from btcterm import sources
    from btcterm.liquidations import LiquidationFeed, OKXLiquidationConnector

    contrats = sources.okx_swap_contracts([
        {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctValCcy": "BTC",
         "instCategory": "1"},
        {"instId": "SNDK-USDT-SWAP", "ctVal": "1", "ctValCcy": "SNDK",
         "instCategory": "3"},
        {"instId": "XAU-USDT-SWAP", "ctVal": "0.001", "ctValCcy": "XAU",
         "instCategory": "4"},
        {"instId": "ANCIEN-USDT-SWAP", "ctVal": "10", "ctValCcy": "ANCIEN"},
        {"instId": "SANS-VALEUR-SWAP", "ctVal": "", "instCategory": "1"},
    ])
    assert contrats == {"BTC-USDT-SWAP": (0.01, "BTC"), "SNDK-USDT-SWAP": None,
                        "XAU-USDT-SWAP": None,
                        "ANCIEN-USDT-SWAP": (10.0, "ANCIEN")}, contrats
    try:
        sources.okx_swap_contracts([{"instId": "MU-USDT-SWAP", "ctVal": "1",
                                     "ctValCcy": "MU", "instCategory": "3"}])
    except RuntimeError:
        pass
    else:
        raise AssertionError("une table sans cryptomonnaie doit lever")

    feed = LiquidationFeed()
    okx = OKXLiquidationConnector(feed)
    okx.contracts = contrats
    okx.since = horloge.time() - 5
    maintenant = str(int(horloge.time() * 1000))
    ligne = {"posSide": "short", "side": "buy", "bkPx": "900", "sz": "1000",
             "ts": maintenant}
    inconnu = okx.handle({"data": [
        {"instId": "SNDK-USDT-SWAP", "details": [ligne]},
        {"instId": "XAU-USDT-SWAP", "details": [ligne]},
        {"instId": "BTC-USDT-SWAP", "details": [
            {**ligne, "bkPx": "75000", "sz": "10"}]},
    ]})
    assert inconnu is False, "un contrat écarté est connu : rien à redemander"
    assert [e.symbol for e in feed.latest(10)] == ["BTCUSDT"]
    print("  ✓ OKX : actions et matières premières connues, hors du fil")


def test_okx_troisieme_source():
    """OKX compte en contrats : linéaire en unités du sous-jacent, inverse en
    dollars ; le côté vient de `posSide`, ou du sens de l'ordre en mode net ;
    un contrat inconnu se signale, et le rejeu de l'abonnement est écarté."""
    import time as horloge
    from btcterm.liquidations import LiquidationFeed, OKXLiquidationConnector

    feed = LiquidationFeed()
    okx = OKXLiquidationConnector(feed)
    okx.contracts = {"BTC-USDT-SWAP": (0.01, "BTC"),
                     "BTC-USD-SWAP": (100.0, "USD")}
    okx.since = horloge.time() - 5
    maintenant = int(horloge.time() * 1000)
    inconnu = okx.handle({"data": [
        {"instId": "BTC-USDT-SWAP", "details": [
            {"posSide": "long", "side": "sell", "bkPx": "75000", "sz": "8.2",
             "ts": str(maintenant)},
            # Rejeu de l'abonnement : la veille au soir.
            {"posSide": "short", "side": "buy", "bkPx": "76000", "sz": "50",
             "ts": str(maintenant - 10 * 3600 * 1000)}]},
        {"instId": "BTC-USD-SWAP", "details": [
            # Mode net : un ordre forcé d'achat liquide un short.
            {"posSide": "", "side": "buy", "bkPx": "80000", "sz": "40",
             "ts": str(maintenant)}]},
        {"instId": "NOUVEAU-USDT-SWAP", "details": [
            {"posSide": "long", "bkPx": "1", "sz": "1", "ts": str(maintenant)}]},
    ]})
    assert inconnu is True, "un contrat inconnu doit se signaler"
    evenements = {e.symbol: e for e in feed.latest(10)}
    assert set(evenements) == {"BTCUSDT", "BTCUSD"}, evenements
    lineaire = evenements["BTCUSDT"]
    assert lineaire.side == "long" and abs(lineaire.quantity - 0.082) < 1e-12
    assert abs(lineaire.notional - 6_150) < 1e-6
    inverse = evenements["BTCUSD"]
    assert inverse.side == "short" and abs(inverse.notional - 4_000) < 1e-6, \
        "40 contrats de 100 $"
    assert inverse.exchange == "OKX" and feed.links["OKX"] == (False, None)
    try:
        okx.handle({"event": "error", "msg": "canal inconnu"})
    except RuntimeError:
        pass
    else:
        raise AssertionError("un refus d'abonnement doit lever")
    print("  ✓ contrats convertis, côté lu, rejeu écarté, inconnu signalé")


def test_le_bitcoin_se_reconnait_exactement():
    """« Commence par BTC » comptait l'indice de dominance de Binance
    (`BTCDOMUSDT`, quelques milliers de points) comme du bitcoin, dans le
    « dont BTC » du panneau comme sur la carte."""
    from btcterm.liquidations import Liquidation, LiquidationFeed, is_bitcoin, price_map

    assert all(is_bitcoin(s) for s in ("BTCUSDT", "BTCUSDC", "BTCUSD",
                                       "BTCUSDT_261225"))
    assert not any(is_bitcoin(s) for s in ("BTCDOMUSDT", "ETHUSDT", "", None))
    feed = LiquidationFeed()
    import time as horloge
    for symbole, prix in (("BTCUSDT", 75_000.0), ("BTCDOMUSDT", 5_000.0)):
        feed.record(Liquidation(time=horloge.time(), symbol=symbole, side="long",
                                price=prix, quantity=1.0, exchange="Binance"))
    assert feed.totals()["btc"] == 75_000.0
    carte = price_map([{"symbol": "BTCDOMUSDT", "side": "long", "price": 5_000,
                        "notional": 5_000, "exchange": "Binance", "ts": 1}])
    assert carte is None, "l'indice de dominance n'a rien à faire sur l'axe du bitcoin"
    print("  ✓ BTC en dollars et ses échéances ; l'indice de dominance écarté")

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nFil des liquidations — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le fil des liquidations lit correctement son flux.\n")
