#!/usr/bin/env python3
"""
Les primes régionales : la prime coréenne, de la source à l'écran.

Ce qui se vérifie mal à l'écran se vérifie ici :

- la prime se décompose exactement en won × bitcoin, et se tait s'il
  manque une pièce ;
- Upbit se lit en deux prix ou pas du tout ; le won vient du marché, de la
  BCE en secours, et rien d'implausible ne passe ;
- le hub ne touche jamais le réseau pour la servir, se tait sur une
  lecture d'Upbit trop vieille, ne lance qu'un thread à la fois, et lit
  une source quand l'autre tombe ;
- le relevé de marché la journalise, une base antérieure gagne ses
  colonnes ;
- la ligne du panneau ARBITRAGE et la synthèse la disent.

Aucun réseau n'est touché.

Lancement :
    python tests/test_premiums.py
"""

import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import premiums, sources  # noqa: E402

UPBIT = {"btc": 98_198_000.0, "usdt": 1_352.0, "at": 1_794_490_711.0}
WON = {"rate": 1_333.46, "source": "marché"}


def test_la_prime_se_decompose():
    prime = premiums.korea(UPBIT, WON, 72_650.0)
    assert abs(prime["price_usd"] - 98_198_000 / 1_333.46) < 1e-6
    assert abs(prime["premium"] - (98_198_000 / 1_333.46 / 72_650 - 1)) < 1e-12
    assert abs(prime["won"] - (1_352 / 1_333.46 - 1)) < 1e-12
    assert abs(prime["bitcoin"] - (98_198_000 / 1_352 / 72_650 - 1)) < 1e-12
    assert abs((1 + prime["won"]) * (1 + prime["bitcoin"])
               - (1 + prime["premium"])) < 1e-12, "les deux facteurs se composent"
    assert prime["source"] == "marché" and prime["at"] == UPBIT["at"]
    for manque in ((None, WON, 72_650.0), (UPBIT, None, 72_650.0),
                   (UPBIT, WON, None), (UPBIT, WON, 0.0),
                   ({**UPBIT, "usdt": None}, WON, 72_650.0),
                   (UPBIT, {"rate": None}, 72_650.0)):
        assert premiums.korea(*manque) is None
    print("  ✓ prime = won × bitcoin, exactement ; une pièce manque, rien")


class Reponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self):
        return self.payload


def test_upbit_et_le_won_se_lisent_ou_tombent():
    demandes = []

    def upbit(url, params=None, **_):
        demandes.append((url, params))
        return Reponse([{"market": "KRW-BTC", "trade_price": 98_198_000.0},
                        {"market": "KRW-USDT", "trade_price": 1_352.0}])

    with mock.patch.object(sources._http, "get", upbit):
        cotes = sources.fetch_upbit_quotes()
    assert demandes == [(sources.UPBIT_TICKER, {"markets": "KRW-BTC,KRW-USDT"})]
    assert cotes["btc"] == 98_198_000.0 and cotes["usdt"] == 1_352.0
    assert abs(cotes["at"] - time.time()) < 5

    seul = lambda *a, **k: Reponse([{"market": "KRW-BTC", "trade_price": 9.8e7}])
    with mock.patch.object(sources._http, "get", seul):
        try:
            sources.fetch_upbit_quotes()
        except ValueError:
            pass
        else:
            raise AssertionError("un prix sans l'autre ne décompose rien")

    def yahoo(prix):
        return {"chart": {"result": [{"meta": {"regularMarketPrice": prix}}]}}

    marche = lambda url, **k: Reponse(yahoo(1_333.46))
    with mock.patch.object(sources._http, "get", marche):
        assert sources.fetch_krw_rate() == {"rate": 1_333.46, "source": "marché"}

    vues = []

    def absurde(url, **k):
        vues.append(url)
        if "yahoo" in url:
            return Reponse(yahoo(0.00075))          # le dollar en wons, à l'envers
        return Reponse({"rates": {"KRW": 1_336.9}})

    with mock.patch.object(sources._http, "get", absurde):
        assert sources.fetch_krw_rate() == {"rate": 1_336.9, "source": "BCE"}
    assert vues[1] == sources.FX_REFERENCE_URL

    panne = lambda url, **k: Reponse({}, status=503)
    with mock.patch.object(sources._http, "get", panne):
        try:
            sources.fetch_krw_rate()
        except RuntimeError as exc:
            assert "marché" in str(exc) and "BCE" in str(exc)
        else:
            raise AssertionError("deux sources muettes : une erreur")
    print("  ✓ Upbit en deux prix ; won du marché, BCE en secours, l'absurde écarté")


def hub_avec_carnet(prix=72_650.0):
    from btcterm.hub import MarketHub
    hub = MarketHub(collect_news=False, keep_journal=False)
    hub.books["Binance"].replace({prix - 0.5: 1.0}, {prix + 0.5: 1.0})
    hub.books["Binance"].connected = True
    return hub


def test_le_hub_la_sert_du_cache_seulement():
    hub = hub_avec_carnet()
    reseau = mock.Mock(side_effect=AssertionError("réseau touché"))
    with mock.patch.object(sources, "fetch_upbit_quotes", reseau), \
            mock.patch.object(sources, "fetch_krw_rate", reseau):
        assert hub.korea_premium() is None, "rien en cache : rien"

    frais = {**UPBIT, "at": time.time()}
    with mock.patch.object(sources, "fetch_upbit_quotes", return_value=frais), \
            mock.patch.object(sources, "fetch_krw_rate", return_value=WON):
        hub._refresh_korea()
    with mock.patch.object(sources, "fetch_upbit_quotes", reseau), \
            mock.patch.object(sources, "fetch_krw_rate", reseau):
        prime = hub.korea_premium()
        assert prime is not None and abs(prime["premium"] - premiums.korea(
            frais, WON, 72_650.0)["premium"]) < 1e-12
        assert hub.korea_premium(now=frais["at"] + hub.KOREA_MAX_AGE + 1) is None, \
            "Upbit vieux de plus de trois minutes : la prime se tait"

    # Le won tombe, Upbit répond : la lecture d'Upbit passe quand même, et
    # le cache sert le dernier won.
    neuf = {**UPBIT, "btc": 99_000_000.0, "at": time.time()}
    hub._cache._entries["upbit"] = (0.0, frais)        # vieilli : à relire
    hub._cache._entries["krw"] = (0.0, WON)
    with mock.patch.object(sources, "fetch_upbit_quotes", return_value=neuf), \
            mock.patch.object(sources, "fetch_krw_rate",
                              side_effect=RuntimeError("Yahoo et BCE muets")):
        hub._refresh_korea()
    assert hub._cache.peek("upbit")["btc"] == 99_000_000.0
    assert hub._cache.stale("krw") and hub.korea_premium()["rate"] == WON["rate"]

    # Un seul thread à la fois.
    with hub._korea_lock:
        assert not hub._spawn_korea(), "le précédent tourne encore"
    lu = mock.Mock()
    with mock.patch.object(hub, "_refresh_korea", lu):
        assert hub._spawn_korea()
        for _ in range(50):
            if not hub._korea_lock.locked():
                break
            time.sleep(0.02)
    assert lu.called and not hub._korea_lock.locked()
    print("  ✓ servie du cache, muette si Upbit vieillit, un thread, une source "
          "sans l'autre")


def test_le_releve_la_journalise():
    import sqlite3
    import pandas as pd
    from btcterm import options as opt
    from btcterm.journal import SNAPSHOT_FIELDS, Journal

    with tempfile.TemporaryDirectory() as dossier:
        # Une base d'avant la prime coréenne : ses colonnes s'ajoutent.
        ancienne = Path(dossier) / "journal.db"
        with sqlite3.connect(ancienne) as db:
            db.execute("CREATE TABLE market_snapshots (ts REAL NOT NULL, "
                       "btc_dominance REAL, stable_share REAL, "
                       "total_cap_usd REAL, total_volume_usd REAL, oi_usd REAL)")
            db.execute("INSERT INTO market_snapshots (ts, btc_dominance) "
                       "VALUES (?, 56.0)", (time.time() - 3600,))
        hub = hub_avec_carnet()
        hub.journal = Journal(ancienne)
        hub.market_global = lambda: {}
        hub.open_interest = lambda **kw: pd.DataFrame(columns=["time", "oi", "oi_usd"])
        hub.perp_snapshot = lambda: {}
        hub.long_short = lambda **kw: pd.DataFrame(columns=["time", "crowd", "top"])
        hub.option_chain = lambda: opt.option_chain([])
        hub.futures_curve = lambda: opt.futures_curve([])
        hub.dvol = lambda: pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        maintenant = time.time()
        hub._cache._entries["upbit"] = (maintenant, {**UPBIT, "at": maintenant})
        hub._cache._entries["krw"] = (maintenant, WON)
        hub.record_market_snapshot(now=maintenant)
        assert {"kr_premium", "kr_won"} <= set(SNAPSHOT_FIELDS)
        lignes = hub.market_snapshots()
        attendu = premiums.korea(UPBIT, WON, 72_650.0)
        derniere = lignes.dropna(subset=["kr_premium"]).iloc[-1]
        assert abs(derniere["kr_premium"] - attendu["premium"] * 100) < 1e-9
        assert abs(derniere["kr_won"] - attendu["won"] * 100) < 1e-9
        assert lignes["btc_dominance"].iloc[0] == 56.0, "l'historique reste"
        hub.journal.close()
    print("  ✓ le relevé de marché l'écrit ; une base antérieure gagne ses colonnes")


def texte(composant) -> str:
    if isinstance(composant, (list, tuple)):
        return "".join(texte(c) for c in composant)
    if isinstance(composant, str):
        return composant
    return texte(getattr(composant, "children", "") or "")


def couleurs(composant) -> list:
    if isinstance(composant, (list, tuple)):
        return [c for x in composant for c in couleurs(x)]
    style = getattr(composant, "style", None) or {}
    return [style.get("color")] + couleurs(getattr(composant, "children", None) or [])


def test_le_panneau_et_la_synthese_la_disent():
    from btcterm import brief
    from terminal.panels import arbitrage as panneau
    from terminal.theme import C

    prime = premiums.korea(UPBIT, WON, 72_650.0)
    ligne = panneau.premiums_line(0.0002, prime)
    lu = texte(ligne)
    assert lu.startswith("primes · Coinbase +0.02 % · Corée +1.36 %"), lu
    assert "= won +1.39 %" in lu and "+ BTC -0.03 %" in lu, lu
    assert C["orange"] not in couleurs(ligne), "1,4 % : rien d'ardent"
    ardente = premiums.korea({**UPBIT, "btc": 102_000_000.0}, WON, 72_650.0)
    assert ardente["premium"] * 100 > premiums.KOREA_HOT
    assert C["orange"] in couleurs(panneau.premiums_line(None, ardente))
    assert texte(panneau.premiums_line(None, prime)).startswith("primes · Corée")
    assert texte(panneau.premiums_line(-0.0004, None)) == "primes · Coinbase -0.04 %"
    assert panneau.premiums_line(None, None) == ""

    maintenant = 1_794_490_000.0
    lignes = [{"ts": maintenant - 30 * 3600, "kr_premium": 9.0, "kr_won": 1.0},
              {"ts": maintenant - 3600, "kr_premium": 1.2, "kr_won": 1.1},
              {"ts": maintenant - 60, "kr_premium": 1.4, "kr_won": 1.39},
              {"ts": maintenant - 30, "kr_premium": None, "kr_won": None}]
    item = brief.korea_item(lignes, maintenant)
    assert item.label == "Prime coréenne"
    assert item.text == "+1.40 % · moyenne +1.30 % · dont won +1.39 %", item.text
    assert item.tone == "", "la veille de 9 % est hors fenêtre"
    chaude = brief.korea_item([{"ts": maintenant, "kr_premium": 4.0,
                                "kr_won": 1.0}], maintenant)
    assert chaude.tone == "warn"
    decote = brief.korea_item([{"ts": maintenant, "kr_premium": -0.8}], maintenant)
    assert decote.tone == "down" and "won" not in decote.text
    assert brief.korea_item([{"ts": maintenant, "cb_premium": 0.1}], maintenant) is None
    print("  ✓ ligne de l'arbitrage décomposée, ardente au-delà de 3 % ; synthèse "
          "sur 24 h")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nPrimes régionales — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La demande se lit aussi par où elle passe.\n")
