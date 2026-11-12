"""
Fil des liquidations forcées — Binance Futures, Bybit et OKX.

Quand une position à effet de levier ne couvre plus sa marge, la
plateforme la ferme au marché : c'est une liquidation. Elles arrivent par
rafales, et ces rafales expliquent une partie des mèches qu'on voit sur
le graphique du cours — d'où l'intérêt de les regarder à côté du carnet
plutôt que dans un onglet de navigateur.

Le flux `!forceOrder@arr` diffuse celles de **toutes** les paires, sans
clé. On les garde toutes : une cascade sur les altcoins précède souvent
celle du Bitcoin. Le côté est déduit du sens de l'ordre — une vente
forcée liquide un long, un achat forcé liquide un short.

Le flux est épisodique par nature : il peut rester silencieux plusieurs
minutes sans que rien n'aille mal.

Un lien peut s'ouvrir, s'abonner et ne rien recevoir sans qu'aucune
erreur ne le dise : c'est arrivé à Binance pendant des mois, le fil
écoutant une adresse que la scission de ses flux futures avait vidée
(voir `BINANCE_FUTURES_WS`). D'autres sources alimentent donc le
**même** magasin d'événements par des connecteurs à part : Bybit
(`BybitLiquidationConnector`), dont le canal `allLiquidation` est par
paire, sans joker — d'où une liste de paires plutôt que « toutes » —, et
OKX. Chaque événement dit d'où il vient (`exchange`), et le fil publie
l'état de chacun de ses liens — le panneau sait dire lequel manque, et
lequel tient sans rien livrer (`silent`) : un lien ouvert et muet ne se
distingue d'un marché calme que par la durée du silence.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .exchanges import ExchangeConnector

__all__ = ["Liquidation", "LiquidationFeed", "BybitLiquidationConnector",
           "OKXLiquidationConnector", "BYBIT_SYMBOLS", "MAP_BINS", "price_map",
           "is_bitcoin", "BITCOIN_SQL",
           "coin_contract_size"]

#: La route `/market` est obligatoire : depuis la scission des flux futures
#: de Binance (annoncée le 6 mars 2026, ancienne adresse retirée le
#: 23 avril), une connexion sans route — `/ws/` tout court — ne reçoit plus
#: que les flux « public » (carnets, meilleurs prix). Les liquidations sont
#: rangées sous `/market` : à l'ancienne adresse, le lien s'ouvrait,
#: s'abonnait et ne recevait plus rien.
BINANCE_FUTURES_WS = "wss://fstream.binance.com/market/ws/!forceOrder@arr"
BYBIT_LINEAR_WS = "wss://stream.bybit.com/v5/public/linear"
OKX_PUBLIC_WS = "wss://ws.okx.com/ws/v5/public"

#: Le bitcoin coté en dollars, sous les noms des trois plateformes —
#: `BTCUSDT` partout, `BTCUSD` pour l'inverse d'OKX —, échéances comprises
#: (`BTCUSDT_261225`). « Commence par BTC » laissait passer `BTCDOMUSDT`,
#: le contrat de Binance sur l'indice de dominance, qui cote quelques
#: milliers de points et n'est pas du bitcoin.
BITCOIN_SYMBOLS = frozenset({"BTCUSDT", "BTCUSDC", "BTCUSD"})

#: Les contrats coin-margined de Binance — « BTCUSD_PERP », « ETHUSD_261225 ».
#: Depuis le passage du flux à la route `/market`, Binance y diffuse aussi
#: leurs liquidations, et leur quantité se compte en **contrats** d'une valeur
#: fixe en dollars, pas en pièces : multipliée par le prix, elle faisait d'une
#: liquidation de 983 700 $ (9 837 contrats de 100 $) une de 703,6 M$, et du
#: 12 novembre 2026 une journée à 5,6 Md$ de longs liquidés.
_COIN_MARGINED = re.compile(r"([A-Z0-9]+)USD_(PERP|\d{6})")

#: La valeur d'un contrat coin-margined quand le catalogue manque : 100 $
#: pour le bitcoin, 10 $ pour les autres (catalogue COIN-M du 13 novembre
#: 2026 : trois contrats à 100 $, tous sur le bitcoin, vingt-sept à 10 $).
COIN_CONTRACT_BTC = 100.0
COIN_CONTRACT_OTHER = 10.0


def coin_contract_size(symbol: str,
                       sizes: Optional[dict[str, float]] = None
                       ) -> Optional[float]:
    """La valeur en dollars d'un contrat coin-margined, ou `None` pour un
    contrat linéaire (USDT, USDC), dont la quantité est déjà en pièces."""
    match = _COIN_MARGINED.fullmatch(symbol or "")
    if not match:
        return None
    if sizes and symbol in sizes:
        return sizes[symbol]
    return COIN_CONTRACT_BTC if match.group(1) == "BTC" else COIN_CONTRACT_OTHER


def is_bitcoin(symbol: str) -> bool:
    """Vrai pour un contrat sur le bitcoin coté en dollars."""
    return (symbol or "").split("_", 1)[0] in BITCOIN_SYMBOLS


_LISTE = "(" + ", ".join(f"'{s}'" for s in sorted(BITCOIN_SYMBOLS)) + ")"

#: La même règle, en SQL, pour les lectures du journal (§ journal) : le
#: nom du contrat, échéance retirée, est l'un des trois. Le texte sert
#: aussi de condition à l'index partiel des contrats bitcoin — SQLite ne
#: prend cet index que si la requête répète la condition mot pour mot.
BITCOIN_SQL = (f"(symbol IN {_LISTE} OR (instr(symbol, '_') > 0 AND "
               f"substr(symbol, 1, instr(symbol, '_') - 1) IN {_LISTE}))")

#: Paires suivies chez Bybit — le canal `allLiquidation` se souscrit par
#: paire. Les plus grosses capitalisations : c'est là que les cascades
#: se lisent, et une cascade sur les altcoins précède souvent celle du
#: Bitcoin.
BYBIT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                 "BNBUSDT", "ADAUSDT", "LINKUSDT", "SUIUSDT", "AVAXUSDT")


@dataclass(frozen=True)
class Liquidation:
    """Une position fermée de force."""

    time: float
    symbol: str
    side: str          #: `long` ou `short` — la position qui a sauté
    price: float
    quantity: float
    exchange: str = "Binance"   #: la plateforme qui a fermé la position

    @property
    def notional(self) -> float:
        """Taille de la position liquidée, en dollars."""
        return self.price * self.quantity


class LiquidationFeed(ExchangeConnector):
    """Fil temps réel, gardé en mémoire dans une fenêtre glissante.

    Le fil lui-même ne persiste rien : le panneau lit les dernières
    liquidations et leurs totaux, et ce qui sort de la fenêtre est
    oublié — l'indicateur de tension du moment. La persistance est
    l'affaire du rappel `on_event`, où le hub branche le journal
    (btcterm/journal.py) — et du chemin inverse, `restore`, par lequel
    le hub rend la fenêtre à un service qui redémarre.

    L'état de connexion est publié ici plutôt que dans un carnet — ce
    flux n'en alimente aucun — d'où la redéfinition des deux marqueurs.
    Il l'est par **lien** : le fil est lui-même le lien Binance, et les
    connecteurs secondaires (Bybit) déclarent le leur par `attach` et le
    tiennent à jour par `mark`. `connected` vaut dès qu'un lien tient,
    `error` rapporte le premier lien tombé.

    Un lien peut tenir sans rien livrer — Binance l'a fait des mois, à
    une adresse sans route —, et rien dans l'état de connexion ne le dit. Le fil
    retient donc, par lien, l'heure du dernier événement reçu et celle
    de l'ouverture du lien : `silent` nomme ceux qui tiennent sans avoir
    rien livré depuis plus longtemps qu'un seuil.
    """

    name = "Liquidations"

    #: Nombre d'événements conservés. À raison de quelques dizaines par
    #: minute en marché agité, cela couvre le dernier quart d'heure.
    MAX_EVENTS = 500

    def __init__(self, maxlen: int = MAX_EVENTS, **kwargs):
        super().__init__(book=None, **kwargs)
        self.events: deque[Liquidation] = deque(maxlen=maxlen)
        #: État par lien : nom → (connecté, dernière erreur).
        self.links: dict[str, tuple[bool, Optional[str]]] = {
            "Binance": (False, None)}
        #: Heure du dernier événement reçu, par lien — nourrie par
        #: `record` comme par `restore` : un événement relu du journal
        #: dit aussi quand la plateforme a parlé pour la dernière fois.
        self.last_seen: dict[str, float] = {}
        #: Moment où chaque lien s'est ouvert, pour ceux qui tiennent :
        #: le silence d'un lien se compte depuis son ouverture quand il
        #: n'a encore rien livré, jamais depuis un événement d'avant.
        self.since: dict[str, float] = {}
        self._lock = threading.Lock()
        #: Compteur des changements que le panneau doit montrer sans
        #: attendre — un événement retenu, un lien qui tombe ou se rouvre.
        #: Le pousseur ne rebâtit la table que s'il a bougé, ou une fois par
        #: seconde pour ce que seul le temps change (totaux de l'heure,
        #: silences) : la rebâtir dix fois par seconde valait près de la
        #: moitié d'une trame.
        self.version = 0
        #: Rappel appelé à chaque événement retenu — même convention que
        #: les collectes de newsdb : c'est l'appelant qui décide quoi en
        #: faire (le hub y branche le journal). Une erreur du rappel ne
        #: doit jamais fermer le flux.
        self.on_event: Optional[Callable[[Liquidation], None]] = None
        #: Symboles de Binance qui ne sont pas des cryptomonnaies — actions,
        #: matières premières —, lus à son catalogue (§ sources.
        #: fetch_binance_tradfi_symbols) et écartés du fil.
        self.excluded: frozenset[str] = frozenset()
        #: La valeur en dollars des contrats coin-margined, lue au catalogue
        #: COIN-M (§ coin_contract_size) ; vide, la règle de repli sert.
        self.coin_sizes: dict[str, float] = {}
        self._catalog_due = 0.0
        self._catalog_task = None

    # ── État de connexion ───────────────────────────────────

    def attach(self, name: str) -> None:
        """Déclare un lien secondaire, pas encore connecté."""
        self.links.setdefault(name, (False, None))

    def mark(self, name: str, connected: bool,
             error: Optional[str] = None) -> None:
        """Met à jour l'état d'un lien, et date son ouverture."""
        was_up = self.links.get(name, (False, None))[0]
        self.links[name] = (connected, None if connected else error)
        self.version += 1
        if connected and not was_up:
            self.since[name] = time.time()
        elif not connected:
            self.since.pop(name, None)

    @property
    def connected(self) -> bool:
        """Vrai dès qu'un lien tient."""
        return any(up for up, _ in self.links.values())

    @property
    def error(self) -> Optional[str]:
        """L'erreur du premier lien tombé, s'il y en a une."""
        for up, err in self.links.values():
            if not up and err:
                return err
        return None

    def missing(self) -> list[str]:
        """Les liens qui ne tiennent pas, dans l'ordre de déclaration."""
        return [name for name, (up, _) in self.links.items() if not up]

    def silent(self, threshold: float) -> list[tuple[str, float]]:
        """Les liens qui tiennent sans rien livrer depuis `threshold` secondes.

        Chaque entrée dit le lien et la durée de son silence, comptée
        depuis son dernier événement ou, s'il est plus récent, depuis
        l'ouverture du lien : un lien qui vient de se rouvrir n'est pas
        muet, même si son dernier événement date. Dans l'ordre de
        déclaration.
        """
        muets = []
        for name, (up, _) in self.links.items():
            if not up:
                continue
            age = self.last_event_age(name)
            if age is not None and age >= threshold:
                muets.append((name, age))
        return muets

    def _mark_connected(self) -> None:
        self.mark("Binance", True)

    def _mark_disconnected(self, exc: Exception) -> None:
        self.mark("Binance", False, str(exc)[:50])

    # ── Lecture ─────────────────────────────────────────────

    def latest(self, limit: int = 12) -> list[Liquidation]:
        """Les dernières liquidations, la plus récente d'abord."""
        with self._lock:
            return list(self.events)[-limit:][::-1]

    def totals(self, window: float = 3600) -> dict[str, float]:
        """Montants liquidés sur la fenêtre, par côté et pour le Bitcoin.

        `btc` ne compte que les contrats sur le bitcoin (`is_bitcoin`) :
        c'est ce qui distingue une cascade locale d'une cascade de marché.
        """
        limite = time.time() - window
        longs = shorts = btc = 0.0
        with self._lock:
            evenements = [e for e in self.events if e.time >= limite]

        for event in evenements:
            if event.side == "long":
                longs += event.notional
            else:
                shorts += event.notional
            if is_bitcoin(event.symbol):
                btc += event.notional

        return {"long": longs, "short": shorts, "btc": btc,
                "count": len(evenements)}

    def last_event_age(self, link: Optional[str] = None) -> Optional[float]:
        """Secondes écoulées depuis la dernière liquidation reçue.

        Sans argument, pour le fil entier — la fenêtre d'événements,
        quelle qu'en soit la source. Avec un nom de lien, pour ce lien
        seul : depuis son dernier événement ou, s'il est plus récent,
        depuis son ouverture ; `None` tant qu'il n'a ni parlé ni ouvert.
        """
        now = time.time()
        if link is not None:
            repere = max(filter(None, (self.last_seen.get(link),
                                       self.since.get(link))), default=None)
            return None if repere is None else max(0.0, now - repere)
        with self._lock:
            if not self.events:
                return None
            return now - self.events[-1].time

    def _seen(self, event: Liquidation) -> None:
        """Date le dernier événement du lien dont il vient."""
        if event.time > self.last_seen.get(event.exchange, 0.0):
            self.last_seen[event.exchange] = event.time

    # ── Flux ────────────────────────────────────────────────

    #: Période de relecture du catalogue de Binance, en secondes : après un
    #: succès, et après un échec — le catalogue se lit mal depuis certains
    #: réseaux, et le fil ne l'attend pas.
    CATALOG_EVERY = 6 * 3600.0
    CATALOG_RETRY = 600.0

    async def _load_excluded(self) -> None:
        """Lit les contrats hors cryptomonnaies de Binance, et la valeur des
        contrats coin-margined, sans jamais retenir le fil : un catalogue
        illisible laisse la liste d'avant — et, pour les contrats, la règle
        de repli (§ coin_contract_size)."""
        import asyncio

        from . import sources
        try:
            self.excluded = await asyncio.to_thread(
                sources.fetch_binance_tradfi_symbols)
            self._catalog_due = time.time() + self.CATALOG_EVERY
        except Exception:
            self._catalog_due = time.time() + self.CATALOG_RETRY
        try:
            self.coin_sizes = await asyncio.to_thread(
                sources.fetch_binance_coin_contracts)
        except Exception:  # noqa: BLE001 — la règle de repli suffit
            pass

    async def _stream(self) -> None:
        import asyncio

        async with self._connect(BINANCE_FUTURES_WS) as socket:
            self._mark_connected()

            def refresh() -> None:
                # En tâche de fond : la lecture peut durer vingt secondes,
                # le flux n'attend pas. Dès la connexion, pour que les
                # premières liquidations soient déjà triées.
                if time.time() >= self._catalog_due:
                    self._catalog_due = time.time() + self.CATALOG_RETRY
                    self._catalog_task = asyncio.create_task(
                        self._load_excluded())

            refresh()
            async for raw in socket:
                refresh()
                message = json.loads(raw)
                # Binance documente un objet par événement, mais le nom
                # du flux (`@arr`) laisse attendre un tableau : les deux
                # formes sont acceptées plutôt que pariées.
                for event in message if isinstance(message, list) else [message]:
                    self._handle(event)

    def _handle(self, message: dict) -> None:
        order = message.get("o")
        if not order:
            return
        try:
            quantity = float(order.get("q", 0))
            # `ap` est le prix moyen d'exécution, `p` le prix limite de
            # l'ordre de liquidation : le premier est le vrai prix payé.
            price = float(order.get("ap") or order.get("p") or 0)
            if order.get("s") in self.excluded:
                return          # action ou matière première : hors du fil
            # Un contrat coin-margined se compte en contrats d'une valeur
            # fixe en dollars : la quantité repasse en pièces, pour que
            # prix × quantité dise le montant, ici comme au journal.
            size = coin_contract_size(order.get("s", ""), self.coin_sizes)
            if size is not None and price > 0:
                quantity = quantity * size / price
            event = Liquidation(
                time=float(order.get("T", message.get("E", 0))) / 1000,
                symbol=order.get("s", "?"),
                # Une vente forcée ferme un long, un achat forcé un short.
                side="long" if order.get("S") == "SELL" else "short",
                price=price,
                quantity=quantity,
            )
        except (TypeError, ValueError):
            return

        self.record(event)

    def record(self, event: Liquidation) -> None:
        """Retient un événement, d'où qu'il vienne, et le signale.

        C'est l'entrée commune des sources : le fil lui-même pour Binance,
        `BybitLiquidationConnector` pour Bybit. Un événement sans prix ou
        sans taille est ignoré.
        """
        if event.price <= 0 or event.quantity <= 0:
            return
        with self._lock:
            self.events.append(event)
            self._seen(event)
            self.version += 1
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                # Le journal peut échouer (disque plein, base verrouillée) ;
                # le fil, lui, doit continuer à nourrir le panneau.
                pass

    def restore(self, events: Iterable[Liquidation]) -> int:
        """Repeuple la fenêtre d'événements **déjà connus**, sans les resignaler.

        La fenêtre ne vit qu'en mémoire : redémarrer le service la vide,
        et le panneau repartait de zéro alors que le journal gardait les
        dernières heures sur disque — un panneau vide après un
        redémarrage se lit comme une panne, ce qu'il n'est pas.

        Contrairement à `record`, rien n'est signalé à `on_event` : ces
        événements viennent du journal, les réémettre les y écrirait une
        seconde fois. À appeler **avant** d'ouvrir les connexions, les
        événements devant rester en ordre chronologique dans la
        fenêtre ; les plus anciens sortent d'eux-mêmes si l'on en donne
        plus qu'elle n'en tient.
        """
        kept = 0
        with self._lock:
            for event in events:
                if event.price <= 0 or event.quantity <= 0:
                    continue
                self.events.append(event)
                self._seen(event)
                kept += 1
            self.version += 1
        return kept


class BybitLiquidationConnector(ExchangeConnector):
    """Seconde source du fil : le canal `allLiquidation` de Bybit.

    Un abonnement par paire, sur le WebSocket public des contrats
    linéaires ; chaque message porte un tableau d'événements. Le champ
    `S` y est le **côté de la position** liquidée — `Buy` pour un long,
    `Sell` pour un short —, à l'inverse de Binance qui donne le sens de
    l'ordre forcé. Les événements vont au magasin du fil (`record`), et
    l'état du lien au fil aussi (`mark`).
    """

    name = "Bybit"

    def __init__(self, feed: LiquidationFeed,
                 symbols: tuple[str, ...] = BYBIT_SYMBOLS, **kwargs):
        super().__init__(book=None, **kwargs)
        self.feed = feed
        self.feed.attach(self.name)
        self.subscription = {
            "op": "subscribe",
            "args": [f"allLiquidation.{symbol}" for symbol in symbols],
        }

    def _mark_connected(self) -> None:
        self.feed.mark(self.name, True)

    def _mark_disconnected(self, exc: Exception) -> None:
        self.feed.mark(self.name, False, str(exc)[:50])

    async def _stream(self) -> None:
        async with self._connect(BYBIT_LINEAR_WS) as socket:
            await socket.send(json.dumps(self.subscription))
            self._mark_connected()
            async for raw in socket:
                if not self._running:
                    break
                self._handle(json.loads(raw))

    def _handle(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        # Réponse à l'abonnement : un refus (paire inconnue…) vaut panne,
        # pour qu'il se lise dans le panneau plutôt que dans un silence.
        if "success" in message:
            if not message.get("success"):
                raise RuntimeError(message.get("ret_msg") or "abonnement refusé")
            return
        if not str(message.get("topic", "")).startswith("allLiquidation."):
            return
        for item in message.get("data") or []:
            try:
                event = Liquidation(
                    time=float(item.get("T", message.get("ts", 0))) / 1000,
                    symbol=item.get("s", "?"),
                    side="long" if item.get("S") == "Buy" else "short",
                    price=float(item.get("p") or 0),
                    quantity=float(item.get("v") or 0),
                    exchange=self.name,
                )
            except (TypeError, ValueError, AttributeError):
                continue
            self.feed.record(event)


class OKXLiquidationConnector(ExchangeConnector):
    """Troisième source du fil : le canal `liquidation-orders` d'OKX.

    Tous les contrats perpétuels sur cryptomonnaies d'un seul abonnement,
    sans liste de paires — ceux sur actions et matières premières, près de
    la moitié du catalogue, sont écartés (§ sources.
    fetch_okx_swap_contracts). Bybit, sur dix paires, peut se taire une
    heure : le 7 novembre 2026 au matin, OKX a livré dix liquidations en
    trois minutes pendant que Bybit n'en livrait aucune — et que Binance,
    écouté à une adresse sans route, ne livrait plus rien depuis des mois.

    OKX compte en contrats : un contrat linéaire vaut `ctVal` unités du
    sous-jacent (0,01 BTC pour BTC-USDT), un inverse `ctVal` dollars (100
    pour BTC-USD) — la table des contrats est lue à la connexion, et relue
    au plus une fois par heure si un contrat inconnu se présente. Le côté
    vient de `posSide` (la position liquidée) ou, en mode net, du sens de
    l'ordre forcé. OKX coupe une connexion muette depuis trente secondes :
    le connecteur lui envoie son « ping » toutes les vingt.

    À l'abonnement, OKX renvoie des liquidations anciennes — de la veille au
    soir, au premier essai. Tout ce qui précède l'ouverture du lien est
    écarté : sans cela, chaque reconnexion les reverserait au journal.
    """

    name = "OKX"
    PING_EVERY = 20.0
    CONTRACTS_RETRY = 3600.0

    def __init__(self, feed: LiquidationFeed, **kwargs):
        super().__init__(book=None, **kwargs)
        self.feed = feed
        self.feed.attach(self.name)
        self.contracts: dict[str, tuple[float, str]] = {}
        self._contracts_at = 0.0
        #: Instant d'ouverture du lien, moins une marge d'horloge : ce qui
        #: le précède est un rejeu, pas une liquidation qui arrive.
        self.since = 0.0

    def _mark_connected(self) -> None:
        self.feed.mark(self.name, True)

    def _mark_disconnected(self, exc: Exception) -> None:
        self.feed.mark(self.name, False, str(exc)[:50])

    async def _load_contracts(self) -> None:
        import asyncio

        from . import sources
        self._contracts_at = time.time()
        self.contracts = await asyncio.to_thread(sources.fetch_okx_swap_contracts)

    async def _stream(self) -> None:
        import asyncio

        if not self.contracts:
            await self._load_contracts()
        async with self._connect(OKX_PUBLIC_WS) as socket:
            self.since = time.time() - 5.0
            await socket.send(json.dumps({"op": "subscribe", "args": [
                {"channel": "liquidation-orders", "instType": "SWAP"}]}))
            self._mark_connected()

            async def keepalive():
                while self._running:
                    await asyncio.sleep(self.PING_EVERY)
                    await socket.send("ping")

            pinger = asyncio.create_task(keepalive())
            try:
                async for raw in socket:
                    if not self._running:
                        break
                    if raw == "pong":
                        continue
                    unknown = self.handle(json.loads(raw))
                    if (unknown and time.time() - self._contracts_at
                            > self.CONTRACTS_RETRY):
                        await self._load_contracts()
            finally:
                pinger.cancel()

    def handle(self, message: dict) -> bool:
        """Verse les liquidations au fil ; vrai si un contrat est inconnu."""
        if message.get("event") == "error":
            raise RuntimeError(message.get("msg") or "abonnement refusé")
        unknown = False
        for item in message.get("data") or []:
            inst = item.get("instId", "")
            if inst not in self.contracts:
                unknown = True
                continue
            contract = self.contracts[inst]
            if contract is None:
                continue        # action ou matière première : hors du fil
            ct_val, ct_ccy = contract
            symbol = inst.replace("-SWAP", "").replace("-", "")
            for detail in item.get("details") or []:
                try:
                    moment = float(detail["ts"]) / 1000
                    if moment < self.since:
                        continue
                    price = float(detail["bkPx"])
                    size = float(detail["sz"]) * ct_val
                    quantity = size / price if ct_ccy == "USD" else size
                    side = detail.get("posSide") or (
                        "long" if detail.get("side") == "sell" else "short")
                    if side not in ("long", "short") or price <= 0:
                        continue
                    self.feed.record(Liquidation(
                        time=moment, symbol=symbol,
                        side=side, price=price, quantity=quantity,
                        exchange=self.name))
                except (KeyError, TypeError, ValueError, ZeroDivisionError):
                    continue
        return unknown


# ─────────────────────────────────────────────────────────────
# La carte des liquidations : où, en prix, le levier a sauté
# ─────────────────────────────────────────────────────────────

#: Tranches de prix de la carte, comme le profil de volume.
MAP_BINS = 60


def price_map(events: Iterable, bins: int = MAP_BINS) -> Optional[dict]:
    """Le notionnel liquidé par tranche de prix, longs et shorts à part.

    Les événements sont des lignes du journal (`symbol`, `side`, `price`,
    `notional`, `exchange`, `ts`). Les tranches couvrent la plage des prix
    liquidés, en `bins` parts égales. Une tranche chargée dit où des
    positions à levier ont été fermées de force — des ventes forcées pour
    les longs, des achats forcés pour les shorts : les zones où le marché a
    déjà purgé son levier, et celles où il l'a fait d'un seul côté.

    Seuls comptent les contrats sur le bitcoin (`is_bitcoin`), de toutes
    les plateformes : leurs prix sont ceux de l'axe du graphique.

    C'est la règle de référence, sur des événements en mémoire ; le
    journal la calcule lui-même, en SQL (`Journal.liquidation_map`), et un
    test vérifie que les deux disent la même chose.

    Rend `None` sans aucun événement de la paire ; sinon `centers`, `width`
    (largeur d'une tranche), `longs`, `shorts` (notionnel en dollars),
    `count`, `notional`, `exchanges` (les plateformes présentes — la
    couverture se dit) et `first`, `last` (bornes des événements, epoch).
    """
    rows = [(float(e["price"]), float(e["notional"]), e["side"],
             e["exchange"] or "Binance", float(e["ts"]))
            for e in events if is_bitcoin(e["symbol"])]
    if not rows:
        return None
    prices = [row[0] for row in rows]
    low, high = min(prices), max(prices)
    if high <= low:
        high = low * 1.001 or 1.0
    width = (high - low) / bins
    longs = [0.0] * bins
    shorts = [0.0] * bins
    for price, notional, side, _, _ in rows:
        index = min(bins - 1, int((price - low) / width))
        (longs if side == "long" else shorts)[index] += notional
    return {
        "centers": [low + (i + 0.5) * width for i in range(bins)],
        "width": width,
        "longs": longs,
        "shorts": shorts,
        "count": len(rows),
        "notional": sum(row[1] for row in rows),
        "exchanges": sorted({row[3] for row in rows}),
        "first": min(row[4] for row in rows),
        "last": max(row[4] for row in rows),
    }
