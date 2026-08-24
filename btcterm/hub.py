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

import threading
import time
from typing import Any, Callable, Optional

import pandas as pd

from . import sources
from .alerts import AlertEngine
from .arbitrage import ArbitrageEngine
from .journal import Journal
from .newsdb import NewsCollector
from .liquidations import LiquidationFeed
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


class TTLCache:
    """Cache mémoire à durée de vie, sûr entre threads.

    Plusieurs panneaux peuvent réclamer la même donnée dans la même
    seconde ; le premier paie l'appel réseau, les autres lisent le cache.
    En cas d'échec de rafraîchissement, la dernière valeur connue est
    conservée : un panneau qui affiche une donnée un peu datée vaut mieux
    qu'un panneau vide.
    """

    def __init__(self):
        self._entries: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float, producer: Callable[[], Any]) -> Any:
        with self._lock:
            entry = self._entries.get(key)
        if entry and time.time() - entry[0] < ttl:
            return entry[1]

        try:
            value = producer()
        except Exception:
            if entry:
                return entry[1]
            raise

        with self._lock:
            self._entries[key] = (time.time(), value)
        return value

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
        #: glissante en mémoire.
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
        self._connectors = [
            BinanceConnector(self.books["Binance"], symbol="btcusdt", depth=20),
            KrakenConnector(self.books["Kraken"], pair="XBT/USDT", depth=25),
            BybitConnector(self.books["Bybit"], symbol="BTCUSDT", depth=50),
            OKXConnector(self.books["OKX"], inst_id="BTC-USDT"),
            CoinbaseAdvancedConnector(self.books["Coinbase"], product="BTC-USDT"),
            self.liquidations,
        ]
        self._thread = run_connectors_in_thread(self._connectors)

        if self.collect_news:
            self.news.start()

        self._observe_thread = threading.Thread(
            target=self._observe_loop, daemon=True, name="observe")
        self._observe_thread.start()

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
        next_snapshot = time.monotonic() + self.SNAPSHOT_WARMUP
        while not self._observe_stop.wait(1.0):
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

    def stop(self) -> None:
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
        agregats = self.market_global()
        oi = self.open_interest()

        shares = agregats.get("shares") or {}
        fields = {
            "btc_dominance": shares.get("BTC"),
            "stable_share": (sum(part for nom, part in shares.items()
                                 if nom in sources.STABLES)
                             if shares else None),
            "total_cap_usd": agregats.get("total_cap_usd"),
            "total_volume_usd": agregats.get("total_volume_usd"),
            "oi_usd": float(oi["oi_usd"].iloc[-1]) if not oi.empty else None,
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
        """
        columns = ["time", "btc_dominance", "stable_share",
                   "total_cap_usd", "total_volume_usd", "oi_usd"]
        if self.journal is None:
            return pd.DataFrame(columns=columns)
        end = time.time()
        rows = self.journal.snapshots_between(end - days * 86_400, end)
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame([dict(row) for row in rows])
        df["time"] = pd.to_datetime(df.pop("ts"), unit="s")
        return df[columns]

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
        resampled = (history.set_index("time")["oi_usd"]
                     .resample("4h").last().dropna().reset_index())
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

    def fear_greed(self) -> Optional[dict]:
        return self._cache.get(
            "fear_greed", self.TTL_FEAR_GREED, sources.fetch_fear_greed
        )
