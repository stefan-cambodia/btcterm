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
from .arbitrage import ArbitrageEngine
from .newsdb import NewsCollector
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
    TTL_FUNDING = 900
    TTL_OPEN_INTEREST = 300
    TTL_PERP = 30

    #: Période de collecte des news, en secondes. Les flux RSS publient
    #: quelques articles par heure : un quart d'heure suffit largement.
    NEWS_INTERVAL = 900

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        min_profit_pct: float = 0.1,
        collect_news: bool = True,
        cryptopanic_key: str = "",
    ):
        self.symbol = symbol
        self.books: dict[str, OrderBook] = {
            name: OrderBook(exchange=name)
            for name in ("Binance", "Kraken", "Bybit", "OKX", "Coinbase")
        }
        self.engine = ArbitrageEngine(self.books, min_profit_pct=min_profit_pct)
        self.started_at = time.time()

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
        ]
        self._thread = run_connectors_in_thread(self._connectors)

        if self.collect_news:
            self.news.start()

    def stop(self) -> None:
        for connector in self._connectors:
            connector.stop()
        self.news.stop()

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
        `attrs["demo"]`, sur lequel `build_price_chart` pose un bandeau
        d'avertissement. Sans ce repli, un panneau prix vide et une trace
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
