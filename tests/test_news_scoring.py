#!/usr/bin/env python3
"""
Non-régression de l'extraction de la base de news.

Le scoring, le schéma SQLite et la collecte vivaient dans
`news/btc_news.py` ; ils vivent maintenant dans `btcterm/newsdb.py`, où
le terminal les partage avec le tracker. Ce test rejoue les
implémentations d'avant l'extraction et vérifie que le socle attribue
exactement les mêmes scores et les mêmes sentiments.

Il vérifie aussi ce que l'extraction rendait possible et qui n'était
couvert nulle part : la collecte filtre bien sous le seuil, et ne
réinsère pas deux fois le même article.

Aucun réseau n'est touché : les flux RSS sont remplacés par une liste en
dur, et la base par un fichier temporaire.

Lancement :
    python tests/test_news_scoring.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import newsdb, sources  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Implémentations historiques (copies conformes, avant extraction)
# ─────────────────────────────────────────────────────────────

LEGACY_KEYWORDS: dict[str, int] = {
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


def legacy_score_article(title, summary=""):
    text = (title + " " + summary).lower()
    total = 0
    found = []
    for kw, weight in LEGACY_KEYWORDS.items():
        if kw in text:
            total += weight
            found.append(kw)
    return min(total, 100), found


def legacy_detect_sentiment(title, summary=""):
    text = (title + " " + summary).lower()
    positive = ["rally", "bull", "surge", "ath", "all-time high", "adoption",
                "approved", "launch", "partnership", "growth", "soar"]
    negative = ["crash", "dump", "ban", "hack", "exploit", "bankruptcy",
                "collapse", "liquidation", "lawsuit", "plunge", "fear"]
    p = sum(1 for w in positive if w in text)
    n = sum(1 for w in negative if w in text)
    if p > n:
        return "bullish"
    if n > p:
        return "bearish"
    return "neutral"


# ─────────────────────────────────────────────────────────────
# Corpus : titres réalistes, choisis pour exercer les deux bornes
# (plafond à 100, seuil à MIN_SCORE) et les trois sentiments.
# ─────────────────────────────────────────────────────────────

CORPUS = [
    ("SEC approves spot Bitcoin ETF from BlackRock and Fidelity",
     "The regulation clears the way for institutional adoption"),
    ("Bitcoin crashes 12% after exchange hack and mass liquidation", ""),
    ("Lightning Network mempool grows quietly", "taproot adoption continues"),
    ("MicroStrategy adds to its treasury as inflation data lands", "CPI surprise"),
    ("A quiet day on the markets", "nothing much happened at all"),
    ("Bitcoin all-time high as halving rally meets record exchange outflow",
     "whale accumulation, ATH, bull run"),
    ("Congress debates legislation on stablecoins", "senate hearing next week"),
]


def test_scores_identiques():
    for title, summary in CORPUS:
        attendu, mots_attendus = legacy_score_article(title, summary)
        obtenu, mots_obtenus = newsdb.score_article(title, summary)
        assert obtenu == attendu, f"{title!r} : {obtenu} au lieu de {attendu}"
        assert sorted(mots_obtenus) == sorted(mots_attendus), title
    print(f"  ✓ scores identiques sur {len(CORPUS)} titres")


def test_sentiments_identiques():
    for title, summary in CORPUS:
        attendu = legacy_detect_sentiment(title, summary)
        obtenu = newsdb.detect_sentiment(title, summary)
        assert obtenu == attendu, f"{title!r} : {obtenu} au lieu de {attendu}"
    print(f"  ✓ sentiments identiques sur {len(CORPUS)} titres")


def test_pondérations_inchangées():
    assert newsdb.KEYWORDS == LEGACY_KEYWORDS, "la table de pondérations a changé"
    assert newsdb.MIN_SCORE == 30
    print(f"  ✓ {len(newsdb.KEYWORDS)} pondérations et seuil à {newsdb.MIN_SCORE}")


def test_collecte_filtre_et_dédoublonne():
    """Ce que l'extraction rend enfin testable, sans réseau ni base réelle."""
    entrees = [
        {"title": "SEC approves spot Bitcoin ETF", "summary": "regulation",
         "url": "https://exemple/1", "source": "Test", "published": ""},
        {"title": "A quiet day on the markets", "summary": "nothing happened",
         "url": "https://exemple/2", "source": "Test", "published": ""},
    ]
    original = sources.fetch_rss_entries
    sources.fetch_rss_entries = lambda *a, **k: entrees
    try:
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "news.db"
            connection = newsdb.init_db(chemin)
            try:
                nouveaux = newsdb.collect_rss(connection)
                assert nouveaux == 1, f"{nouveaux} articles retenus au lieu d'un"

                total = connection.execute("SELECT COUNT(*) FROM news").fetchone()[0]
                assert total == 1, "l'article sous le seuil a été stocké"

                # Deuxième passage : mêmes articles, aucune insertion.
                assert newsdb.collect_rss(connection) == 0, "doublon inséré"
            finally:
                connection.close()

            lignes = newsdb.latest(5, chemin)
            assert len(lignes) == 1 and lignes[0]["source"] == "Test"
            assert newsdb.latest(5, Path(dossier) / "absente.db") == []
    finally:
        sources.fetch_rss_entries = original
    print("  ✓ collecte : seuil respecté, doublons écartés, base absente tolérée")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nBase de news — {len(tests)} vérifications\n" + "─" * 60)
    for test_fn in tests:
        print(f"\n{test_fn.__name__}")
        test_fn()
    print("\n" + "─" * 60)
    print("Le scoring et la collecte sont sans régression.\n")
