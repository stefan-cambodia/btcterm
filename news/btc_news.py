#!/usr/bin/env python3
"""
BTC News Tracker
Récupère et stocke les news ayant un impact sur le cours du BTC.
Sources : RSS (CoinDesk, CoinTelegraph, Decrypt, Bitcoin Magazine)
          + CryptoPanic API (optionnel, clé gratuite sur cryptopanic.com)
          + Fear & Greed Index
Stockage : SQLite local (~/.btc_news/news.db)
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser
import requests

# ── Configuration ────────────────────────────────────────────────────────────

DB_DIR  = Path.home() / ".btc_news"
DB_PATH = DB_DIR / "news.db"
CFG_PATH = DB_DIR / "config.json"

# Seuil de score minimum pour qu'une news soit conservée (0-100)
MIN_SCORE = 30

# Clé CryptoPanic (optionnelle – laisser vide pour n'utiliser que les RSS)
CRYPTOPANIC_API_KEY = ""

# ── Mots-clés et pondérations ────────────────────────────────────────────────

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
    "gold":            10, "oil":             8,

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

# Sources RSS
RSS_FEEDS: list[dict] = [
    {"name": "CoinDesk",        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph",   "url": "https://cointelegraph.com/rss"},
    {"name": "Decrypt",         "url": "https://decrypt.co/feed"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/.rss/full/"},
    {"name": "The Block",       "url": "https://www.theblock.co/rss.xml"},
    {"name": "CryptoSlate",     "url": "https://cryptoslate.com/feed/"},
]

# ── Base de données ───────────────────────────────────────────────────────────

def init_db() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
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
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fear_greed (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            value       INTEGER,
            label       TEXT,
            fetched_at  TEXT
        )
    """)
    conn.commit()
    return conn


def article_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}{title}".encode()).hexdigest()


def save_article(conn: sqlite3.Connection, article: dict) -> bool:
    """Retourne True si l'article est nouveau."""
    aid = article_id(article.get("url", ""), article["title"])
    try:
        conn.execute("""
            INSERT INTO news (id, title, summary, url, source, published,
                              fetched_at, score, keywords, sentiment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            aid,
            article["title"],
            article.get("summary", ""),
            article.get("url", ""),
            article.get("source", ""),
            article.get("published", ""),
            datetime.now(timezone.utc).isoformat(),
            article.get("score", 0),
            json.dumps(article.get("keywords", [])),
            article.get("sentiment", "neutral"),
        ))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # déjà en base

# ── Scoring ───────────────────────────────────────────────────────────────────

def score_article(title: str, summary: str = "") -> tuple[int, list[str]]:
    """Retourne (score, [mots-clés trouvés])."""
    text = (title + " " + summary).lower()
    total = 0
    found: list[str] = []
    for kw, weight in KEYWORDS.items():
        if kw in text:
            total += weight
            found.append(kw)
    return min(total, 100), found


def detect_sentiment(title: str, summary: str = "") -> str:
    text = (title + " " + summary).lower()
    positive = ["rally", "bull", "surge", "ath", "all-time high", "adoption",
                 "approved", "launch", "partnership", "growth", "soar"]
    negative = ["crash", "dump", "ban", "hack", "exploit", "bankruptcy",
                 "collapse", "liquidation", "lawsuit", "plunge", "fear"]
    p = sum(1 for w in positive if w in text)
    n = sum(1 for w in negative if w in text)
    if p > n:   return "bullish"
    if n > p:   return "bearish"
    return "neutral"

# ── Sources ───────────────────────────────────────────────────────────────────

def fetch_rss(conn: sqlite3.Connection, verbose: bool = False) -> int:
    new_count = 0
    for feed_info in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries:
                title   = entry.get("title", "").strip()
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
                url     = entry.get("link", "")
                pub     = entry.get("published", "")

                score, kws = score_article(title, summary)
                if score < MIN_SCORE:
                    continue

                article = {
                    "title":    title,
                    "summary":  summary[:500],
                    "url":      url,
                    "source":   feed_info["name"],
                    "published": pub,
                    "score":    score,
                    "keywords": kws,
                    "sentiment": detect_sentiment(title, summary),
                }
                if save_article(conn, article):
                    new_count += 1
                    if verbose:
                        icon = {"bullish": "📈", "bearish": "📉"}.get(article["sentiment"], "➡️")
                        print(f"  {icon} [{score:3d}] {feed_info['name']}: {title[:80]}")
        except Exception as e:
            print(f"  ⚠️  Erreur RSS {feed_info['name']}: {e}", file=sys.stderr)
    return new_count


def fetch_cryptopanic(conn: sqlite3.Connection, api_key: str, verbose: bool = False) -> int:
    """Récupère les news filtrées BTC depuis CryptoPanic (clé gratuite)."""
    if not api_key:
        return 0
    new_count = 0
    url = (
        f"https://cryptopanic.com/api/v1/posts/"
        f"?auth_token={api_key}&currencies=BTC&filter=important&public=true"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("results", []):
            title   = item.get("title", "").strip()
            summary = ""
            url_    = item.get("url", "")
            pub     = item.get("published_at", "")
            votes   = item.get("votes", {})
            # Bonus de score selon les votes CryptoPanic
            bonus   = min((votes.get("important", 0) * 5 +
                           votes.get("liked", 0) * 2), 30)
            score, kws = score_article(title, summary)
            score = min(score + bonus, 100)
            if score < MIN_SCORE:
                continue

            # Sentiment depuis les votes
            if votes.get("bullish", 0) > votes.get("bearish", 0):
                sentiment = "bullish"
            elif votes.get("bearish", 0) > votes.get("bullish", 0):
                sentiment = "bearish"
            else:
                sentiment = detect_sentiment(title)

            article = {
                "title":    title,
                "summary":  summary,
                "url":      url_,
                "source":   f"CryptoPanic/{item.get('source', {}).get('title', '')}",
                "published": pub,
                "score":    score,
                "keywords": kws,
                "sentiment": sentiment,
            }
            if save_article(conn, article):
                new_count += 1
                if verbose:
                    icon = {"bullish": "📈", "bearish": "📉"}.get(sentiment, "➡️")
                    print(f"  {icon} [{score:3d}] CryptoPanic: {title[:80]}")
    except Exception as e:
        print(f"  ⚠️  Erreur CryptoPanic: {e}", file=sys.stderr)
    return new_count


def fetch_fear_greed(conn: sqlite3.Connection) -> Optional[dict]:
    """Récupère le Fear & Greed Index."""
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        conn.execute(
            "INSERT INTO fear_greed (value, label, fetched_at) VALUES (?, ?, ?)",
            (int(data["value"]), data["value_classification"],
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        return data
    except Exception as e:
        print(f"  ⚠️  Erreur Fear&Greed: {e}", file=sys.stderr)
        return None

# ── Affichage ─────────────────────────────────────────────────────────────────

SENTIMENT_COLOR = {
    "bullish": "\033[32m",   # vert
    "bearish": "\033[31m",   # rouge
    "neutral": "\033[33m",   # jaune
}
RESET = "\033[0m"
BOLD  = "\033[1m"

SCORE_BARS = {90: "██████████", 70: "████████  ", 50: "██████    ",
              30: "████      ",  0: "██        "}


def score_bar(score: int) -> str:
    for threshold, bar in SCORE_BARS.items():
        if score >= threshold:
            return bar
    return "██        "


def print_news(rows: list, limit: int = 20) -> None:
    if not rows:
        print("  Aucune news trouvée.")
        return
    for row in rows[:limit]:
        sentiment = row["sentiment"]
        color = SENTIMENT_COLOR.get(sentiment, "")
        icon  = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(sentiment, "➡️")
        pub   = row["published"][:16] if row["published"] else "?"
        unread = "" if row["read"] else f"{BOLD}[NEW]{RESET} "
        print(
            f"\n{color}{BOLD}[{row['score']:3d}]{RESET} {score_bar(row['score'])} "
            f"{icon}  {color}{unread}{row['title'][:90]}{RESET}"
        )
        print(f"     📰 {row['source']}  ·  🕒 {pub}")
        if row["summary"]:
            print(f"     {row['summary'][:120]}…")
        kws = json.loads(row["keywords"] or "[]")
        if kws:
            print(f"     🏷  {', '.join(kws[:6])}")
        print(f"     🔗 {row['url']}")


def print_fear_greed(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT value, label, fetched_at FROM fear_greed ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return
    v = row["value"]
    if   v >= 75: emoji = "🤑"
    elif v >= 55: emoji = "😊"
    elif v >= 45: emoji = "😐"
    elif v >= 25: emoji = "😨"
    else:         emoji = "😱"
    print(f"\n{BOLD}Fear & Greed Index :{RESET} {emoji}  {v}/100  ({row['label']})")

# ── CLI ───────────────────────────────────────────────────────────────────────

def cmd_fetch(args, conn: sqlite3.Connection) -> None:
    api_key = args.api_key or CRYPTOPANIC_API_KEY
    print(f"\n{BOLD}🔄 Récupération des news BTC…{RESET}")

    print("\n📡 RSS feeds…")
    n_rss = fetch_rss(conn, verbose=args.verbose)

    n_cp = 0
    if api_key:
        print("\n📡 CryptoPanic…")
        n_cp = fetch_cryptopanic(conn, api_key, verbose=args.verbose)

    print("\n📡 Fear & Greed Index…")
    fg = fetch_fear_greed(conn)

    print(f"\n{BOLD}✅ Terminé.{RESET} {n_rss + n_cp} nouvelles news stockées.")
    if fg:
        print_fear_greed(conn)


def cmd_list(args, conn: sqlite3.Connection) -> None:
    sentiment_filter = ""
    params: list = []
    if args.sentiment:
        sentiment_filter = "AND sentiment = ?"
        params.append(args.sentiment)

    min_score = args.min_score
    params.insert(0, min_score)

    rows = conn.execute(f"""
        SELECT * FROM news
        WHERE score >= ? {sentiment_filter}
        ORDER BY fetched_at DESC
        LIMIT 50
    """, params).fetchall()

    print_fear_greed(conn)
    print(f"\n{BOLD}📋 Dernières news BTC (score ≥ {min_score}){RESET}\n{'─'*60}")
    print_news(rows, limit=args.limit)

    # Marquer comme lues
    if not args.unread_only:
        conn.execute("UPDATE news SET read = 1 WHERE read = 0")
        conn.commit()


def cmd_unread(args, conn: sqlite3.Connection) -> None:
    rows = conn.execute("""
        SELECT * FROM news WHERE read = 0
        ORDER BY score DESC, fetched_at DESC
        LIMIT 30
    """).fetchall()
    print(f"\n{BOLD}🆕 News non lues ({len(rows)}){RESET}\n{'─'*60}")
    print_news(rows)
    conn.execute("UPDATE news SET read = 1 WHERE read = 0")
    conn.commit()


def cmd_search(args, conn: sqlite3.Connection) -> None:
    q = f"%{args.query.lower()}%"
    rows = conn.execute("""
        SELECT * FROM news
        WHERE lower(title) LIKE ? OR lower(summary) LIKE ?
        ORDER BY score DESC, fetched_at DESC
        LIMIT 20
    """, (q, q)).fetchall()
    print(f"\n{BOLD}🔍 Résultats pour « {args.query} »{RESET}\n{'─'*60}")
    print_news(rows)


def cmd_stats(args, conn: sqlite3.Connection) -> None:
    total  = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    unread = conn.execute("SELECT COUNT(*) FROM news WHERE read = 0").fetchone()[0]
    bull   = conn.execute("SELECT COUNT(*) FROM news WHERE sentiment='bullish'").fetchone()[0]
    bear   = conn.execute("SELECT COUNT(*) FROM news WHERE sentiment='bearish'").fetchone()[0]
    avg_s  = conn.execute("SELECT AVG(score) FROM news").fetchone()[0] or 0
    last   = conn.execute("SELECT fetched_at FROM news ORDER BY fetched_at DESC LIMIT 1").fetchone()

    print(f"\n{BOLD}📊 Statistiques{RESET}")
    print(f"  Total news stockées : {total}")
    print(f"  Non lues            : {unread}")
    print(f"  Score moyen         : {avg_s:.1f}/100")
    print(f"  📈 Bullish          : {bull}")
    print(f"  📉 Bearish          : {bear}")
    print(f"  Dernière fetch      : {last[0][:19] if last else 'jamais'}")
    print(f"  Base de données     : {DB_PATH}")
    print_fear_greed(conn)


def cmd_watch(args, conn: sqlite3.Connection) -> None:
    """Mode surveillance : rafraîchit toutes les N minutes."""
    interval = args.interval * 60
    api_key  = args.api_key or CRYPTOPANIC_API_KEY
    print(f"{BOLD}👁  Mode surveillance — rafraîchissement toutes les {args.interval} min{RESET}")
    print("Ctrl+C pour arrêter.\n")
    while True:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Récupération…")
        fetch_rss(conn, verbose=True)
        if api_key:
            fetch_cryptopanic(conn, api_key, verbose=True)
        fetch_fear_greed(conn)
        n = conn.execute("SELECT COUNT(*) FROM news WHERE read=0").fetchone()[0]
        print(f"→ {n} news non lues. Prochain refresh dans {args.interval} min.\n")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nArrêt du mode surveillance.")
            break

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="🪙  BTC News Tracker – news à impact sur le cours du Bitcoin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  btc_news.py fetch                   # Récupérer les nouvelles news
  btc_news.py fetch --api-key MYKEY   # Avec CryptoPanic
  btc_news.py list                    # Afficher les dernières (≥30)
  btc_news.py list --min-score 60     # Seulement les importantes
  btc_news.py list --sentiment bearish
  btc_news.py unread                  # News non lues
  btc_news.py search "etf"            # Rechercher
  btc_news.py stats                   # Statistiques
  btc_news.py watch --interval 30     # Surveillance toutes les 30 min
        """
    )
    parser.add_argument("--api-key", help="Clé API CryptoPanic (gratuite sur cryptopanic.com)")
    sub = parser.add_subparsers(dest="command")

    # fetch
    p_fetch = sub.add_parser("fetch", help="Récupérer les news")
    p_fetch.add_argument("-v", "--verbose", action="store_true")
    p_fetch.add_argument("--api-key")

    # list
    p_list = sub.add_parser("list", help="Afficher les news")
    p_list.add_argument("--min-score", type=int, default=MIN_SCORE)
    p_list.add_argument("--sentiment", choices=["bullish", "bearish", "neutral"])
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--unread-only", action="store_true")

    # unread
    sub.add_parser("unread", help="Afficher uniquement les news non lues")

    # search
    p_search = sub.add_parser("search", help="Rechercher dans les news")
    p_search.add_argument("query")

    # stats
    sub.add_parser("stats", help="Statistiques de la base")

    # watch
    p_watch = sub.add_parser("watch", help="Mode surveillance en continu")
    p_watch.add_argument("--interval", type=int, default=30, help="Minutes entre chaque refresh (défaut: 30)")
    p_watch.add_argument("--api-key")

    args = parser.parse_args()

    conn = init_db()

    dispatch = {
        "fetch":  cmd_fetch,
        "list":   cmd_list,
        "unread": cmd_unread,
        "search": cmd_search,
        "stats":  cmd_stats,
        "watch":  cmd_watch,
    }

    if args.command in dispatch:
        dispatch[args.command](args, conn)
    else:
        parser.print_help()

    conn.close()


if __name__ == "__main__":
    main()
