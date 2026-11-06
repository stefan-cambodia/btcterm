#!/usr/bin/env python3
"""
Le bitcoin parmi les marchés, sans réseau.

Vérifié ici :

- un prix se compare en rendements logarithmiques, un taux en points ;
- le bitcoin ne se lit que sur les séances du marché comparé — ses
  week-ends disparaissent ;
- la corrélation glissante vaut 1 pour des variations identiques, −1 pour
  des variations opposées, et rien tant que la fenêtre n'est pas pleine ;
- la performance repart de 100 au début de la fenêtre ;
- le tableau dit les variations en % ou en points de base, et garde la
  ligne vide d'une place muette ;
- le collecteur Yahoo écarte les séances sans cotation, date à l'heure de
  la place, et lève sur une série vide ;
- le panneau et sa figure tiennent sans données.

Lancement :
    python tests/test_markets.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from btcterm import markets as mkt, sources  # noqa: E402


def serie(dates, valeurs):
    return pd.DataFrame({"time": pd.to_datetime(dates), "close": valeurs})


def marche_aleatoire(jours, graine, depart="2026-01-01", tous_les_jours=False):
    """Des clôtures plausibles ; en jours ouvrés sauf pour le bitcoin."""
    dates = (pd.date_range(depart, periods=jours, freq="D") if tous_les_jours
             else pd.bdate_range(depart, periods=jours))
    pas = np.random.default_rng(graine).normal(0, 0.02, len(dates))
    return serie(dates, 100 * np.exp(np.cumsum(pas)))


def test_prix_et_taux_ne_se_comparent_pas_pareil():
    prix = pd.Series([100.0, 110.0, 99.0])
    assert np.allclose(mkt.changes(prix).dropna(),
                       [math.log(1.1), math.log(0.9)])
    taux = pd.Series([4.0, 4.25, 4.10])
    assert np.allclose(mkt.changes(taux, "rate").dropna(), [0.25, -0.15])
    print("  ✓ rendements logarithmiques pour un prix, points pour un taux")


def test_le_bitcoin_se_lit_sur_les_seances_du_marche():
    btc = marche_aleatoire(14, 1, tous_les_jours=True)
    spx = marche_aleatoire(10, 2)
    aligne = mkt.align(btc, spx)
    assert len(aligne) == 10
    assert all(date.weekday() < 5 for date in aligne.index), "un week-end est resté"
    # Le cours du bitcoin est celui du même jour.
    lundi = aligne.index[2]
    assert aligne.loc[lundi, "btc"] == float(
        btc.set_index("time").loc[lundi, "close"])
    assert mkt.align(btc.iloc[0:0], spx).empty
    print("  ✓ les week-ends du bitcoin écartés, le même jour comparé")


def test_la_correlation_glissante():
    base = marche_aleatoire(80, 3)
    meme = base.assign(close=base["close"] * 3)          # mêmes rendements
    inverse = base.assign(close=10_000 / base["close"])  # rendements opposés
    un = mkt.rolling_correlation(mkt.align(base, meme), window=30)
    assert len(un) == 80 - 30 and np.allclose(un, 1.0)
    moins_un = mkt.rolling_correlation(mkt.align(base, inverse), window=30)
    assert np.allclose(moins_un, -1.0)
    assert mkt.rolling_correlation(mkt.align(base.iloc[:20], meme), window=30).empty
    print("  ✓ 1 pour des variations identiques, −1 opposées, rien trop tôt")


def test_la_performance_repart_de_100():
    valeurs = pd.Series([50.0, 80.0, 100.0, 120.0],
                        index=pd.to_datetime(["2026-01-01", "2026-02-01",
                                              "2026-03-01", "2026-04-01"]))
    perf = mkt.performance(valeurs, pd.Timestamp("2026-02-15"))
    assert list(perf) == [100.0, 120.0]
    assert mkt.performance(valeurs, pd.Timestamp("2027-01-01")).empty
    print("  ✓ base 100 au début de la fenêtre")


def test_le_tableau():
    btc = marche_aleatoire(400, 4, tous_les_jours=True)
    frames = {"spx": marche_aleatoire(200, 5),
              "us10y": serie(pd.bdate_range("2026-01-01", periods=30),
                             np.linspace(4.0, 4.5, 30))}
    lignes = {ligne["key"]: ligne for ligne in mkt.snapshot(btc, frames)}
    assert list(lignes) == ["btc", "spx", "gold", "dxy", "ndx", "us10y"]
    # Le bitcoin : un jour, trente jours.
    fermetures = btc["close"]
    assert math.isclose(lignes["btc"]["day"],
                        (fermetures.iloc[-1] / fermetures.iloc[-2] - 1) * 100)
    assert math.isclose(lignes["btc"]["month"],
                        (fermetures.iloc[-1] / fermetures.iloc[-31] - 1) * 100)
    # Le taux : des points, sur une séance et sur vingt et une.
    pas = 0.5 / 29
    assert math.isclose(lignes["us10y"]["day"], pas)
    assert math.isclose(lignes["us10y"]["month"], 21 * pas)
    assert lignes["spx"]["corr30"] is not None and lignes["spx"]["corr90"] is not None
    # Une place muette garde sa ligne, vide.
    assert lignes["gold"]["last"] is None and lignes["gold"]["corr30"] is None
    print("  ✓ variations en % ou en points, corrélations, place muette dite")


def test_le_collecteur_yahoo():
    class Reponse:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload

    # Trois séances à New York, dont une sans cotation ; 14:30 UTC,
    # l'ouverture de la place, tombe le même jour à son heure.
    payload = {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "timestamp": [1793716200, 1793802600, 1793889000],
        "indicators": {"quote": [{"close": [7400.0, None, 7353.7]}]},
    }]}}
    original = sources.requests.get
    try:
        vus = []

        def faux(url, params=None, headers=None, timeout=None):
            vus.append(headers)
            return Reponse(payload)
        sources.requests.get = faux
        frame = sources.fetch_yahoo_daily("^GSPC")
        assert list(frame["close"]) == [7400.0, 7353.7], frame
        assert [d.day for d in frame["time"]] == [3, 5], frame["time"]
        assert vus[0]["User-Agent"] == "btcterm/1.1", "Yahoo refuse l'agent par défaut"

        for vide in ({"chart": {"result": None}},
                     {"chart": {"result": [{"meta": {}, "timestamp": [1],
                      "indicators": {"quote": [{"close": [None]}]}}]}}):
            sources.requests.get = lambda *a, **k: Reponse(vide)
            try:
                sources.fetch_yahoo_daily("^GSPC")
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"une série vide doit lever : {vide}")
    finally:
        sources.requests.get = original
    print("  ✓ séances sans cotation écartées, date de la place, vide qui lève")


def test_le_panneau_tient_sans_donnees():
    from terminal import charts
    from terminal.panels import markets as panneau

    vide = pd.DataFrame(columns=["time", "close"])
    lignes = mkt.snapshot(vide, {})
    assert [l["key"] for l in lignes] == ["spx", "gold", "dxy", "ndx", "us10y"]
    assert "indisponible" in str(panneau.badges(lignes))
    assert "indisponible" in str(panneau.table(lignes))
    for vue in charts.MARKETS_VIEWS:
        figure = charts.build_markets_chart(vue, vide, {}, "6M")
        assert figure.layout.annotations, "une figure vide doit le dire"
    print("  ✓ absence dite au titre, au tableau et au graphique")


def test_les_etiquettes_ne_se_chevauchent_pas():
    from terminal.charts import _spread_labels

    bouts = [("x", 100.0, "S&P 500"), ("x", 100.2, "Dollar"), ("x", 80.0, "Or")]
    places = sorted(y for _, y, _ in _spread_labels(bouts, span=40))
    assert all(b - a >= 40 * 0.035 - 1e-9 for a, b in zip(places, places[1:]))
    assert places[0] == 80.0, "une étiquette isolée ne bouge pas"
    print("  ✓ étiquettes de bout de courbe écartées")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nMarchés — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le bitcoin se lit parmi les marchés, et se tait sans eux.\n")
