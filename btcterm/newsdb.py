"""
Base de news — socle commun du tracker et du terminal.

`news/btc_news.py` était seul à savoir scorer un article et où vivent les
news ; le panneau du terminal ne pouvait que lire cette base en espérant
que quelqu'un l'ait remplie. Ce module tient les trois choses que les
deux partagent — le schéma SQLite, le scoring, la collecte — et laisse à
chacun ce qui lui est propre : l'affichage en ligne de commande au
tracker, la grille au terminal.

Rien ici n'écrit sur la sortie standard : les collectes signalent ce
qu'elles trouvent, et ce qui échoue, par rappels (`on_new`, `on_error`).

Une connexion SQLite n'étant pas partageable entre threads, chaque
appelant ouvre la sienne ; `NewsCollector` s'en charge pour la boucle de
fond du terminal.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import sources

__all__ = [
    "DB_DIR", "DB_PATH", "MIN_SCORE", "KEYWORDS",
    "connect", "init_db", "article_id", "save_article",
    "score_article", "detect_sentiment",
    "collect_rss", "collect_cryptopanic", "record_fear_greed",
    "latest", "last_fear_greed", "NewsCollector",
]

DB_DIR = Path.home() / ".btc_news"
DB_PATH = DB_DIR / "news.db"

#: Score minimal pour qu'un article soit conservé (0-100).
MIN_SCORE = 30

#: Pondérations par mot-clé. Un article cumule les poids des mots-clés
#: qu'il contient, plafonnés à 100 : c'est ce qui distingue « la SEC
#: approuve un ETF spot » d'un billet d'humeur sur le halving.
KEYWORDS: dict[str, int] = {
    # Macro / régulation (très fort impact)
    "sec":             25, "etf":             25, "federal reserve":  20,
    "fed rate":        20, "interest rate":   20, "regulation":      18,
    "ban":             18, "lawsuit":         15, "congress":        15,
    "senate":          15, "legislation":     15, "treasury":        15,
    "blackrock":       18, "fidelity":        15, "vanguard":        12,
    "spot etf":        25, "bitcoin etf":     25,

    # Adoption institutionnelle
    "microstrategy":   18, "strategy":        10, "tesla":           15,
    "institutional":   15, "corporate":       10, "reserve":         12,
    "sovereign":       18, "nation":          12, "government":      12,

    # On-chain / marché
    "whale":           15, "exchange outflow":20, "exchange inflow": 15,
    "halving":         22, "mining":          10, "hash rate":       10,
    "mempool":          8, "lightning":        8,

    # Macro économique
    "inflation":       15, "cpi":             18, "gdp":             12,
    "recession":       15, "dollar":          12, "usd":             10,
    "gold":            10, "oil":              8,

    # Événements extrêmes
    "hack":            20, "exploit":         20, "breach":          18,
    "bankruptcy":      20, "insolvency":      20, "collapse":        20,
    "liquidation":     18, "short squeeze":   18,

    # Signaux de prix
    "all-time high":   20, "ath":             20, "rally":           12,
    "crash":           18, "dump":            15, "bull":            10,
    "bear":            10, "correction":      12, "resistance":       8,
    "support":          8,

    # Bitcoin spécifique
    "bitcoin":          5, "btc":              5, "satoshi":          5,
    "taproot":         10, "ordinals":        10, "runes":           10,
}

#: Vocabulaire de sentiment, à défaut de votes de communauté.
POSITIVE_WORDS = ["rally", "bull", "surge", "ath", "all-time high", "adoption",
                  "approved", "launch", "partnership", "growth", "soar"]
NEGATIVE_WORDS = ["crash", "dump", "ban", "hack", "exploit", "bankruptcy",
                  "collapse", "liquidation", "lawsuit", "plunge", "fear"]

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS news (
        id          TEXT PRIMARY KEY,
        title       TEXT NOT NULL,
        summary     TEXT,
        url         TEXT,
        source      TEXT,
        published   TEXT,
        fetched_at  TEXT,
        score       INTEGER DEFAULT 0,
        keywords    TEXT,
        sentiment   TEXT,
        read        INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fear_greed (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        value       INTEGER,
        label       TEXT,
        fetched_at  TEXT
    )
    """,
)


# ─────────────────────────────────────────────────────────────
# Base
# ─────────────────────────────────────────────────────────────

def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    """Ouvre la base en écriture, en créant fichier et schéma au besoin."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    for statement in SCHEMA:
        connection.execute(statement)
    connection.commit()
    return connection


def connect(path: Path = DB_PATH) -> Optional[sqlite3.Connection]:
    """Ouvre la base en lecture seule, ou `None` si elle n'existe pas.

    Le mode lecture seule est ce qui permet à un panneau d'interroger la
    base pendant que le collecteur y écrit, sans risquer de la créer
    vide au passage.
    """
    if not path.exists():
        return None
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def article_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}{title}".encode()).hexdigest()


def save_article(connection: sqlite3.Connection, article: dict) -> bool:
    """Insère l'article. Retourne `False` s'il était déjà en base."""
    try:
        connection.execute(
            """
            INSERT INTO news (id, title, summary, url, source, published,
                              fetched_at, score, keywords, sentiment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id(article.get("url", ""), article["title"]),
                article["title"],
                article.get("summary", ""),
                article.get("url", ""),
                article.get("source", ""),
                article.get("published", ""),
                datetime.now(timezone.utc).isoformat(),
                article.get("score", 0),
                json.dumps(article.get("keywords", [])),
                article.get("sentiment", "neutral"),
            ),
        )
        connection.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# ─────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────

def score_article(title: str, summary: str = "") -> tuple[int, list[str]]:
    """Score 0-100 et mots-clés reconnus dans le titre et le résumé."""
    text = f"{title} {summary}".lower()
    total = 0
    found: list[str] = []
    for keyword, weight in KEYWORDS.items():
        if keyword in text:
            total += weight
            found.append(keyword)
    return min(total, 100), found


def detect_sentiment(title: str, summary: str = "") -> str:
    """`bullish`, `bearish` ou `neutral`, par comptage de vocabulaire."""
    text = f"{title} {summary}".lower()
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    if positive > negative:
        return "bullish"
    if negative > positive:
        return "bearish"
    return "neutral"


# ─────────────────────────────────────────────────────────────
# Collecte
# ─────────────────────────────────────────────────────────────

Callback = Optional[Callable[..., Any]]


def collect_rss(
    connection: sqlite3.Connection,
    on_new: Callback = None,
    on_error: Callback = None,
) -> int:
    """Récupère, score et stocke les flux RSS. Retourne le nombre de nouveaux.

    Un flux en échec est signalé à `on_error(nom, exception)` et ignoré :
    une source indisponible ne doit pas priver la base des autres.
    """
    new_count = 0
    for entry in sources.fetch_rss_entries(on_error=on_error):
        score, keywords = score_article(entry["title"], entry["summary"])
        if score < MIN_SCORE:
            continue

        article = {
            **entry,
            "summary": entry["summary"][:500],
            "score": score,
            "keywords": keywords,
            "sentiment": detect_sentiment(entry["title"], entry["summary"]),
        }
        if save_article(connection, article):
            new_count += 1
            if on_new:
                on_new(article, entry["source"])
    return new_count


def collect_cryptopanic(
    connection: sqlite3.Connection,
    api_key: str,
    on_new: Callback = None,
    on_error: Callback = None,
) -> int:
    """Idem pour CryptoPanic, dont les votes affinent score et sentiment.

    Sans clé, la source est simplement inactive et la fonction retourne 0.
    """
    try:
        posts = sources.fetch_cryptopanic_posts(api_key)
    except Exception as exc:  # noqa: BLE001
        if on_error:
            on_error("CryptoPanic", exc)
        return 0

    new_count = 0
    for post in posts:
        votes = post["votes"]
        # Le vote de la communauté vaut mieux qu'un mot-clé : il fait
        # monter le score, et son sentiment prime sur le lexical.
        bonus = min(votes.get("important", 0) * 5 + votes.get("liked", 0) * 2, 30)
        score, keywords = score_article(post["title"])
        score = min(score + bonus, 100)
        if score < MIN_SCORE:
            continue

        if votes.get("bullish", 0) > votes.get("bearish", 0):
            sentiment = "bullish"
        elif votes.get("bearish", 0) > votes.get("bullish", 0):
            sentiment = "bearish"
        else:
            sentiment = detect_sentiment(post["title"])

        article = {key: value for key, value in post.items() if key != "votes"}
        article.update(score=score, keywords=keywords, sentiment=sentiment)

        if save_article(connection, article):
            new_count += 1
            if on_new:
                on_new(article, "CryptoPanic")
    return new_count


def record_fear_greed(
    connection: sqlite3.Connection, on_error: Callback = None
) -> Optional[dict]:
    """Relève l'indice Fear & Greed et l'historise."""
    data = sources.fetch_fear_greed()
    if data is None:
        if on_error:
            on_error("Fear & Greed", RuntimeError("source injoignable"))
        return None

    connection.execute(
        "INSERT INTO fear_greed (value, label, fetched_at) VALUES (?, ?, ?)",
        (data["value"], data["label"], datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    return data


# ─────────────────────────────────────────────────────────────
# Lecture
# ─────────────────────────────────────────────────────────────

def latest(limit: int = 12, path: Path = DB_PATH) -> list[sqlite3.Row]:
    """Dernières news stockées, les plus fraîches et les mieux notées d'abord.

    Retourne une liste vide si la base n'existe pas encore : un terminal
    lancé avant la première collecte affiche un panneau vide, pas une
    trace d'erreur.
    """
    connection = connect(path)
    if connection is None:
        return []
    try:
        return connection.execute(
            "SELECT title, source, score, sentiment, url, published"
            " FROM news ORDER BY fetched_at DESC, score DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        connection.close()


def last_fear_greed(path: Path = DB_PATH) -> Optional[sqlite3.Row]:
    """Dernier indice Fear & Greed historisé, ou `None`."""
    connection = connect(path)
    if connection is None:
        return None
    try:
        return connection.execute(
            "SELECT value, label, fetched_at FROM fear_greed"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()


# ─────────────────────────────────────────────────────────────
# Collecte périodique
# ─────────────────────────────────────────────────────────────

class NewsCollector:
    """Collecte en tâche de fond, pour un terminal qui remplit sa base.

    Le panneau news lisait une base qu'il ne remplissait pas : sans le
    timer systemd du tracker, il restait vide. Le collecteur comble ce
    trou, mais dans son propre thread — attendre six flux RSS dans un
    callback Dash figerait le panneau plusieurs secondes.

    L'état de la dernière tournée est publié dans `status`, ce qui permet
    à l'interface de dire quand elle a regardé pour la dernière fois, et
    de le dire aussi quand ça échoue.
    """

    def __init__(self, interval: float = 900, api_key: str = ""):
        self.interval = interval
        self.api_key = api_key
        self.status: dict[str, Any] = {
            "running": False, "last_run": None, "new": 0, "error": None,
        }
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> Optional[threading.Thread]:
        """Démarre la boucle. Idempotent."""
        if self._thread is not None:
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()

    def collect_once(self, connection: sqlite3.Connection) -> int:
        """Une tournée complète : RSS, CryptoPanic, Fear & Greed."""
        errors: list[str] = []

        def note(name: str, exc: Exception) -> None:
            errors.append(f"{name}: {exc}")

        new = collect_rss(connection, on_error=note)
        new += collect_cryptopanic(connection, self.api_key, on_error=note)
        record_fear_greed(connection, on_error=note)

        self.status.update(
            last_run=time.time(), new=new,
            error="; ".join(errors)[:200] or None,
        )
        return new

    def _loop(self) -> None:
        self.status["running"] = True
        try:
            connection = init_db()
        except Exception as exc:  # noqa: BLE001
            self.status.update(running=False, error=f"base : {exc}")
            return
        try:
            while not self._stop.is_set():
                try:
                    self.collect_once(connection)
                except Exception as exc:  # noqa: BLE001
                    # Une tournée ratée ne doit pas arrêter les suivantes :
                    # le réseau revient, la boucle doit être encore là.
                    self.status.update(last_run=time.time(), error=str(exc)[:200])
                self._stop.wait(self.interval)
        finally:
            connection.close()
            self.status["running"] = False
