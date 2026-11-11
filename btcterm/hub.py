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

import datetime as dt
import logging
import socket
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import pandas as pd

from . import brief
from . import cycle
from . import markets as mkt
from . import options as opt
from . import sources
from .indicators import volatility
from .alerts import Alert, AlertEngine
from .arbitrage import ArbitrageEngine
from .analysis import monthly_returns, rule_stats, signal_stats
from .history import KlineHistory
from .journal import SNAPSHOT_FIELDS, Journal
from .newsdb import NewsCollector
from .tape import (BinanceTradeConnector, BybitTradeConnector,
                   CoinbaseTradeConnector, Trade, TradeTape)
from .liquidations import (BybitLiquidationConnector, Liquidation,
                           LiquidationFeed, OKXLiquidationConnector)
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

__all__ = ["MarketHub", "SleepWatch", "SourceBackoff", "TTLCache"]


log = logging.getLogger("btcterm.hub")

#: Un échec plus vieux que ce délai ne confirme plus la panne : le bandeau
#: cesse de la dire, et son rétablissement ne s'en attribue pas la durée.
FAILING_WINDOW = 900


def _asleep_seconds() -> float:
    """Le temps que la machine a passé endormie depuis son boot.

    Sous Linux, `CLOCK_BOOTTIME` court pendant le sommeil et l'horloge
    monotone non : leur écart est le sommeil cumulé, exact et sourd aux
    réglages de l'heure. Ailleurs, l'heure murale contre l'horloge
    monotone en tient lieu, à un réglage NTP près.
    """
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()
    return time.time() - time.monotonic()


def _span(seconds: float) -> str:
    """Une durée de panne, à la minute."""
    return "moins d'une minute" if seconds < 60 else f"{seconds / 60:.0f} min"


class SourceBackoff(ConnectionError):
    """Une source qu'on ménage après un échec : elle n'a pas été appelée.

    Le cache sert sa dernière valeur sans compter de panne : la lecture
    qui a échoué l'a déjà dite. Une seule expiration de délai chez
    Binance écrivait sinon onze lignes au journal du service — une par
    intervalle lu pendant l'attente, chacune « en panne » puis « de
    nouveau servie » — pour un seul échec réel.
    """


class SleepWatch:
    """Les réveils de la machine, lus sur ses horloges.

    Le sommeil se voyait à l'heure murale qui saute entre deux tours de
    la boucle d'observation, au-delà de trente secondes. Mais le journal
    du service a montré un sommeil de onze secondes suivi de trois
    sources en panne — l'instantané de marché parti avant le réseau — et
    les lectures des autres threads, onglet qui se réveille ou pousseur,
    échouaient avant même que la boucle n'ait vu le réveil. Toute lecture
    peut donc échantillonner la montre, et le premier qui voit le sommeil
    en date le réveil.
    """

    #: En deçà, l'écart des horloges est du bruit de lecture.
    THRESHOLD = 1.0

    def __init__(self, asleep: Callable[[], float] = _asleep_seconds):
        self._asleep = asleep
        self._lock = threading.Lock()
        self._seen = asleep()
        #: Instant, en temps monotone, du dernier réveil vu.
        self.woke_at: Optional[float] = None
        #: Durée du dernier sommeil, en secondes.
        self.slept = 0.0

    def sample(self) -> None:
        with self._lock:
            asleep = self._asleep()
            if asleep - self._seen >= self.THRESHOLD:
                self.woke_at = time.monotonic()
                self.slept = asleep - self._seen
            self._seen = asleep

    def woke_within(self, seconds: float) -> bool:
        """Vrai si la machine s'est réveillée il y a moins de `seconds`."""
        self.sample()
        return (self.woke_at is not None
                and time.monotonic() - self.woke_at < seconds)


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

    Un échec dans les `grace` secondes d'un réveil de la machine ne dit
    rien de sa source : le réseau n'est pas encore revenu. Il est servi
    de secours, mais ne se journalise pas et ne compte pas comme panne —
    il reliait sinon la panne d'avant le sommeil à son retour, et le
    journal disait « après 95 min » de quatre-vingt-quatre minutes de
    sommeil.
    """

    def __init__(self, watch: Optional[SleepWatch] = None,
                 grace: float = 60.0):
        self._watch = watch
        self._grace = grace
        self._entries: dict[str, tuple[float, Any]] = {}
        #: Clé → vrai si la dernière lecture a été servie de secours
        #: (source en panne), ou a échoué sans rien à servir. Une clé
        #: jamais lue n'y est pas : le cache ne disqualifie que ce qu'il
        #: a vu tomber.
        self._stale: dict[str, bool] = {}
        #: Clé → instant de la première panne encore en cours.
        self._failing: dict[str, float] = {}
        #: Clé → instant du dernier échec : une clé qu'on ne relit plus,
        #: une page d'historique par exemple, resterait sinon en panne.
        self._failed_at: dict[str, float] = {}
        #: Clé → instant de la dernière lecture, et durée de vie demandée :
        #: ce qui permet d'oublier ce que personne ne lit plus (`sweep`).
        self._read_at: dict[str, float] = {}
        self._ttl: dict[str, float] = {}
        #: Clé → la dernière erreur, en une ligne : le panneau SOURCES la
        #: montre, le journal du service ne disant que la première.
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float, producer: Callable[[], Any]) -> Any:
        with self._lock:
            entry = self._entries.get(key)
            self._read_at[key] = time.time()
            self._ttl[key] = ttl
        if entry and time.time() - entry[0] < ttl:
            return entry[1]

        try:
            value = producer()
        except Exception as exc:
            if not isinstance(exc, SourceBackoff) and not self.waking():
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

    def failing(self, within: float) -> dict[str, float]:
        """Les pannes en cours, vues il y a moins de `within` secondes,
        avec leur instant de début."""
        horizon = time.time() - within
        with self._lock:
            return {key: since for key, since in self._failing.items()
                    if self._failed_at.get(key, 0.0) >= horizon}

    def waking(self) -> bool:
        """Vrai dans les `grace` secondes d'un réveil de la machine."""
        return self._watch is not None and self._watch.woke_within(self._grace)

    def _note_failure(self, key: str, exc: Exception, entry) -> None:
        """La première panne d'une source, dite une fois.

        Un échec qui suit un silence de plus de `FAILING_WINDOW` ne
        prolonge pas la panne d'avant : on ignore ce qu'elle est devenue
        pendant que personne ne lisait. Il en ouvre une nouvelle — le
        pendant, à l'échec, de ce que `_note_recovery` fait au retour.
        """
        now = time.time()
        with self._lock:
            self._errors[key] = sources.brief_error(exc)
            since = self._failing.get(key)
            last = self._failed_at.get(key, since)
            lapsed = since is not None and now - last > FAILING_WINDOW
            first = since is None or lapsed
            if first:
                self._failing[key] = now
            self._failed_at[key] = now
        if not first:
            return
        age = f"{now - entry[0]:.0f} s" if entry else "aucune"
        if not lapsed:
            log.warning("source %s en panne : %s — dernière valeur : %s",
                        key, sources.brief_error(exc), age)
            return
        log.warning("source %s en panne à sa première lecture depuis %.0f min : "
                    "%s — dernière valeur : %s (panne précédente constatée : "
                    "%s)", key, (now - last) / 60, sources.brief_error(exc),
                    age, _span(last - since))

    def _note_recovery(self, key: str) -> None:
        """Le retour d'une source, avec la durée de panne qu'on a vue.

        Une source que personne n'a relue depuis son dernier échec — le
        panneau qui la lit était replié, ou la machine dormait — n'a pas été
        en panne tout ce temps : on ne sait d'elle que l'intervalle entre ses
        échecs. Le journal du service a affiché « après 1915 min » pour un
        seul échec suivi de trente-deux heures sans lecture.
        """
        now = time.time()
        with self._lock:
            since = self._failing.pop(key, None)
            last = self._failed_at.get(key, since)
        if since is None:
            return
        if now - last <= FAILING_WINDOW:
            log.warning("source %s de nouveau servie après %.0f min",
                        key, (now - since) / 60)
            return
        log.warning("source %s de nouveau servie à sa première lecture depuis "
                    "%.0f min (panne constatée : %s)", key, (now - last) / 60,
                    _span(last - since))

    def peek(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._entries.get(key)
            if entry:
                self._read_at[key] = time.time()
        return entry[1] if entry else None

    def sweep(self, idle: float) -> int:
        """Oublie ce que personne n'a lu depuis `idle` secondes et dont la
        durée de vie est passée ; rend le nombre de clés oubliées.

        Une page d'historique est une clé à usage unique
        (`klines:15m:500:<fin>`) : sans ce ménage, chaque pan restait en
        mémoire jusqu'au redémarrage — le journal du service en a montré
        une « dernière valeur » de dix-neuf heures. Ce qu'on lit encore
        reste, même périmé : c'est le secours d'une source en panne. L'état
        de panne, lui, reste aussi, pour que le retour de la source se dise
        au journal.
        """
        now = time.time()
        with self._lock:
            gone = [key for key, read in self._read_at.items()
                    if now - read > idle
                    and now - self._entries.get(key, (0.0,))[0]
                    > self._ttl.get(key, 0.0)]
            for key in gone:
                for table in (self._entries, self._stale, self._read_at,
                              self._ttl):
                    table.pop(key, None)
        return len(gone)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def status(self, within: float = FAILING_WINDOW) -> dict[str, dict]:
        """L'état de chaque clé lue : dernière valeur fraîche, secours,
        panne en cours (vue il y a moins de `within`) et dernière erreur."""
        horizon = time.time() - within
        with self._lock:
            keys = set(self._read_at) | set(self._entries) | set(self._failing)
            return {key: {
                "fresh_at": (self._entries[key][0]
                             if key in self._entries else None),
                "stale": self._stale.get(key, False),
                "failing_since": (self._failing[key]
                                  if key in self._failing
                                  and self._failed_at.get(key, 0.0) >= horizon
                                  else None),
                "error": self._errors.get(key),
            } for key in keys}


class MarketHub:
    """Carnets temps réel + données REST mises en cache."""

    #: Durées de vie des caches, en secondes.
    TTL_KLINES = 5
    #: Une page d'historique antérieur est close : ses bougies ne
    #: changeront plus jamais. Une heure de cache, sans coût de fraîcheur ;
    #: passé ce délai sans relecture, le ménage l'oublie (`CACHE_IDLE`) —
    #: le disque la tient de toute façon.
    TTL_KLINES_HISTORY = 3600
    #: Binance injoignable, l'historique sauvegardé répond à sa place, et la
    #: source n'est retentée qu'après un délai : le pousseur, qui lit les
    #: chandeliers toutes les deux secondes, attendrait sinon chaque délai
    #: de connexion. Ce délai suit la panne — cinq secondes d'abord, puis la
    #: durée de la panne déjà vue, une minute au plus. Fixé à une minute, il
    #: figeait le graphique soixante secondes pour un accroc que le premier
    #: nouvel essai effaçait — douze des vingt-trois pannes relues au
    #: journal se rétablissaient en deux minutes au plus. Le temps écoulé,
    #: et non le compte des échecs, fait grandir le délai : plusieurs
    #: lecteurs qui échouent ensemble ne le doublent pas d'un coup.
    KLINES_RETRY = 60
    KLINES_RETRY_FIRST = 5.0
    #: Bougies relues pour le bilan des signaux, et cadence de ce bilan : il
    #: ne change qu'avec les nouvelles clôtures.
    DEEP_BARS = 20_000
    TTL_SIGNAL_STATS = 600
    #: Complément de l'historique en tâche de fond : de l'intervalle le plus
    #: large au plus fin, soit cent trente appels la première fois. Le quart
    #: d'heure et la demi-heure en demanderaient trois fois et demie plus, pour
    #: des bougies qui vieillissent vite ; ils se remplissent à l'usage.
    BACKFILL_INTERVALS = ("1M", "1w", "1d", "12h", "6h", "4h", "1h")
    #: Une page toutes les deux secondes : loin des limites de Binance, et
    #: invisible à côté du graphique qui attend sa fin de série.
    BACKFILL_PAUSE = 2.0
    #: Laisser le démarrage passer d'abord : le réseau, puis le terminal.
    BACKFILL_DELAY = 60.0
    #: Une panne se dit au bandeau tant que la source a échoué depuis moins
    #: que ce délai. Une page d'historique ratée une fois, que personne ne
    #: relit, s'efface ainsi d'elle-même au lieu de rester affichée.
    FAILING_WINDOW = FAILING_WINDOW
    TTL_TICKER = 5
    TTL_EUR = 3600
    #: Délai avant de réinterroger une source de change en panne.
    EUR_RETRY = 60
    #: Taux servi quand la source de change n'a jamais répondu —
    #: démarrage hors ligne. Un ordre de grandeur (septembre 2026), dit
    #: comme tel à l'écran, jamais une donnée.
    EUR_FALLBACK = 0.88
    TTL_ETF = 1800
    #: Options et futures à échéance : une photographie de la chaîne par
    #: minute suffit à un tableau de positionnement, et ménage Deribit — la
    #: requête rend près d'un millier d'instruments. DVOL est journalier.
    TTL_OPTIONS = 60
    TTL_DVOL = 600
    #: Fenêtre de la volatilité réalisée comparée à DVOL : trente jours,
    #: l'horizon de l'indice.
    REALIZED_WINDOW = 30
    #: Marchés traditionnels (§ markets) : des clôtures quotidiennes, dont
    #: la dernière bouge pendant la séance — cinq minutes, l'horloge rare.
    #: Deux ans de profondeur : un an au graphique, et la chauffe des
    #: corrélations à quatre-vingt-dix séances.
    TTL_MARKETS = 300
    MARKETS_SPAN = "2y"
    MARKETS_BTC_DAYS = 730
    #: Les clôtures du cycle : neuf ans de jours, relus du disque — une
    #: minute suffit à un panneau qui se lit à l'échelle des mois.
    TTL_CYCLE = 60
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

    #: La liquidité journalisée : le volume offert à moins de `DEPTH_BAND`
    #: pour cent du prix médian de chaque plateforme — une portée que les
    #: cinq carnets couvrent tous, et celle où un ordre de quelques
    #: dizaines de bitcoins trouve ou non sa contrepartie. Un carnet
    #: respire à la seconde : la valeur écrite est la moyenne
    #: d'échantillons pris toutes les `DEPTH_SAMPLE_EVERY` secondes sur la
    #: période d'un instantané, et pas une photographie. En deçà de
    #: `DEPTH_MIN_SAMPLES`, la plateforme n'a pas assez parlé pour compter.
    DEPTH_BAND = 0.1
    DEPTH_SAMPLE_EVERY = 10.0
    DEPTH_MIN_SAMPLES = 6
    #: Un carnet sans mise à jour depuis ce délai ne dit plus le marché.
    DEPTH_MAX_AGE_MS = 10_000.0

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
    #: « cinq sources en panne » en une journée, une par réveil. La boucle
    #: lit donc le sommeil sur les horloges (`SleepWatch`) et rattend le
    #: réseau avant de reprendre. Les autres lectures ne l'attendent pas :
    #: dans la minute d'un réveil, leurs échecs ne comptent pas comme
    #: pannes (le réseau est revenu en 4 à 28 s aux réveils mesurés), et
    #: Binance est retenté toutes les cinq secondes sans que le délai ne
    #: grandisse (`KLINES_RETRY_FIRST`) — l'attente armée par un échec du
    #: réveil laissait le graphique une minute en retard sur un réseau
    #: déjà revenu.
    WAKE_GRACE = 60.0
    #: Ménage du cache mémoire (`TTLCache.sweep`) : ce que personne n'a lu
    #: depuis une heure, et dont la durée de vie est passée, est oublié —
    #: une heure, la durée de vie d'une page d'historique, qui devait
    #: justement borner la mémoire et ne la bornait pas. Le ménage passe
    #: toutes les dix minutes, dans la boucle d'observation.
    CACHE_IDLE = 3600.0
    CACHE_SWEEP_EVERY = 600.0
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
        #: Le ruban des échanges (§ tape) : gros ordres et flux agressif
        #: de Binance, Coinbase et Bybit au comptant.
        self.tape = TradeTape()
        self.started_at = time.time()

        #: Journal des données éphémères (liquidations, épisodes
        #: d'arbitrage). Sa base n'existe qu'à la première écriture :
        #: un hub jamais démarré — les tests — ne crée aucun fichier.
        self.journal: Optional[Journal] = Journal() if keep_journal else None
        if self.journal is not None:
            self.liquidations.on_event = self.journal.record_liquidation
            # Les gros ordres du ruban aussi : sans eux, chaque redémarrage
            # effaçait l'heure en mémoire.
            self.tape.on_large = self.journal.record_trade

        #: Heure locale de la synthèse du matin (§ brief), envoyée au sujet
        #: ntfy des alertes ; `None` : pas d'envoi. Posée par la fabrique
        #: (`BTCTERM_BRIEF`) ou la ligne de commande (`--brief`).
        self.brief_at: Optional[dt.time] = None
        #: Le jour du dernier envoi, à côté du journal quand il est tenu :
        #: un service relancé après l'heure ne la renvoie pas.
        self._brief_sent = brief.SentLog(
            self.journal.path.parent / "brief.json"
            if self.journal is not None else None)

        #: Les bougies closes sur disque (§ history). Lié au journal, et
        #: pas seulement par commodité : les tests remplacent la source par
        #: des bougies fabriquées, qui ne doivent pas finir dans la vraie
        #: base — `keep_journal=False` les en tient à l'écart.
        self.history: Optional[KlineHistory] = (
            KlineHistory(symbol) if keep_journal else None)
        #: Instant avant lequel Binance n'est pas retenté (`KLINES_RETRY`),
        #: et début de la panne en cours, qui règle ce délai.
        self._klines_retry_at = 0.0
        self._klines_down_since: Optional[float] = None
        self._backfill_thread: Optional[threading.Thread] = None

        #: Moteur d'alertes : évalué par la boucle d'observation, réglé
        #: par le panneau ALERTES, journalisé quand le journal est tenu.
        self.alerts = AlertEngine(journal=self.journal)

        self._observe_stop = threading.Event()
        #: Échantillons de liquidité de la période en cours, par
        #: plateforme : [somme des achats, somme des ventes, nombre].
        self._depth_acc: dict[str, list[float]] = {}
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

        #: Les réveils de la machine : la boucle d'observation y rattend
        #: le réseau, le cache n'y compte pas les échecs comme des pannes.
        self._sleep = SleepWatch()
        #: Le réveil que la boucle a déjà traité (`SleepWatch.woke_at`).
        self._wake_handled: Optional[float] = None
        self._cache = TTLCache(self._sleep, self.WAKE_GRACE)
        #: Instant avant lequel la source de change, tombée, n'est pas
        #: réinterrogée (`eur_rate`).
        self._eur_retry_at = 0.0
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
        self._warm_tape()
        self._warm_alerts()
        self._connectors = [
            # Des carnets de portée comparable : la profondeur comparée et
            # les murs ne valent que si chaque plateforme montre plus que
            # son premier rang (§2.2).
            BinanceConnector(self.books["Binance"], symbol="btcusdt", depth=1000),
            KrakenConnector(self.books["Kraken"], pair="XBT/USDT", depth=100),
            BybitConnector(self.books["Bybit"], symbol="BTCUSDT", depth=200),
            OKXConnector(self.books["OKX"], inst_id="BTC-USDT"),
            CoinbaseAdvancedConnector(self.books["Coinbase"], product="BTC-USDT"),
            self.liquidations,
            # Seconde source du même fil : Binance tait ses flux futures
            # depuis certains pays, Bybit non.
            BybitLiquidationConnector(self.liquidations),
            # Troisième : OKX, tous les perpétuels d'un abonnement.
            OKXLiquidationConnector(self.liquidations),
            BinanceTradeConnector(self.tape),
            CoinbaseTradeConnector(self.tape),
            BybitTradeConnector(self.tape),
        ]
        self._thread = run_connectors_in_thread(self._connectors)

        if self.collect_news:
            self.news.start()

        self._observe_thread = threading.Thread(
            target=self._observe_loop, daemon=True, name="observe")
        self._observe_thread.start()
        if self.history is not None:
            self._backfill_thread = threading.Thread(
                target=self._backfill_loop, daemon=True, name="backfill")
            self._backfill_thread.start()

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

    def _warm_tape(self) -> int:
        """Rend au ruban les gros ordres de la dernière heure, du journal —
        comme les liquidations : un panneau vide après un redémarrage se
        lit comme une panne des flux."""
        if self.journal is None:
            return 0
        now = time.time()
        try:
            rows = self.journal.trades_between(now - self.tape.keep, now)
        except Exception:
            return 0
        return self.tape.restore(
            Trade(time=row["ts"], exchange=row["exchange"], side=row["side"],
                  price=row["price"], quantity=row["quantity"])
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
        next_sweep = time.monotonic() + self.CACHE_SWEEP_EVERY
        next_depth = time.monotonic() + self.DEPTH_SAMPLE_EVERY
        while not self._observe_stop.wait(1.0):
            self._after_sleep()
            if time.monotonic() >= next_depth:
                next_depth = time.monotonic() + self.DEPTH_SAMPLE_EVERY
                self._sample_depth()
            if time.monotonic() >= next_sweep:
                next_sweep = time.monotonic() + self.CACHE_SWEEP_EVERY
                self._cache.sweep(self.CACHE_IDLE)
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
            self._maybe_brief()
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
                try:
                    self._flush_depth()
                except Exception:
                    pass

    def _maybe_brief(self, now: Optional[float] = None) -> bool:
        """Envoie la synthèse du jour, une fois son heure passée.

        Une fois par jour au plus, la date d'envoi notée avant de partir :
        la construire lit plusieurs sources, jusqu'à leurs délais, dans un
        thread à elle — la boucle d'observation ne l'attend pas, et ne la
        relance pas entre-temps. Une machine qui dormait à l'heure dite
        l'envoie au réveil, datée : le résumé reste celui de la journée
        écoulée. Rien ne part sans sujet ntfy.
        """
        notifier = self.alerts.notifier
        if self.brief_at is None or notifier is None:
            return False
        now = time.time() if now is None else now
        moment = dt.datetime.fromtimestamp(now)
        if (moment.time() < self.brief_at
                or self._brief_sent.sent_on(moment.date())):
            return False
        self._brief_sent.mark(moment.date())
        threading.Thread(target=self._send_brief, args=(notifier, now),
                         name="brief", daemon=True).start()
        return True

    def _send_brief(self, notifier, now: float) -> None:
        try:
            text = brief.build(self, now).text()
        except Exception as exc:  # noqa: BLE001 — demain, elle repartira
            log.warning("synthèse du matin non construite : %s", exc)
            return
        notifier.send(Alert(time=now, kind="brief", message=text))
        log.info("synthèse du matin envoyée à %s", notifier.describe())

    def _after_sleep(self) -> None:
        """Rattend le réseau si la machine s'est réveillée depuis le tour
        précédent.

        `Event.wait` compte en temps monotone, que le sommeil n'avance
        pas : le tour qui suit un réveil part à la seconde même, avant que
        la machine ne soit reconnectée — et déclarait toutes les sources
        en panne. Le réveil a pu être vu d'abord par une autre lecture
        (`SleepWatch`) : la boucle le reconnaît à sa date.
        """
        self._sleep.sample()
        woke_at = self._sleep.woke_at
        if woke_at is None or woke_at == self._wake_handled:
            return
        self._wake_handled = woke_at
        slept = self._sleep.slept
        sources.reset_connections()
        self._wait_for_network(
            "au réveil, après "
            + (f"{slept:.0f} s" if slept < 60 else f"{slept / 60:.0f} min")
            + " de sommeil")
        # Le fil de news a l'âge du sommeil : sa tournée n'attend pas la
        # fin de son intervalle, que le sommeil n'a pas fait avancer.
        if self.collect_news:
            self.news.nudge()

    def stop(self) -> None:
        self.stopping.set()
        for connector in self._connectors:
            connector.stop()
        self.news.stop()
        if self.alerts.notifier is not None:
            self.alerts.notifier.stop()
        self._observe_stop.set()
        if self._observe_thread is not None:
            self._observe_thread.join(timeout=3)
            self._observe_thread = None
        if self.journal is not None:
            # Les épisodes encore ouverts partent avec la séance.
            self.journal.flush()
            self.journal.close()
        if self.history is not None:
            self.history.close()

    def woke_within(self, seconds: float) -> bool:
        """Vrai si la machine s'est réveillée il y a moins de `seconds`."""
        return self._sleep.woke_within(seconds)

    def realtime_links(self) -> list[tuple[str, bool]]:
        """Tous les liens temps réel et s'ils tiennent : carnets, fils de
        liquidations, ruban. Le bandeau ne comptait que les cinq carnets."""
        links = [(f"carnet {name}", book.connected)
                 for name, book in self.books.items()]
        links += [(f"liquidations {name}", up)
                  for name, (up, _) in self.liquidations.links.items()]
        links += [(f"ruban {name}", up)
                  for name, (up, _) in self.tape.links.items()]
        return links

    @property
    def connected_count(self) -> int:
        return sum(1 for book in self.books.values() if book.connected)

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)

    # ── Marché ──────────────────────────────────────────────

    def klines(self, interval: str = "1d", limit: int = 350) -> pd.DataFrame:
        """Chandeliers, mutualisés entre panneaux via le cache.

        Les bougies closes viennent du disque (§ history), Binance ne
        fournit que la fin de la série. S'il se tait et que le cache n'a
        rien, l'historique sauvegardé répond seul : le DataFrame porte
        `attrs["stale"]`, et la bougie en cours y manque. Il ne reste la
        série de démonstration que si le disque n'a rien non plus —
        typiquement un premier démarrage hors ligne. Le terminal s'ouvre
        alors quand même, et le graphique le dit dans les deux cas :
        `attrs["demo"]` et `attrs["stale"]`, que `/api/klines` relaie au
        navigateur où lwc-price.js pose un bandeau d'avertissement. Sans
        ce repli, un panneau prix vide et une trace dans la console
        seraient tout ce que l'utilisateur obtiendrait.
        """
        key = f"klines:{interval}:{limit}"
        try:
            df = self._cache.get(
                key,
                self.TTL_KLINES,
                lambda: self._through_history(
                    lambda: self.history.latest(interval, limit),
                    lambda: sources.fetch_klines(self.symbol, interval, limit),
                ),
            )
            # Binance se tait en pleine séance : le cache sert sa dernière
            # série, et la bougie en cours s'y fige. Elle se dit comme
            # l'autre. Copie superficielle : l'objet du cache, que le mémo
            # du pousseur suit à la trace, reste celui d'une lecture réussie.
            if self._cache.stale(key):
                df = df.copy(deep=False)
                df.attrs["stale"] = True
            return df
        except Exception:
            df = self._stored_klines(interval, limit)
            if df.empty:
                return sources.generate_demo_ohlcv(
                    limit, interval=interval, index=False)
            df.attrs["stale"] = True
            return df

    def deep_klines(self, interval: str,
                    limit: Optional[int] = None) -> pd.DataFrame:
        """Les bougies closes les plus nombreuses possible, sans réseau si
        le disque en tient. La bougie en cours n'y est pas : un bilan ou un
        export n'ont que faire d'une bougie qui bouge encore. Sans disque,
        la dernière page, moins cette bougie ; jamais la démonstration.
        """
        df = self._stored_klines(interval, limit or self.DEEP_BARS)
        if not df.empty:
            return df
        live = self.klines(interval, limit=1000)
        if live.attrs.get("demo", False) or len(live) < 2:
            return pd.DataFrame(columns=["time"] + sources.OHLCV_COLUMNS)
        return live.iloc[:-1].reset_index(drop=True)

    def signal_stats(self, interval: str) -> Optional[dict]:
        """Le bilan des signaux d'un intervalle (§ analysis)."""
        return self._cache.get(
            f"signal_stats:{interval}", self.TTL_SIGNAL_STATS,
            lambda: signal_stats(self.deep_klines(interval)))

    def rule_stats(self, interval: str) -> Optional[dict]:
        """Les règles simples face à la détention (§ analysis)."""
        return self._cache.get(
            f"rule_stats:{interval}", self.TTL_SIGNAL_STATS,
            lambda: rule_stats(self.deep_klines(interval)))

    def monthly_returns(self) -> Optional[dict]:
        """Les rendements mensuels, le mois en cours compris."""
        return monthly_returns(self.klines("1M", limit=120))

    def _backfill_loop(self) -> None:
        """Complète l'historique vers le passé, une page à la fois.

        Le bilan des signaux n'a de sens qu'avec de la profondeur, et le pan
        vers le passé n'attend plus Binance à la première visite. Rien
        d'urgent : Binance muet, la boucle patiente et reprend où elle en
        était ; base inutilisable, elle s'arrête sans bruit de plus.
        """
        if self._observe_stop.wait(self.BACKFILL_DELAY):
            return
        for interval in self.BACKFILL_INTERVALS:
            while True:
                try:
                    if self.history is None or not self.history.extend_back(
                            interval):
                        break
                except sqlite3.Error as exc:
                    log.warning("complément de l'historique arrêté : %s", exc)
                    return
                except Exception:
                    if self._observe_stop.wait(self.KLINES_RETRY):
                        return
                    continue
                if self._observe_stop.wait(self.BACKFILL_PAUSE):
                    return

    def _through_history(self, read: Callable[[], pd.DataFrame],
                         direct: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        """Lit par l'historique sur disque, ou à la source sans lui.

        Source injoignable : elle n'est retentée qu'après un délai qui suit
        la panne (`KLINES_RETRY`), et la lecture refusée entre-temps lève
        `SourceBackoff`, que le cache ne compte pas comme une panne de
        plus. Base inutilisable — disque plein, fichier abîmé : ce n'est
        qu'un cache, le terminal continue sans lui.
        """
        if self.history is None:
            return direct()
        if time.time() < self._klines_retry_at:
            raise SourceBackoff("Binance en attente de nouvel essai")
        try:
            frame = read()
        except sqlite3.Error as exc:
            log.warning("historique du cours inutilisable : %s — "
                        "lecture directe de la source", exc)
            self.history = None
            return direct()
        except Exception:
            now = time.time()
            if self._klines_down_since is None:
                self._klines_down_since = now
            self._klines_retry_at = now + self._klines_delay(now)
            raise
        self._klines_down_since = None
        return frame

    def _klines_delay(self, now: float) -> float:
        """Le délai avant de retenter Binance, d'après la panne en cours.

        Cinq secondes, puis autant que la panne déjà vue — ce qui double
        le délai d'un essai à l'autre —, une minute au plus. Dans la minute
        d'un réveil, c'est le réseau qui manque et il revient en quelques
        secondes : le délai ne grandit pas.
        """
        if self._cache.waking() or self._klines_down_since is None:
            return self.KLINES_RETRY_FIRST
        return min(self.KLINES_RETRY,
                   max(self.KLINES_RETRY_FIRST, now - self._klines_down_since))

    def _stored_klines(self, interval: str, limit: int,
                       end_ms: Optional[int] = None) -> pd.DataFrame:
        """Ce que le disque tient, ou rien s'il n'y a pas de disque."""
        if self.history is not None:
            try:
                return self.history.stored(interval, limit, end_ms)
            except sqlite3.Error:
                pass
        return pd.DataFrame(columns=["time"] + sources.OHLCV_COLUMNS)

    def klines_history(
        self, interval: str, end_ms: int, limit: int = 350
    ) -> pd.DataFrame:
        """Page d'historique antérieur : les `limit` bougies ouvertes au
        plus tard à `end_ms` (millisecondes epoch).

        C'est le lazy-loading du panneau prix qui appelle ici, au pan
        vers le passé. Pas de repli de démonstration : une page que la
        source ne peut pas servir revient avec ce que le disque tient,
        vide au besoin.

        Mais une page vide ne dit pas toujours la même chose. Historique
        épuisé, le client cesse de demander plus ancien ; source muette,
        il redemandera. Le journal du service a montré un pan en quart
        d'heure tombé pendant les soixante secondes d'attente de Binance :
        le disque, qui ne tient le quart d'heure que par l'usage, n'avait
        rien, et le graphique ne remontait plus le passé jusqu'au
        changement d'intervalle. La page servie de secours porte donc
        `attrs["stale"]`, que `/api/klines` relaie.
        """
        try:
            return self._cache.get(
                f"klines:{interval}:{limit}:{int(end_ms)}",
                self.TTL_KLINES_HISTORY,
                lambda: self._through_history(
                    lambda: self.history.page(interval, int(end_ms), limit),
                    lambda: sources.fetch_klines(
                        self.symbol, interval, limit, end_time=int(end_ms)),
                ),
            )
        except Exception:
            df = self._stored_klines(interval, limit, int(end_ms))
            df.attrs["stale"] = True
            return df

    def ticker(self) -> dict:
        return self._cache.get(
            "ticker", self.TTL_TICKER, lambda: sources.fetch_ticker_24h(self.symbol)
        )

    def eur_rate(self) -> float:
        """Taux USD→EUR, jamais une exception.

        Source de change en panne : le dernier taux lu, ou à défaut
        `EUR_FALLBACK` — `eur_rate_stale` le dit, et le bandeau comme le
        graphique le signalent. La source n'est alors réinterrogée
        qu'une fois par `EUR_RETRY` : le bandeau lit le taux toutes les
        deux secondes, et chaque essai peut coûter les cinq secondes du
        timeout.
        """
        if time.time() < self._eur_retry_at:
            cached = self._cache.peek("eur")
            return self.EUR_FALLBACK if cached is None else cached
        try:
            rate = self._cache.get("eur", self.TTL_EUR, sources.fetch_eur_rate)
        except Exception:
            rate = self.EUR_FALLBACK
        if self._cache.stale("eur"):
            self._eur_retry_at = time.time() + self.EUR_RETRY
        return rate

    def eur_rate_stale(self) -> bool:
        """Vrai si le taux servi n'est pas une lecture fraîche de la source."""
        return self._cache.stale("eur")

    def eur_rate_cached(self) -> tuple[float, bool]:
        """Le taux USD→EUR et s'il est de secours, sans jamais toucher au réseau.

        Pour les panneaux rapides : `eur_rate` peut coûter les cinq
        secondes du timeout, et le pousseur dormirait autant, carnet figé.
        Le cache est tenu par le bandeau, toujours dans la page, qui lit
        `eur_rate` à son régime dès que la devise est l'euro. Rien de lu
        encore : `EUR_FALLBACK`, dit de secours.
        """
        cached = self._cache.peek("eur")
        if cached is None:
            return self.EUR_FALLBACK, True
        return cached, self._cache.stale("eur")

    #: Ce que l'état des sources ne montre pas : des calculs tirés des
    #: sources, pas des sources.
    STATUS_IGNORED = ("signal_stats", "rule_stats", "cycle_closes")
    #: Un carnet connecté sans mise à jour depuis ce délai est muet : en
    #: marché ouvert, un carnet BTC bouge à la seconde.
    BOOK_SILENT = 30.0
    #: Le silence d'un lien de liquidations — celui du panneau du même nom.
    LIQ_SILENT = 15 * 60.0

    def sources_status(self, now: Optional[float] = None) -> dict[str, list[dict]]:
        """L'état de chaque source, par famille : de quoi dire, sans lire le
        journal système, ce qui répond, ce qui sert de secours et ce qui se
        tait — et depuis quand.

        Chaque ligne : `name`, `state` (`ok`, `secours`, `panne`, `muet`,
        `coupé`, `inactif`), `age` (secondes depuis la dernière donnée
        fraîche, ou `None`) et `detail` (l'erreur, ou ce qu'il faut savoir).
        Le cache compte par clé — une par intervalle de chandeliers, une par
        page d'historique — : les clés se regroupent par famille, la plus
        mal en point donnant l'état, la plus fraîche l'âge.
        """
        now = time.time() if now is None else now
        streams = []
        for name, book in self.books.items():
            age = now - book.timestamp
            if not book.connected:
                state, detail = "coupé", book.error or "déconnecté"
            elif age > self.BOOK_SILENT:
                state, detail = "muet", "connecté, sans mise à jour"
            else:
                state, detail = "ok", "carnet WebSocket"
            streams.append({"name": f"Carnet {name}", "state": state,
                            "age": age, "detail": detail})
        feed = self.liquidations
        silent = dict(feed.silent(self.LIQ_SILENT))
        for name, (up, error) in feed.links.items():
            age = feed.last_event_age(name)
            if not up:
                state, detail = "coupé", error or "déconnecté"
            elif name in silent:
                state, detail = "muet", "connecté, sans liquidation livrée"
            else:
                state, detail = "ok", "fil de liquidations"
            streams.append({"name": f"Liquidations {name}", "state": state,
                            "age": age, "detail": detail})
        for name, (up, error) in self.tape.links.items():
            age = self.tape.last_event_age(name)
            if not up:
                state, detail = "coupé", error or "déconnecté"
            elif age is not None and age > self.BOOK_SILENT:
                state, detail = "muet", "connecté, sans échange reçu"
            else:
                state, detail = "ok", "ruban des échanges"
            streams.append({"name": f"Ruban {name}", "state": state,
                            "age": age, "detail": detail})

        families: dict[str, dict] = {}
        rank = {"ok": 0, "secours": 1, "panne": 2}
        for key, info in self._cache.status().items():
            family = key.split(":", 1)[0]
            if family in self.STATUS_IGNORED:
                continue
            state = ("panne" if info["failing_since"] is not None
                     else "secours" if info["stale"] else "ok")
            row = families.setdefault(family, {"name": family, "state": "ok",
                                               "age": None, "detail": "",
                                               "keys": 0})
            row["keys"] += 1
            if info["fresh_at"] is not None:
                age = now - info["fresh_at"]
                row["age"] = age if row["age"] is None else min(row["age"], age)
            if rank[state] > rank[row["state"]]:
                row["state"] = state
                row["detail"] = info["error"] or ""
                if info["failing_since"] is not None:
                    row["since"] = info["failing_since"]
        rest = sorted(families.values(),
                      key=lambda row: (-rank[row["state"]], row["name"]))

        tasks = []
        if self.collect_news:
            news = self.news.status
            tasks.append({
                "name": "Collecte des news",
                "state": "panne" if news.get("error") else "ok",
                "age": (now - news["last_run"]) if news.get("last_run") else None,
                "detail": news.get("error") or f"{news.get('new', 0)} nouvelles "
                                                "au dernier passage"})
        else:
            tasks.append({"name": "Collecte des news", "state": "inactif",
                          "age": None, "detail": "lancée sans --news"})
        if self.journal is not None:
            rows = self.journal.snapshots_between(now - 3600, now)
            last = rows[-1]["ts"] if rows else None
            tasks.append({
                "name": "Instantané de marché",
                "state": "ok" if last and now - last < 2 * self.SNAPSHOT_EVERY
                else "muet",
                "age": (now - last) if last else None,
                "detail": f"journal {self.journal.path.name}, toutes les "
                          f"{self.SNAPSHOT_EVERY / 60:.0f} min"})
        else:
            tasks.append({"name": "Instantané de marché", "state": "inactif",
                          "age": None, "detail": "lancé sans journal"})
        tasks.append({
            "name": "Résolution DNS de secours",
            "state": "ok" if resolver.is_installed() else "inactif",
            "age": None,
            "detail": (f"{len(resolver._cache)} noms résolus par DoH"
                       if resolver.is_installed() else "non installée")})
        return {"Flux temps réel": streams, "Sources REST": rest,
                "Tâches de fond": tasks}

    def failing_sources(self) -> dict[str, float]:
        """Les sources en panne, par famille, avec le début de la panne.

        Le cache compte par clé, soit une par intervalle pour les
        chandeliers : au bandeau, c'est la source qui importe.
        """
        families: dict[str, float] = {}
        for key, since in self._cache.failing(self.FAILING_WINDOW).items():
            name = key.split(":", 1)[0]
            families[name] = min(since, families.get(name, since))
        return families

    def reference_price(self) -> Optional[float]:
        """Prix médian de la plateforme la plus fraîche encore connectée."""
        live = [b for b in self.books.values() if b.connected and b.mid]
        if not live:
            return None
        return min(live, key=lambda b: b.age_ms).mid

    # ── Contexte ────────────────────────────────────────────

    def option_chain(self) -> pd.DataFrame:
        """La chaîne d'options Deribit (§ options) ; vide si la source se tait
        et que le cache n'a rien."""
        try:
            return self._cache.get(
                "options", self.TTL_OPTIONS,
                lambda: opt.option_chain(sources.fetch_deribit_summary("option")))
        except Exception:
            return opt.option_chain([])

    def futures_curve(self) -> pd.DataFrame:
        """La base des futures Deribit à échéance, annualisée (§ options)."""
        try:
            return self._cache.get(
                "futures", self.TTL_OPTIONS,
                lambda: opt.futures_curve(sources.fetch_deribit_summary("future")))
        except Exception:
            return opt.futures_curve([])

    def dvol(self) -> pd.DataFrame:
        """DVOL sur un an, en bougies journalières ; vide si la source se tait."""
        try:
            return self._cache.get("dvol", self.TTL_DVOL, sources.fetch_dvol)
        except Exception:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close"])

    def dvol_cached(self) -> pd.DataFrame:
        """DVOL tel que le cache le tient, sans jamais toucher au réseau.

        Pour le bandeau, qui lit toutes les deux secondes : une panne de
        Deribit y bloquerait le cours le temps du délai d'attente. Le cache
        est tenu chaud par la règle d'alerte de volatilité, à la minute, et
        par le panneau options quand il est affiché.
        """
        cached = self._cache.peek("dvol")
        if cached is None:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        return cached

    def realized_volatility(self) -> pd.DataFrame:
        """Volatilité réalisée à trente jours, annualisée, en points.

        Sur les clôtures journalières — la série que l'alerte de la MA 200
        lit déjà, d'où aucun appel de plus. Rien sur la série de
        démonstration : comparer DVOL à une marche aléatoire n'aurait aucun
        sens.
        """
        daily = self.klines("1d", 365)
        if daily.attrs.get("demo", False) or len(daily) <= self.REALIZED_WINDOW:
            return pd.DataFrame(columns=["time", "value"])
        values = volatility(daily["close"], window=self.REALIZED_WINDOW,
                            periods_per_year=365)
        return (pd.DataFrame({"time": daily["time"], "value": values})
                .dropna().reset_index(drop=True))

    def markets(self) -> dict[str, pd.DataFrame]:
        """Les marchés confrontés au bitcoin, par clé (§ markets).

        Une série par marché, chacune son cache : une place muette laisse
        les autres répondre, et son absence se dit au tableau. Les cinq
        lectures partent ensemble : l'une après l'autre, des ratés de
        Yahoo — délai dépassé, connexion coupée, vus dès le premier essai —
        faisaient attendre le panneau cinq délais au lieu d'un.
        """
        def read(asset):
            try:
                return self._cache.get(
                    f"market:{asset.key}", self.TTL_MARKETS,
                    lambda: sources.fetch_yahoo_daily(asset.symbol,
                                                      self.MARKETS_SPAN))
            except Exception:
                return pd.DataFrame(columns=["time", "close"])

        with ThreadPoolExecutor(max_workers=len(mkt.ASSETS)) as pool:
            return dict(zip((asset.key for asset in mkt.ASSETS),
                            pool.map(read, mkt.ASSETS)))

    def btc_daily(self) -> pd.DataFrame:
        """Deux ans de clôtures journalières du bitcoin, bougie du jour
        comprise ; vide sur la série de démonstration, qu'on ne compare à
        rien."""
        daily = self.klines("1d", self.MARKETS_BTC_DAYS)
        if daily.attrs.get("demo", False):
            return pd.DataFrame(columns=["time", "close"])
        return daily[["time", "close"]]

    def cycle_closes(self) -> pd.Series:
        """Toutes les clôtures journalières, celle du jour comprise, pour
        le panneau du cycle (§ cycle) ; vide sur la série de démonstration.

        Le disque donne les jours clos depuis 2017, la lecture courante le
        jour en cours : rien n'est demandé de plus à Binance.
        """
        def read() -> pd.Series:
            frames = [self.deep_klines("1d")[["time", "close"]],
                      self.btc_daily()]
            frames = [frame for frame in frames if not frame.empty]
            if not frames:
                return pd.Series(dtype=float)
            return cycle.closes(pd.concat(frames, ignore_index=True))
        return self._cache.get("cycle_closes", self.TTL_CYCLE, read)

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

    def market_cap_change(self, now: Optional[float] = None
                          ) -> Optional[tuple[float, float]]:
        """La variation de la capitalisation totale sur un jour, mesurée au
        journal : `(pourcentage, heures couvertes)`, ou `None`.

        La source donne la sienne, mais sans dire sur quel univers : le
        11 novembre 2026, elle annonçait −4,07 % quand le journal — même
        univers aux deux bouts (§ journal.rebase_universe) — en mesurait
        −1,69, et la capitalisation du bitcoin −1,45. La référence est
        l'instantané le plus proche de vingt-quatre heures plus tôt ; la
        machine a pu dormir à ce moment-là, et les heures rendues disent
        l'écart réel. Sans journal, ou avec moins d'une demi-journée de
        recul, il n'y a rien à mesurer.
        """
        if self.journal is None:
            return None
        now = time.time() if now is None else now
        rows = self.journal.snapshots_rebased(
            now - (brief.HOURS + 6) * 3600, now)
        found = brief.change(rows, "total_cap_usd", now)
        if found is None or found[1] is None or not found[1]:
            return None
        current, reference, span = found
        return (current / reference - 1) * 100, span

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
        # Le marché des options : Deribit ne sert gratuitement l'historique
        # que de DVOL ; le skew, la volatilité à un mois, le put/call et la
        # base n'existent qu'au présent, et seul ce journal les garde.
        chain = self.option_chain()
        if self._cache.stale("options"):
            chain = opt.option_chain([])
        futures = self.futures_curve()
        if self._cache.stale("futures"):
            futures = opt.futures_curve([])
        dvol = self.dvol()
        if self._cache.stale("dvol"):
            dvol = dvol.iloc[0:0]
        options = opt.summary(chain, futures)

        shares = agregats.get("shares") or {}
        fields = {
            "btc_dominance": shares.get("BTC"),
            "stable_share": (sum(part for nom, part in shares.items()
                                 if nom in sources.STABLES)
                             if shares else None),
            "total_cap_usd": agregats.get("total_cap_usd"),
            "total_volume_usd": agregats.get("total_volume_usd"),
            # La part retirée de la capitalisation annoncée (§ sources.
            # INTERMITTENT) : la colonne distingue ces lignes de celles
            # d'avant, que la lecture doit encore rebaser.
            "cap_excluded": agregats.get("excluded") if shares else None,
            "oi_usd": float(oi["oi_usd"].iloc[-1]) if not oi.empty else None,
            #: Le taux de la période en cours — celui que la prochaine
            #: échéance réglera : rééchantillonné par tranches de 8 h,
            #: il reconstitue l'historique des règlements (§ funding_
            #: history_extended).
            "funding_rate": perp.get("funding_rate"),
            "dvol": float(dvol["close"].iloc[-1]) if not dvol.empty else None,
            "atm30_iv": (options["atm30"] * 100
                         if options["atm30"] is not None else None),
            "rr25": options["rr25_30"],
            "pc_oi": options["pc_oi"],
            "basis90": (options["basis90"][1] * 100
                        if options["basis90"] is not None else None),
            # La prime Coinbase des cinq dernières minutes — la cadence de
            # l'instantané : un historique qu'aucune API gratuite ne sert.
            "cb_premium": (premium * 100 if (premium := self.tape
                           .coinbase_premium(self.SNAPSHOT_EVERY)) is not None
                           else None),
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
        columns = ["time", *SNAPSHOT_FIELDS, "gap"]
        if self.journal is None:
            return pd.DataFrame(columns=columns)
        end = time.time()
        # Rebasés : les lignes d'avant le retrait des actifs intermittents
        # portent des sauts de dominance qui ne sont pas du marché.
        rows = self.journal.snapshots_rebased(end - days * 86_400, end)
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame(rows)
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

    def _sample_depth(self) -> None:
        """Un échantillon de liquidité par carnet qui dit le marché."""
        for name, book in self.books.items():
            if not book.connected or book.age_ms > self.DEPTH_MAX_AGE_MS:
                continue
            depth = book.depth_within(self.DEPTH_BAND)
            if depth is None:
                continue
            acc = self._depth_acc.setdefault(name, [0.0, 0.0, 0])
            acc[0] += depth[0]
            acc[1] += depth[1]
            acc[2] += 1

    def _flush_depth(self, now: Optional[float] = None) -> None:
        """Écrit la liquidité moyenne de la période, et repart de zéro.

        Une plateforme qui n'a pas livré `DEPTH_MIN_SAMPLES` échantillons
        — carnet coupé, ou en resynchronisation la moitié du temps —
        n'écrit rien : son absence se lit, une moyenne de deux points
        mentirait.
        """
        acc, self._depth_acc = self._depth_acc, {}
        if self.journal is None:
            return
        self.journal.record_book_depth(
            time.time() if now is None else now,
            [(name, bid / n, ask / n) for name, (bid, ask, n) in acc.items()
             if n >= self.DEPTH_MIN_SAMPLES])

    def book_depth_history(self, days: float = 1) -> pd.DataFrame:
        """La liquidité journalisée : `time`, `exchange`, `bid`, `ask`."""
        columns = ["time", "exchange", "bid", "ask"]
        if self.journal is None:
            return pd.DataFrame(columns=columns)
        end = time.time()
        rows = self.journal.depth_between(end - days * 86_400, end)
        if not rows:
            return pd.DataFrame(columns=columns)
        df = pd.DataFrame([dict(row) for row in rows])
        df["time"] = pd.to_datetime(df.pop("ts"), unit="s")
        return df.rename(columns={"bid_btc": "bid", "ask_btc": "ask"})[columns]

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

    def chain_fees(self) -> dict:
        """Frais de transaction estimés, en sat/vB, ou `{}`."""
        try:
            return self._cache.get(
                "fees", self.TTL_CHAIN_STATS, sources.fetch_fee_estimates)
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

    def perp_compare(self) -> dict[str, dict]:
        """Le perpétuel par plateforme ; une plateforme muette est absente.

        Binance vient de `perp_snapshot` et de son open interest, déjà lus
        par le panneau. Bybit et OKX s'y ajoutent à la même cadence : une
        seule plateforme en panne ne prive pas des autres, et le bandeau la
        nomme comme toute source qui se tait.
        """
        rows: dict[str, dict] = {}
        snapshot = self.perp_snapshot()
        if snapshot.get("funding_rate") is not None:
            oi = self.open_interest_extended()
            rows["Binance"] = {
                **snapshot,
                "oi_usd": (float(oi["oi_usd"].dropna().iloc[-1])
                           if oi is not None and not oi.empty else None)}
        for name, key, fetch in (("Bybit", "perp_bybit", sources.fetch_bybit_perp),
                                 ("OKX", "perp_okx", sources.fetch_okx_perp)):
            try:
                rows[name] = self._cache.get(key, self.TTL_PERP, fetch)
            except Exception:
                continue
        return rows

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
