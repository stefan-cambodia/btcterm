"""
Hub de données — une seule source pour tous les panneaux.

Sans lui, chaque panneau ouvrirait sa propre connexion : le carnet, le
graphique et le scan d'arbitrage réclament les mêmes données à la même
plateforme. Le hub ouvre **une** connexion par plateforme, entretient les
carnets en continu et met en cache les appels REST, puis sert des
instantanés à qui les demande.

Il tourne dans un thread démon avec sa propre boucle asyncio, de sorte
que l'interface — quelle qu'elle soit — garde le thread principal.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any, Callable, Optional

import pandas as pd

from . import sources
from .alerts import Alert, AlertEngine
from .arbitrage import ArbitrageEngine
from .journal import Journal
from .newsdb import NewsCollector
from .liquidations import (BybitLiquidationConnector, Liquidation,
                           LiquidationFeed)
from . import resolver
from .exchanges import (
    BinanceConnector,
    BybitConnector,
    CoinbaseAdvancedConnector,
    KrakenConnector,
    OKXConnector,
    OrderBook,
    run_connectors_in_thread,
)

__all__ = ["MarketHub", "TTLCache"]


log = logging.getLogger("btcterm.hub")

class TTLCache:
    """Cache mémoire à durée de vie, sûr entre threads.

    Plusieurs panneaux peuvent réclamer la même donnée dans la même
    seconde ; le premier paie l'appel réseau, les autres lisent le cache.
    En cas d'échec de rafraîchissement, la dernière valeur connue est
    conservée : un panneau qui affiche une donnée un peu datée vaut mieux
    qu'un panneau vide.

    Mais une valeur datée n'est pas une valeur fraîche, et une source en
    panne n'a pas à l'être en silence. Le journal de séance a montré six
    heures et demie sans financement ni open interest — Binance Futures
    muet après un réveil, pendant que CoinGecko répondait — sans une
    ligne dans le journal du service. Le cache tient donc, par clé, si
    la dernière lecture a été servie de secours (`stale`) — l'instantané
    journalisé n'écrit pas le secours —, et il journalise la première
    panne d'une source comme son rétablissement, avec la durée.
    """

    def __init__(self):
        self._entries: dict[str, tuple[float, Any]] = {}
        #: Clé → vrai si la dernière lecture a été servie de secours
        #: (source en panne), ou a échoué sans rien à servir. Une clé
        #: jamais lue n'y est pas : le cache ne disqualifie que ce qu'il
        #: a vu tomber.
        self._stale: dict[str, bool] = {}
        #: Clé → instant de la première panne encore en cours.
        self._failing: dict[str, float] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float, producer: Callable[[], Any]) -> Any:
        with self._lock:
            entry = self._entries.get(key)
        if entry and time.time() - entry[0] < ttl:
            return entry[1]

        try:
            value = producer()
        except Exception as exc:
            self._note_failure(key, exc, entry)
            with self._lock:
                self._stale[key] = True
            if entry:
                return entry[1]
            raise

        self._note_recovery(key)
        with self._lock:
            self._entries[key] = (time.time(), value)
            self._stale[key] = False
        return value

    def stale(self, key: str) -> bool:
        """Vrai si la dernière lecture de `key` a été servie de secours."""
        with self._lock:
            return self._stale.get(key, False)

    def _note_failure(self, key: str, exc: Exception, entry) -> None:
        with self._lock:
            first = key not in self._failing
            if first:
                self._failing[key] = time.time()
        if first:
            age = f"{time.time() - entry[0]:.0f} s" if entry else "aucune"
            log.warning("source %s en panne : %s — dernière valeur : %s",
                        key, sources.brief_error(exc), age)

    def _note_recovery(self, key: str) -> None:
        with self._lock:
            since = self._failing.pop(key, None)
        if since is not None:
            log.warning("source %s de nouveau servie après %.0f min",
                        key, (time.time() - since) / 60)

    def peek(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._entries.get(key)
        return entry[1] if entry else None


class MarketHub:
    """Carnets temps réel + données REST mises en cache."""

    #: Durées de vie des caches, en secondes.
    TTL_KLINES = 5
    #: Une page d'historique antérieur est close : ses bougies ne
    #: changeront plus jamais. Une heure de cache borne juste la mémoire
    #: quand on remonte loin, sans coût de fraîcheur.
    TTL_KLINES_HISTORY = 3600
    TTL_TICKER = 5
    TTL_EUR = 3600
    TTL_ETF = 1800
    TTL_FEAR_GREED = 900
    #: La masse monétaire est mensuelle et publiée avec deux mois de
    #: retard : la rafraîchir plus souvent qu'une fois par demi-journée
    #: ne peut rien apprendre.
    TTL_M2 = 21600
    #: Le financement tombe toutes les huit heures, l'open interest par
    #: tranches de quatre ; seul l'instantané mérite d'être frais.
    #: Les agrégats de marché bougent lentement et CoinGecko limite le
    #: débit de son offre gratuite : cinq minutes suffisent.
    TTL_GLOBAL = 300
    #: Le hashrate est une moyenne journalière et la difficulté ne change
    #: que tous les quinze jours : une heure de cache est généreuse.
    TTL_CHAIN = 3600
    TTL_CHAIN_STATS = 300
    TTL_FUNDING = 900
    TTL_OPEN_INTEREST = 300
    TTL_PERP = 30

    #: Période de collecte des news, en secondes. Les flux RSS publient
    #: quelques articles par heure : un quart d'heure suffit largement.
    NEWS_INTERVAL = 900

    #: Cadence des instantanés de marché journalisés (§ journal) : cinq
    #: minutes, alignées sur les TTL des sources (TTL_GLOBAL,
    #: TTL_OPEN_INTEREST) — plus vite n'apprendrait rien, chaque
    #: instantané relirait le même cache. Le premier attend une minute :
    #: au démarrage, les panneaux réchauffent déjà les mêmes caches.
    SNAPSHOT_EVERY = 300.0
    SNAPSHOT_WARMUP = 60.0
    #: Au-delà de cet écart entre deux instantanés, la séance a été
    #: interrompue — machine en veille, service arrêté — et les courbes
    #: journalisées s'y rompent au lieu de tirer un trait par-dessus.
    #: Trois cadences : un instantané manqué n'est pas une interruption.
    SNAPSHOT_GAP = 3 * SNAPSHOT_EVERY

    #: Profondeur de la fenêtre de liquidations relue du journal au
    #: démarrage : une heure, celle des totaux du panneau.
    WARM_UP_SECONDS = 3600.0

    #: Au démarrage, la boucle d'observation attend le réseau avant son
    #: premier tour — au plus une minute, sondé toutes les deux
    #: secondes. L'unité utilisateur part avant que la machine ne soit
    #: connectée (`network-online.target` n'engage rien dans un
    #: gestionnaire utilisateur), et le premier tour déclarait cinq
    #: sources en panne deux secondes après le boot. La sonde vise une
    #: adresse, pas un nom : le DNS n'est pas ce qu'on veut tester.
    NETWORK_WAIT = 60.0
    NETWORK_PROBE_EVERY = 2.0
    NETWORK_PROBE = ("1.1.1.1", 443)
    #: Le réveil de la machine rejoue le boot : la boucle dort sur une
    #: horloge monotone, qui ne court pas pendant le sommeil, et son
    #: premier tour partait à la seconde même du réveil — sept salves de
    #: « cinq sources en panne » en une journée, une par réveil. Un tour
    #: dont l'heure murale a sauté de plus de `RESUME_GAP` est un réveil,
    #: et la boucle rattend le réseau avant de reprendre.
    RESUME_GAP = 30.0
    #: Profondeur des sonneries relues au démarrage : la journée, celle
    #: que le panneau journal relit — la cloche, elle, compte l'heure.
    ALERTS_WARM_UP_SECONDS = 24 * 3600.0

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        min_profit_pct: float = 0.1,
        collect_news: bool = True,
        cryptopanic_key: str = "",
        keep_journal: bool = True,
    ):
        self.symbol = symbol
        self.books: dict[str, OrderBook] = {
            name: OrderBook(exchange=name)
            for name in ("Binance", "Kraken", "Bybit", "OKX", "Coinbase")
        }
        self.engine = ArbitrageEngine(self.books, min_profit_pct=min_profit_pct)
        #: Fil des liquidations forcées, toutes paires confondues : il
        #: partage le thread des connecteurs et ne garde qu'une fenêtre
        #: glissante en mémoire — relue du journal au démarrage
        #: (`_warm_liquidations`).
        self.liquidations = LiquidationFeed()
        self.started_at = time.time()

        #: Journal des données éphémères (liquidations, épisodes
        #: d'arbitrage). Sa base n'existe qu'à la première écriture :
        #: un hub jamais démarré — les tests — ne crée aucun fichier.
        self.journal: Optional[Journal] = Journal() if keep_journal else None
        if self.journal is not None:
            self.liquidations.on_event = self.journal.record_liquidation

        #: Moteur d'alertes : évalué par la boucle d'observation, réglé
        #: par le panneau ALERTES, journalisé quand le journal est tenu.
        self.alerts = AlertEngine(journal=self.journal)

        self._observe_stop = threading.Event()
        self._observe_thread: Optional[threading.Thread] = None
        #: Levé quand le hub s'arrête — ou, en régime service, dès que
        #: le signal d'arrêt arrive (terminal/wsgi.py), avant même que
        #: `stop` ne tourne. Les boucles longues qui vivent hors du hub
        #: (les WebSockets /push du pousseur) le lisent pour rendre la
        #: main : c'est ce qui permet au processus de sortir.
        self.stopping = threading.Event()

        # Le panneau news lisait une base que personne ne remplissait
        # dans le terminal ; le collecteur s'en charge en tâche de fond,
        # sans rendre le timer systemd du tracker obligatoire.
        self.collect_news = collect_news
        self.news = NewsCollector(
            interval=self.NEWS_INTERVAL, api_key=cryptopanic_key
        )

        self._cache = TTLCache()
        self._connectors: list = []
        self._thread: Optional[threading.Thread] = None

    # ── Cycle de vie ────────────────────────────────────────

    def start(self) -> None:
        """Ouvre les connexions temps réel. Idempotent."""
        if self._thread is not None:
            return
        # Avant toute connexion : si le résolveur du fournisseur d'accès
        # renvoie 127.0.0.1 pour Binance ou Bybit, la résolution de secours
        # par DNS sur HTTPS prend le relais (voir btcterm/resolver.py).
        resolver.install()
        # Avant les connecteurs : la fenêtre doit être remplie dans
        # l'ordre, et un événement vivant qui arriverait pendant la
        # relecture s'y retrouverait avant des plus anciens.
        self._warm_liquidations()
        self._warm_alerts()
        self._connectors = [
            BinanceConnector(self.books["Binance"], symbol="btcusdt", depth=20),
            KrakenConnector(self.books["Kraken"], pair="XBT/USDT", depth=25),
            BybitConnector(self.books["Bybit"], symbol="BTCUSDT", depth=50),
            OKXConnector(self.books["OKX"], inst_id="BTC-USDT"),
            CoinbaseAdvancedConnector(self.books["Coinbase"], product="BTC-USDT"),
            self.liquidations,
            # Seconde source du même fil : Binance tait ses flux futures
            # depuis certains pays, Bybit non.
            BybitLiquidationConnector(self.liquidations),
        ]
        self._thread = run_connectors_in_thread(self._connectors)

        if self.collect_news:
            self.news.start()

        self._observe_thread = threading.Thread(
            target=self._observe_loop, daemon=True, name="observe")
        self._observe_thread.start()

    def _warm_alerts(self) -> int:
        """Rend au moteur les sonneries de la journée, du journal.

        Le pendant de `_warm_liquidations` pour le panneau alertes et la
        cloche du bandeau : un service relancé — ou une machine qui se
        réveille et relance son service — affichait « aucune alerte »
        quand la nuit avait sonné dix fois. Même contrat : rien n'est
        réécrit, une lecture qui échoue laisse la mémoire vide.
        """
        if self.journal is None:
            return 0
        now = time.time()
        try:
            rows = self.journal.alerts_between(
                now - self.ALERTS_WARM_UP_SECONDS, now)
        except Exception:
            return 0
        return self.alerts.restore(
            Alert(time=row["ts"], kind=row["kind"], message=row["message"])
            for row in rows)

    def _warm_liquidations(self) -> int:
        """Rend au fil les liquidations de la dernière heure, du journal.

        La fenêtre glissante ne vit qu'en mémoire : sans cette relecture,
        un redémarrage du service laisse le panneau vide même après une
        cascade, ce qui se lit comme une panne du flux. L'heure relue est
        celle des totaux du panneau — au-delà, la fenêtre montrerait des
        montants que sa barre de titre ne compte pas.

        Une conséquence assumée : si une rafale est encore dans les cinq
        dernières minutes au redémarrage, l'alerte de rafale sonnera de
        nouveau — la condition est vraie, et un opérateur qui relance son
        terminal au milieu d'une cascade a plutôt intérêt à l'apprendre.

        Rend le nombre d'événements rendus, pour les tests. Une lecture
        qui échoue — base absente, verrouillée, schéma d'une version
        antérieure — laisse simplement la fenêtre vide : le fil se
        remplira des événements vivants.
        """
        if self.journal is None:
            return 0
        now = time.time()
        try:
            rows = self.journal.liquidations_between(
                now - self.WARM_UP_SECONDS, now)
        except Exception:
            return 0
        return self.liquidations.restore(
            Liquidation(
                time=row["ts"], symbol=row["symbol"], side=row["side"],
                price=row["price"], quantity=row["quantity"],
                # Les lignes d'avant la seconde source n'ont pas de
                # plateforme : elles ne pouvaient venir que de Binance.
                exchange=row["exchange"] or "Binance",
            )
            for row in rows
        )

    def _network_reachable(self) -> bool:
        try:
            socket.create_connection(self.NETWORK_PROBE, timeout=2).close()
            return True
        except OSError:
            return False

    def _wait_for_network(self, moment: str = "au démarrage") -> bool:
        """Attend le réseau, au plus `NETWORK_WAIT` ; vrai s'il est là.

        Journalise une fois l'attente et une fois sa fin — ou son
        expiration, auquel cas la boucle part quand même : ses sources
        diront elles-mêmes ce qui manque. `moment` dit d'où vient
        l'attente : le démarrage, ou un réveil.
        """
        if self._network_reachable():
            return True
        log.warning("réseau absent %s : la boucle d'observation attend",
                    moment)
        started = time.monotonic()
        while time.monotonic() - started < self.NETWORK_WAIT:
            if self._observe_stop.wait(self.NETWORK_PROBE_EVERY):
                return False
            if self._network_reachable():
                log.warning("réseau présent après %.0f s",
                            time.monotonic() - started)
                return True
        log.warning("réseau toujours absent après %.0f s : la boucle "
                    "d'observation part sans lui", self.NETWORK_WAIT)
        return False

    def _observe_loop(self) -> None:
        """Observe le marché pour le journal et les alertes, à 1 s.

        Aucun callback d'interface ne peut tenir ce rôle — il n'en
        tourne aucun sans navigateur ouvert, et le journal comme les
        alertes doivent couvrir la séance entière. Un balayage par
        seconde suffit à border des épisodes dont la tolérance est de
        30 s ; c'est aussi ce qui fait avancer le compteur « détectées »
        même terminal fermé.
        """
        if self.journal is not None:
            self.journal.purge()
        self._wait_for_network()
        next_snapshot = time.monotonic() + self.SNAPSHOT_WARMUP
        last_tick = time.time()
        while not self._observe_stop.wait(1.0):
            last_tick = self._after_sleep(last_tick)
            # Un tour raté — balayage, base verrouillée, disque — ne
            # doit pas arrêter d'observer : le suivant retentera.
            try:
                opportunities = self.engine.scan()
                if self.journal is not None:
                    self.journal.observe(opportunities)
            except Exception:
                opportunities = None
            try:
                self.alerts.evaluate(self, opportunities)
            except Exception:
                pass
            # L'instantané de marché, à cadence propre : c'est lui qui
            # construit l'historique que les API refusent de servir, et
            # il couvre la séance entière — navigateur ouvert ou non.
            if (self.journal is not None
                    and time.monotonic() >= next_snapshot):
                next_snapshot = time.monotonic() + self.SNAPSHOT_EVERY
                try:
                    self.record_market_snapshot()
                except Exception:
                    pass

    def _after_sleep(self, last_tick: float) -> float:
        """Rattend le réseau si la machine a dormi depuis `last_tick`.

        Rend l'heure murale du tour qui commence. `Event.wait` compte en
        temps monotone, que le sommeil n'avance pas : le tour qui suit
        un réveil part à la seconde même, avant que la machine ne soit
        reconnectée — et déclarait toutes les sources en panne.
        """
        now = time.time()
        slept = now - last_tick
        if slept > self.RESUME_GAP:
            self._wait_for_network(
                f"au réveil, après {slept / 60:.0f} min de sommeil")
            now = time.time()
        return now

    def stop(self) -> None:
        self.stopping.set()
        for connector in self._connectors:
            connector.stop()
        self.news.stop()
        self._observe_stop.set()
        if self._observe_thread is not None:
            self._observe_thread.join(timeout=3)
            self._observe_thread = None
        if self.journal is not None:
            # Les épisodes encore ouverts partent avec la séance.
            self.journal.flush()
            self.journal.close()

    @property
    def connected_count(self) -> int:
        return sum(1 for book in self.books.values() if book.connected)

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)

    # ── Marché ──────────────────────────────────────────────

    def klines(self, interval: str = "1d", limit: int = 350) -> pd.DataFrame:
        """Chandeliers, mutualisés entre panneaux via le cache.

        Repli sur une série de démonstration quand la source est
        injoignable et qu'aucune valeur n'a encore été mise en cache —
        typiquement un démarrage hors ligne. Le terminal s'ouvre alors
        quand même, et le graphique le dit : le DataFrame de démo porte
        `attrs["demo"]`, que `/api/klines` relaie au navigateur où
        lwc-price.js pose un bandeau d'avertissement. Sans ce repli, un
        panneau prix vide et une trace
        dans la console seraient tout ce que l'utilisateur obtiendrait.
        """
        try:
            return self._cache.get(
                f"klines:{interval}:{limit}",
                self.TTL_KLINES,
                lambda: sources.fetch_klines(self.symbol, interval, limit),
            )
        except Exception:
            return sources.generate_demo_ohlcv(limit, interval=interval, index=False)

    def klines_history(
        self, interval: str, end_ms: int, limit: int = 350
    ) -> pd.DataFrame:
        """Page d'historique antérieur : les `limit` bougies ouvertes au
        plus tard à `end_ms` (millisecondes epoch).

        C'est le lazy-loading du panneau prix qui appelle ici, au pan
        vers le passé. Pas de repli de démonstration : une page qui
        manque parce que la source est injoignable — ou parce que
        l'historique est épuisé — revient vide, et le client cesse
        simplement de demander plus ancien.
        """
        try:
            return self._cache.get(
                f"klines:{interval}:{limit}:{int(end_ms)}",
                self.TTL_KLINES_HISTORY,
                lambda: sources.fetch_klines(
                    self.symbol, interval, limit, end_time=int(end_ms)),
            )
        except Exception:
            return pd.DataFrame(columns=["time"] + sources.OHLCV_COLUMNS)

    def ticker(self) -> dict:
        return self._cache.get(
            "ticker", self.TTL_TICKER, lambda: sources.fetch_ticker_24h(self.symbol)
        )

    def eur_rate(self) -> float:
        return self._cache.get("eur", self.TTL_EUR, sources.fetch_eur_rate)

    def reference_price(self) -> Optional[float]:
        """Prix médian de la plateforme la plus fraîche encore connectée."""
        live = [b for b in self.books.values() if b.connected and b.mid]
        if not live:
            return None
        return min(live, key=lambda b: b.age_ms).mid

    # ── Contexte ────────────────────────────────────────────

    def etf_flows(self) -> pd.DataFrame:
        return self._cache.get("etf", self.TTL_ETF, sources.fetch_etf_flows)

    def m2_supply(self) -> pd.DataFrame:
        """Masse monétaire M2 des États-Unis, pour le panneau macro.

        Retourne un tableau vide plutôt que de lever si la source est
        injoignable et que rien n'est en cache : le panneau le dit, les
        autres continuent de vivre.
        """
        try:
            return self._cache.get("m2", self.TTL_M2, sources.fetch_m2_supply)
        except Exception:
            return pd.DataFrame(columns=["time", "m2"])

    def market_global(self) -> dict:
        """Capitalisation, dominance et volume — instantané, ou `{}`."""
        try:
            return self._cache.get(
                "global", self.TTL_GLOBAL, sources.fetch_market_global
            )
        except Exception:
            return {}

    def record_market_snapshot(self, now: Optional[float] = None) -> None:
        """Journalise l'instantané de marché du moment.

        Les deux sources échouent indépendamment : un instantané partiel
        — dominance sans open interest, ou l'inverse — s'écrit quand
        même, chaque colonne absente restant NULL. Rien ne s'écrit quand
        tout manque : hors ligne, le journal ne se remplit pas de vide.
        """
        if self.journal is None:
            return
        # Le cache sert sa dernière valeur quand la source tombe — bien
        # pour un panneau, faux pour un historique : une valeur de
        # secours écrite comme fraîche mentirait sur des heures. Chaque
        # source ne s'écrit que si sa dernière lecture était fraîche.
        agregats = self.market_global()
        if self._cache.stale("global"):
            agregats = {}
        oi = self.open_interest()
        if self._cache.stale("oi:4h:180"):
            oi = pd.DataFrame(columns=["time", "oi", "oi_usd"])
        perp = self.perp_snapshot()
        if self._cache.stale("perp"):
            perp = {}

        shares = agregats.get("shares") or {}
        fields = {
            "btc_dominance": shares.get("BTC"),
            "stable_share": (sum(part for nom, part in shares.items()
                                 if nom in sources.STABLES)
                             if shares else None),
            "total_cap_usd": agregats.get("total_cap_usd"),
            "total_volume_usd": agregats.get("total_volume_usd"),
            "oi_usd": float(oi["oi_usd"].iloc[-1]) if not oi.empty else None,
            #: Le taux de la période en cours — celui que la prochaine
            #: échéance réglera : rééchantillonné par tranches de 8 h,
            #: il reconstitue l'historique des règlements (§ funding_
            #: history_extended).
            "funding_rate": perp.get("funding_rate"),
        }
        if all(value is None for value in fields.values()):
            return
        self.journal.record_market_snapshot(
            time.time() if now is None else now, **fields)

    def market_snapshots(self, days: float = 400) -> pd.DataFrame:
        """L'historique journalisé des instantanés, en DataFrame.

        C'est la série que CoinGecko ne donne qu'en payant et que
        Binance tronque à trente jours : elle n'existe que par
        l'accumulation locale, et commence donc vide.

        La séance s'interrompt — la machine dort, le service s'arrête —
        et le journal le montre par un trou entre deux instantanés. Une
        ligne de rupture (toutes valeurs à NaN, `gap` vrai) est insérée
        juste après le dernier instantané avant chaque trou de plus de
        `SNAPSHOT_GAP` : une courbe qui la traverse se rompt au lieu de
        tirer un trait sur sept heures de sommeil.
        """
        columns = ["time", "btc_dominance", "stable_share",
                   "total_cap_usd", "total_volume_usd", "oi_usd",
                   "funding_rate", "gap"]
        if self.journal is None:
            return pd.DataFrame(columns=columns)
        end = time.time()
        rows = self.journal.snapshots_between(end - days * 86_400, end)
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame([dict(row) for row in rows])
        df["time"] = pd.to_datetime(df.pop("ts"), unit="s")
        df["gap"] = False
        holes = df.index[df["time"].diff().dt.total_seconds() > self.SNAPSHOT_GAP]
        if len(holes):
            breaks = pd.DataFrame({
                "time": df.loc[holes - 1, "time"].to_numpy()
                + pd.Timedelta(seconds=1),
                "gap": True})
            df = (pd.concat([df, breaks], ignore_index=True)
                  .sort_values("time", kind="stable").reset_index(drop=True))
        return df[columns]

    def market_interruptions(self, days: float = 1) -> list[tuple[float, float]]:
        """Les interruptions de séance sur `days` jours : (avant, après)."""
        if self.journal is None:
            return []
        end = time.time()
        return self.journal.interruptions_between(end - days * 86_400, end,
                                                  self.SNAPSHOT_GAP)

    def chain_chart(self, name: str = "hash-rate",
                    timespan: str = "1year") -> pd.DataFrame:
        """Série on-chain (hashrate, difficulté, mempool), ou tableau vide."""
        try:
            return self._cache.get(
                f"chain:{name}:{timespan}", self.TTL_CHAIN,
                lambda: sources.fetch_chain_chart(name, timespan),
            )
        except Exception:
            return pd.DataFrame(columns=["time", "value"])

    def chain_stats(self) -> dict:
        """Instantané du réseau : hashrate, difficulté, rythme des blocs."""
        try:
            return self._cache.get(
                "chain_stats", self.TTL_CHAIN_STATS, sources.fetch_chain_stats
            )
        except Exception:
            return {}

    def funding_history(self, limit: int = 90) -> pd.DataFrame:
        """Taux de financement du perpétuel, un point par 8 h.

        Comme les autres accès au marché à terme, retourne un tableau
        vide plutôt que de lever : le panneau le dit, le terminal vit.
        """
        try:
            return self._cache.get(
                f"funding:{limit}", self.TTL_FUNDING,
                lambda: sources.fetch_funding_history(self.symbol, limit),
            )
        except Exception:
            return pd.DataFrame(columns=["time", "rate"])

    def open_interest(self, period: str = "4h", limit: int = 180) -> pd.DataFrame:
        """Open interest du perpétuel — trente jours, Binance n'en garde pas plus."""
        try:
            return self._cache.get(
                f"oi:{period}:{limit}", self.TTL_OPEN_INTEREST,
                lambda: sources.fetch_open_interest(self.symbol, period, limit),
            )
        except Exception:
            return pd.DataFrame(columns=["time", "oi", "oi_usd"])

    def open_interest_extended(self) -> pd.DataFrame:
        """Open interest prolongé vers le passé par le journal local.

        Binance ne sert que trente jours ; au-delà, la série continue
        sur les instantanés journalisés (§ record_market_snapshot),
        rééchantillonnés au pas des données Binance (4 h) pour que la
        couture ne se voie pas. Sans historique local, la série est
        celle de Binance, telle quelle — le prolongement se gagne à
        l'usage, séance après séance.
        """
        base = self.open_interest()
        history = self.market_snapshots()
        if history.empty:
            return base
        history = history.dropna(subset=["oi_usd"])[["time", "oi_usd"]]
        if not base.empty:
            history = history[history["time"] < base["time"].iloc[0]]
        if history.empty:
            return base
        # Les tranches vides — la séance interrompue — restent à NaN :
        # la sérialisation en fait des points blancs, et la courbe se
        # rompt là où le journal n'a rien vu.
        resampled = (history.set_index("time")["oi_usd"]
                     .resample("4h").last().reset_index())
        return pd.concat([resampled, base], ignore_index=True)

    def funding_history_extended(self, limit: int = 90) -> pd.DataFrame:
        """Financement prolongé vers le passé par le journal local.

        Binance sert `limit` règlements (90 × 8 h = trente jours) ;
        au-delà, la série continue sur les instantanés journalisés, où
        voyage le taux de la période *en cours*. Rééchantillonnés par
        tranches de 8 h étiquetées à leur borne droite — la grille des
        règlements (00 h, 08 h, 16 h UTC) —, le dernier relevé de chaque
        tranche est l'estimation immédiatement antérieure au règlement :
        une reconstitution, pas le règlement exact, mais à la précision
        d'un relevé de cinq minutes l'écart est négligeable.
        """
        base = self.funding_history(limit=limit)
        history = self.market_snapshots()
        if history.empty:
            return base
        history = history.dropna(subset=["funding_rate"])[
            ["time", "funding_rate"]]
        if not base.empty:
            history = history[history["time"] < base["time"].iloc[0]]
        if history.empty:
            return base
        resampled = (history.set_index("time")["funding_rate"]
                     .resample("8h", label="right", closed="left")
                     .last().rename("rate").reset_index())
        if not base.empty:
            # L'étiquette droite peut retomber sur le premier règlement
            # de Binance : le règlement réel gagne.
            resampled = resampled[resampled["time"] < base["time"].iloc[0]]
        return pd.concat([resampled, base], ignore_index=True)

    def perp_snapshot(self) -> dict:
        """Prix marqué, financement courant et positionnement des comptes."""
        try:
            return self._cache.get(
                "perp", self.TTL_PERP,
                lambda: sources.fetch_perp_snapshot(self.symbol),
            )
        except Exception:
            return {}

    def fear_greed_history(self) -> list[dict]:
        """Historique de l'indice Fear & Greed, du plus ancien au plus récent.

        La série vaut mieux que le chiffre : 30 aujourd'hui ne dit pas la
        même chose selon qu'on vienne de 70 ou de 15. C'est aussi la
        seule requête que le terminal adresse à alternative.me — le badge
        du panneau news en lit le dernier point.

        Retourne une liste vide si la source est injoignable *et* que le
        cache n'a rien à resservir : le panneau le dit alors, plutôt que
        de tracer une courbe fausse.
        """
        try:
            return self._cache.get(
                "fear_greed_history", self.TTL_FEAR_GREED,
                sources.fetch_fear_greed_history,
            )
        except Exception:
            return []

    def fear_greed(self) -> Optional[dict]:
        """Indice du jour — dernier point de l'historique.

        Dériver plutôt que refaire un appel garantit que le chiffre du
        badge et la fin de la courbe racontent la même chose, même quand
        le cache resert une série vieille d'un quart d'heure.
        """
        history = self.fear_greed_history()
        if not history:
            return None
        return {"value": history[-1]["value"], "label": history[-1]["label"]}
