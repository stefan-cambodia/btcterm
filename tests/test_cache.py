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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCache du hub — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Une source en panne se voit, et ne s'écrit pas.\n")
