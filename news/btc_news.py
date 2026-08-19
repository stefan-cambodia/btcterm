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
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Le socle vit à la racine du dépôt, un niveau au-dessus de ce fichier.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import newsdb  # noqa: E402
from btcterm.newsdb import DB_PATH, MIN_SCORE, init_db  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────────────

# Schéma, scoring et collecte vivent dans `btcterm/newsdb.py` : le
# terminal remplit la même base avec les mêmes règles, et ce fichier ne
# garde que ce qui lui est propre — la ligne de commande et l'affichage.

# Clé CryptoPanic (optionnelle – laisser vide pour n'utiliser que les RSS)
CRYPTOPANIC_API_KEY = ""

# ── Sources ───────────────────────────────────────────────────────────────────

def _report(article: dict, label: str) -> None:
    icon = {"bullish": "📈", "bearish": "📉"}.get(article["sentiment"], "➡️")
    print(f"  {icon} [{article['score']:3d}] {label}: {article['title'][:80]}")


def _warn(name: str, exc: Exception) -> None:
    print(f"  ⚠️  Erreur {name}: {exc}", file=sys.stderr)


def fetch_rss(conn: sqlite3.Connection, verbose: bool = False) -> int:
    """Flux RSS : collecte du socle, compte rendu en ligne de commande."""
    return newsdb.collect_rss(
        conn, on_new=_report if verbose else None, on_error=_warn
    )


def fetch_cryptopanic(conn: sqlite3.Connection, api_key: str,
                      verbose: bool = False) -> int:
    """Idem pour CryptoPanic."""
    return newsdb.collect_cryptopanic(
        conn, api_key, on_new=_report if verbose else None, on_error=_warn
    )


def fetch_fear_greed(conn: sqlite3.Connection) -> Optional[dict]:
    """Idem pour l'indice Fear & Greed, historisé en base."""
    return newsdb.record_fear_greed(conn, on_error=_warn)


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
