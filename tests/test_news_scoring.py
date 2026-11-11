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



def test_la_collecte_se_reveille_avec_la_machine():
    """`nudge` devance l'attente de la tournée : au réveil, la collecte
    n'attend plus la fin de son quart d'heure, que le sommeil n'a pas fait
    avancer — dix minutes de matinée sur un fil de six heures."""
    import threading
    import time as horloge

    collecteur = newsdb.NewsCollector(interval=3600)
    tournees = []
    collecteur.collect_once = lambda connexion: tournees.append(horloge.time())
    original = newsdb.init_db
    newsdb.init_db = lambda *a, **k: type("C", (), {"close": lambda self: None})()
    try:
        collecteur.start()
        debut = horloge.monotonic()
        while len(tournees) < 1 and horloge.monotonic() - debut < 3:
            horloge.sleep(0.01)
        collecteur.nudge()
        while len(tournees) < 2 and horloge.monotonic() - debut < 3:
            horloge.sleep(0.01)
        assert len(tournees) == 2, "le réveil devait lancer une tournée"
        collecteur.stop()
        collecteur._thread.join(timeout=2)
        assert not collecteur._thread.is_alive(), "l'arrêt doit couper l'attente"
    finally:
        newsdb.init_db = original
    print("  ✓ une tournée au réveil, sans attendre l'intervalle ; l'arrêt coupe")


def test_l_age_de_la_collecte_tient_dans_le_titre():
    """« il y a 345 min » poussait le titre de la cellule contre ses
    onglets : l'âge s'écrit court, et un fil en retard se dit."""
    import time as horloge
    from terminal.panels import news as panneau

    assert [panneau._age(s) for s in (20, 600, 345 * 60, 3 * 86_400)] == [
        "à l'instant", "10 min", "5 h 45", "3 j"]
    badge = panneau._collecte_badge({"last_run": horloge.time() - 345 * 60,
                                     "new": 2})
    assert badge.children == "collecte 5 h 45 · +2"
    assert badge.style["color"] == panneau.C["orange"], "le retard doit se voir"
    assert "fil en retard" in badge.title
    frais = panneau._collecte_badge({"last_run": horloge.time() - 300})
    assert frais.style["color"] == panneau.C["muted"]
    print("  ✓ âge court, retard dit au-delà de deux intervalles")


RSS_VALIDE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>t</title>
<item><title>SEC approves spot Bitcoin ETF</title><link>https://exemple/a</link>
<description>&lt;p&gt;regulation&lt;/p&gt;</description></item>
</channel></rss>"""
RSS_VIDE = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
</channel></rss>"""


class _Reponse:
    def __init__(self, contenu: bytes, statut: int = 200):
        self.content, self.status_code = contenu, statut
        self.headers = {"content-type": "application/rss+xml"}

    def raise_for_status(self):
        import requests
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")


def test_un_flux_en_panne_se_dit():
    """feedparser, à qui l'URL était confiée, rendait un flux vide sur une
    erreur réseau, sans rien lever : un site en panne passait pour un site
    sans nouvelles. Le flux passe par `requests`, et chaque panne se nomme ;
    un flux bien formé mais vide n'en est pas une."""
    import requests

    def get(url, **kwargs):
        assert kwargs.get("timeout") == sources.RSS_TIMEOUT, "un délai, toujours"
        if url == "coupe":
            raise requests.ConnectionError("Connection refused")
        return {"bon": _Reponse(RSS_VALIDE), "vide": _Reponse(RSS_VIDE),
                "503": _Reponse(b"", 503),
                "html": _Reponse(b"<html><body>Just a moment...")}[url]

    original = sources._http.get
    sources._http.get = get
    erreurs = {}
    try:
        flux = [{"name": n, "url": n} for n in ("bon", "vide", "coupe", "503", "html")]
        articles = sources.fetch_rss_entries(
            flux, on_error=lambda nom, exc: erreurs.setdefault(nom, str(exc)))
    finally:
        sources._http.get = original
    assert [a["source"] for a in articles] == ["bon"]
    assert articles[0]["summary"] == "regulation", "le HTML est retiré"
    assert set(erreurs) == {"coupe", "503", "html"}, erreurs
    assert "503" in erreurs["503"] and "illisible" in erreurs["html"]
    print("  ✓ panne réseau, statut HTTP et page non RSS nommés ; flux vide toléré")


def test_une_source_en_defaut_ne_met_pas_la_collecte_en_echec():
    """Une seule lecture ratée du Fear & Greed affichait « collecte en
    échec » en rouge, les articles arrivés : l'échec est réservé à une
    tournée qui n'a rien pu lire, le reste se compte à part, source par
    source — et le panneau SOURCES leur donne une ligne chacune."""
    import time as horloge

    import requests
    from btcterm.hub import MarketHub
    from terminal.panels import news as panneau

    flux = [f["name"] for f in sources.RSS_FEEDS]
    en_panne: set[str] = {flux[0]}

    def rss(on_error=None, **kwargs):
        for nom in en_panne & set(flux):
            on_error(nom, requests.ConnectionError("Connection refused"))
        return []

    def indice():
        if "Fear & Greed" in en_panne:
            raise requests.Timeout("Read timed out")
        return {"value": 29, "label": "Fear"}

    originaux = sources.fetch_rss_entries, sources.fetch_fear_greed_now
    sources.fetch_rss_entries, sources.fetch_fear_greed_now = rss, indice
    try:
        with tempfile.TemporaryDirectory() as dossier:
            connexion = newsdb.init_db(Path(dossier) / "news.db")
            collecteur = newsdb.NewsCollector()
            try:
                # Un flux et l'indice en défaut : la collecte a eu lieu.
                en_panne.add("Fear & Greed")
                collecteur.collect_once(connexion)
                statut = collecteur.status
                assert statut["error"] is None, statut["error"]
                assert set(statut["failures"]) == {flux[0], "Fear & Greed"}
                assert "Read timed out" in statut["failures"]["Fear & Greed"]
                etats = collecteur.sources
                assert set(etats) == set(flux) | {"Fear & Greed"}, \
                    "sans clé, CryptoPanic n'est pas une source"
                debut = etats[flux[0]]["since"]
                assert etats[flux[0]]["ok_at"] is None and debut is not None
                assert etats[flux[1]]["error"] is None and etats[flux[1]]["ok_at"]

                badge = panneau._collecte_badge(statut)
                assert "échec" not in str(badge) and "⚠ 2" in str(badge)
                assert flux[0] in badge.title and "Fear & Greed" in badge.title

                # Les lignes du panneau SOURCES, et la tâche restée « ok ».
                hub = MarketHub(collect_news=False, keep_journal=False)
                hub.collect_news, hub.news = True, collecteur
                sections = hub.sources_status()
                lignes = {r["name"]: r for r in sections["Sources REST"]}
                assert lignes[f"News · {flux[0]}"]["state"] == "panne"
                assert lignes[f"News · {flux[0]}"]["age"] is None
                assert lignes[f"News · {flux[1]}"]["state"] == "ok"
                tache = {r["name"]: r for r in sections["Tâches de fond"]}
                tache = tache["Collecte des news"]
                assert tache["state"] == "ok" and f"sans {flux[0]}" in tache["detail"]

                # La panne dure : son début reste le premier constat.
                collecteur.collect_once(connexion)
                assert collecteur.sources[flux[0]]["since"] == debut

                # Plus aucun flux : là, la collecte est en échec.
                en_panne.update(flux)
                collecteur.collect_once(connexion)
                assert collecteur.status["error"].startswith("aucun flux RSS")
                assert "échec" in str(panneau._collecte_badge(collecteur.status))

                # Tout revient : plus rien en défaut.
                en_panne.clear()
                collecteur.collect_once(connexion)
                assert collecteur.status["error"] is None
                assert collecteur.status["failures"] == {}
                assert all(e["error"] is None and e["since"] is None
                           for e in collecteur.sources.values())
                badge = panneau._collecte_badge(collecteur.status)
                assert badge.children == "collecte à l'instant"
                assert horloge.time() - collecteur.sources[flux[0]]["ok_at"] < 5
            finally:
                connexion.close()
    finally:
        sources.fetch_rss_entries, sources.fetch_fear_greed_now = originaux
    print("  ✓ source en défaut comptée à part, échec réservé à une tournée vide")


def test_la_liste_ne_repart_que_si_elle_change():
    """L'empreinte de la liste suit ce qui la change à l'écran — lien, score,
    sentiment — et rien d'autre : la liste ne repart vers le navigateur que si
    elle diffère de celle qu'il affiche."""
    from terminal.panels.news import news_signature

    ligne = {"url": "https://exemple/1", "score": 60, "sentiment": "bullish",
             "title": "titre", "source": "Test"}
    une = news_signature([ligne])
    assert une == news_signature([dict(ligne, title="autre titre tronqué")])
    assert une != news_signature([dict(ligne, score=61)])
    assert une != news_signature([ligne, dict(ligne, url="https://exemple/2")])
    assert news_signature([]) == ""
    print("  ✓ empreinte : lien, score, sentiment")

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nBase de news — {len(tests)} vérifications\n" + "─" * 60)
    for test_fn in tests:
        print(f"\n{test_fn.__name__}")
        test_fn()
    print("\n" + "─" * 60)
    print("Le scoring et la collecte sont sans régression.\n")
