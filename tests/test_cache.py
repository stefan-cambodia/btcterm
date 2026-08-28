#!/usr/bin/env python3
"""
Le cache du hub : dernière valeur de secours, fraîcheur, pannes dites.

Le journal de séance a montré six heures et demie d'instantanés sans
financement ni open interest — Binance Futures muet après un réveil,
CoinGecko répondant à côté — sans une ligne dans le journal du service.
Trois choses sont vérifiées ici, sans réseau :

- le cache sert sa dernière valeur quand la source tombe, mais sait
  qu'elle est de secours (`stale`) ;
- la première panne d'une source et son rétablissement laissent une
  ligne de journal, une seule chacune, pas une par lecture ;
- l'instantané de marché n'écrit que des valeurs fraîches : une source
  en panne donne NULL, pas sa valeur d'il y a six heures ;
- un collecteur qui n'a rien obtenu lève, au lieu de rendre un vide
  qui se ferait mettre en cache comme une lecture réussie.

Lancement :
    python tests/test_cache.py
"""

import logging
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm import hub as hub_module, sources  # noqa: E402
from btcterm.hub import MarketHub, TTLCache  # noqa: E402
from btcterm.journal import Journal  # noqa: E402


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lignes = []

    def emit(self, record):
        self.lignes.append(record.getMessage())


def test_secours_et_fraicheur():
    cache = TTLCache()
    appels = []

    def source():
        appels.append(1)
        if len(appels) in (2, 3):
            raise ConnectionError("réseau coupé")
        return len(appels)

    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        assert cache.get("k", ttl=0, producer=source) == 1 and not cache.stale("k")
        # Deux pannes : la valeur de secours est servie, dite telle,
        # et la panne journalisée une seule fois.
        assert cache.get("k", ttl=0, producer=source) == 1 and cache.stale("k")
        assert cache.get("k", ttl=0, producer=source) == 1 and cache.stale("k")
        assert len(capture.lignes) == 1 and "k en panne" in capture.lignes[0], capture.lignes
        # Rétablissement : valeur neuve, fraîche, et une ligne de plus.
        assert cache.get("k", ttl=0, producer=source) == 4 and not cache.stale("k")
        assert len(capture.lignes) == 2 and "de nouveau servie" in capture.lignes[1]
        # Le cache ne disqualifie que ce qu'il a vu tomber : une clé jamais
        # lue n'est pas de secours ; une panne sans rien à servir lève, et
        # la clé est dite de secours.
        assert not cache.stale("jamais")
        try:
            cache.get("vide", ttl=0, producer=lambda: (_ for _ in ()).throw(OSError("x")))
        except OSError:
            pass
        else:
            raise AssertionError("sans valeur de secours, la panne doit remonter")
        assert cache.stale("vide")
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ secours servi mais non frais, panne et retour dits une fois")


def test_l_instantane_n_ecrit_que_le_frais():
    """Une source en panne donne NULL, pas sa valeur d'il y a six heures."""
    reponses = {"global": True, "oi": True, "perp": True}

    def faux_global():
        if not reponses["global"]:
            raise ConnectionError("CoinGecko")
        return {"total_cap_usd": 2.7e12, "total_volume_usd": 9e10,
                "shares": {"BTC": 59.0, "USDT": 6.0}}

    def faux_oi(symbol, period, limit):
        if not reponses["oi"]:
            raise ConnectionError("Binance")
        return pd.DataFrame({"time": [pd.Timestamp("2026-08-25")],
                             "oi": [1e5], "oi_usd": [8.4e9]})

    def faux_perp(symbol):
        if not reponses["perp"]:
            raise ConnectionError("Binance")
        return {"funding_rate": 6e-5}

    originaux = (sources.fetch_market_global, sources.fetch_open_interest,
                 sources.fetch_perp_snapshot)
    sources.fetch_market_global = faux_global
    sources.fetch_open_interest = faux_oi
    sources.fetch_perp_snapshot = faux_perp
    try:
        with tempfile.TemporaryDirectory() as tmp:
            hub = MarketHub(collect_news=False, keep_journal=False)
            hub.journal = Journal(Path(tmp) / "journal.db")
            hub.TTL_GLOBAL = hub.TTL_OPEN_INTEREST = hub.TTL_PERP = 0
            t0 = time.time()
            hub.record_market_snapshot(now=t0)
            # Binance tombe : CoinGecko s'écrit, le reste reste NULL —
            # alors que le cache, lui, sert encore l'OI et le financement.
            reponses["oi"] = reponses["perp"] = False
            assert hub.open_interest()["oi_usd"].iloc[-1] == 8.4e9, "secours servi au panneau"
            hub.record_market_snapshot(now=t0 + 300)
            # Tout tombe : rien ne s'écrit.
            reponses["global"] = False
            hub.record_market_snapshot(now=t0 + 600)
            rows = hub.journal.snapshots_between(0, t0 + 900)
            assert len(rows) == 2, len(rows)
            assert rows[0]["oi_usd"] == 8.4e9 and rows[0]["funding_rate"] == 6e-5
            assert rows[1]["btc_dominance"] == 59.0
            assert rows[1]["oi_usd"] is None and rows[1]["funding_rate"] is None
            hub.journal.close()
    finally:
        (sources.fetch_market_global, sources.fetch_open_interest,
         sources.fetch_perp_snapshot) = originaux
    print("  ✓ NULL pour la source en panne, même quand le cache la sert encore")


def test_le_vide_vaut_panne():
    """Un collecteur sans rien lève au lieu de rendre un vide cacheable."""
    class Reponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return []

    original = sources.requests.get
    sources.requests.get = lambda *a, **k: Reponse()
    try:
        for fetch in (lambda: sources.fetch_open_interest("BTCUSDT"),
                      lambda: sources.fetch_perp_snapshot("BTCUSDT")):
            try:
                fetch()
            except Exception as exc:
                assert "vide" in str(exc) or "injoignable" in str(exc), exc
            else:
                raise AssertionError("un résultat vide doit lever")
    finally:
        sources.requests.get = original
    print("  ✓ open interest vide et perpétuel sans réponse lèvent")


def test_la_panne_se_lit_en_une_ligne():
    """`brief_error` garde l'hôte et la cause, pas l'URL ni le pool."""
    dns = RuntimeError(
        "HTTPSConnectionPool(host='fapi.binance.com', port=443): Max retries "
        "exceeded with url: /fapi/v1/premiumIndex?symbol=BTCUSDT (Caused by "
        "NameResolutionError(\"HTTPSConnection(host='fapi.binance.com', "
        "port=443): Failed to resolve 'fapi.binance.com' ([Errno -2] Name or "
        "service not known)\"))")
    assert sources.brief_error(dns) == "fapi.binance.com : NameResolutionError"
    timeout = RuntimeError(
        "HTTPSConnectionPool(host='farside.co.uk', port=443): Read timed out. "
        "(read timeout=15)")
    assert sources.brief_error(timeout) == "farside.co.uk : Read timed out"
    http = RuntimeError("503 Server Error: Service Unavailable for url: "
                        "https://api.blockchain.info/stats")
    assert sources.brief_error(http, 30) == "503 Server Error: Service Unav"
    assert sources.brief_error(RuntimeError("")) == "RuntimeError"

    # Le perpétuel en compose deux : chacune reste lisible.
    class Panne:
        def raise_for_status(self): raise dns
        def json(self): return {}
    original = sources.requests.get
    sources.requests.get = lambda *a, **k: Panne()
    try:
        sources.fetch_perp_snapshot("BTCUSDT")
    except RuntimeError as exc:
        assert str(exc) == ("Binance Futures injoignable — premiumIndex : "
                            "fapi.binance.com : NameResolutionError ; "
                            "ratio : fapi.binance.com : NameResolutionError"), exc
    else:
        raise AssertionError("les deux endpoints en panne doivent lever")
    finally:
        sources.requests.get = original
    print("  ✓ hôte et cause, sans l'URL ni le pool")


def test_la_boucle_attend_le_reseau():
    """Au boot, la boucle d'observation attend le réseau au lieu de
    déclarer toutes les sources en panne au premier tour ; l'attente et
    sa fin tiennent en une ligne chacune, et l'arrêt du hub l'interrompt."""
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub.NETWORK_PROBE_EVERY = 0.01
    hub.NETWORK_WAIT = 1.0
    sondes = []

    def sonde():
        sondes.append(1)
        return len(sondes) >= 3
    hub._network_reachable = sonde

    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        assert hub._wait_for_network() is True
        assert len(sondes) == 3
        assert [l for l in capture.lignes if "absent au démarrage" in l], capture.lignes
        assert [l for l in capture.lignes if l.startswith("réseau présent après")]

        # Réseau présent d'emblée : rien à dire.
        capture.lignes.clear(); sondes.clear()
        sondes.extend([1, 1])
        assert hub._wait_for_network() is True and capture.lignes == []

        # Jamais de réseau : la boucle part quand même, et le dit.
        capture.lignes.clear()
        hub._network_reachable = lambda: False
        assert hub._wait_for_network() is False
        assert [l for l in capture.lignes if "part sans lui" in l], capture.lignes

        # L'arrêt du hub coupe l'attente sans attendre l'expiration.
        hub.NETWORK_WAIT = 60.0
        hub._observe_stop.set()
        debut = time.monotonic()
        assert hub._wait_for_network() is False
        assert time.monotonic() - debut < 1.0
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ la boucle d'observation attend le réseau, et le dit une fois")


def test_le_reveil_rattend_le_reseau():
    """Un tour dont l'heure murale a sauté est un réveil de la machine :
    la boucle rattend le réseau, comme au démarrage, et le dit avec la
    durée du sommeil ; un tour ordinaire ne sonde rien."""
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub.NETWORK_PROBE_EVERY = 0.01
    sondes = []

    def sonde():
        sondes.append(1)
        return len(sondes) >= 2
    hub._network_reachable = sonde

    capture = Capture()
    hub_module.log.addHandler(capture)
    try:
        # Un tour ordinaire, une seconde après le précédent : rien.
        avant = time.time()
        assert hub._after_sleep(avant - 1.0) >= avant
        assert sondes == [] and capture.lignes == []

        # Trente-deux minutes sans tour : la machine dormait.
        assert hub._after_sleep(time.time() - 32 * 60) >= avant
        assert len(sondes) == 2
        assert [l for l in capture.lignes
                if "au réveil, après 32 min de sommeil" in l], capture.lignes
        assert [l for l in capture.lignes if l.startswith("réseau présent après")]

        # Réveil avec le réseau déjà là : la boucle reprend sans un mot.
        capture.lignes.clear(); sondes.clear(); sondes.extend([1, 1])
        hub._after_sleep(time.time() - 3600)
        assert capture.lignes == []
    finally:
        hub_module.log.removeHandler(capture)
    print("  ✓ le réveil de la machine rattend le réseau, et dit combien elle a dormi")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCache du hub — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Une source en panne se voit, et ne s'écrit pas.\n")
