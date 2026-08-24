#!/usr/bin/env python3
"""
L'enrichissement du panneau prix, contrôlé sans réseau.

`prepare_price_frame` (terminal/charts.py) est l'héritier direct des
dashboards Dash disparus — btc-dash.py en tête : c'est lui qui ajoute à
l'OHLCV brut toutes les colonnes que le graphique prix consomme, moyennes
mobiles, bandes de Bollinger, RSI, Connors RSI, volatilité, ATR et signal
gradué. Ce test fige ce contrat : les colonnes attendues sont là, leurs
valeurs restent dans leurs bornes, l'entrée n'est pas mutée et deux appels
sur les mêmes données produisent le même résultat — la stabilité dont le
canal différentiel dépend en aval.

Lancement :
    python tests/test_prepare_price_frame.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from btcterm.sources import generate_demo_ohlcv  # noqa: E402
from terminal.charts import prepare_price_frame  # noqa: E402

#: Les colonnes que l'enrichissement doit ajouter à l'OHLCV brut —
#: le contrat que le rendu Lightweight Charts consomme.
COLONNES_AJOUTEES = [
    "ma9", "ma26", "ma200",
    "bb_mid", "bb_upper", "bb_lower",
    "rsi", "crsi",
    "vol_252", "atr", "vol_ma20",
    "signal",
]


def frame_de_demo(limit: int = 400, interval: str = "1h") -> pd.DataFrame:
    return generate_demo_ohlcv(limit, interval=interval, index=False)


def test_colonnes_du_contrat_presentes():
    brut = frame_de_demo()
    enrichi = prepare_price_frame(brut)

    manquantes = [c for c in COLONNES_AJOUTEES if c not in enrichi.columns]
    assert not manquantes, f"colonnes absentes : {manquantes}"

    #: L'OHLCV d'origine doit traverser intact, lignes comprises.
    assert len(enrichi) == len(brut), "l'enrichissement ne change pas la longueur"
    for col in ("time", "open", "high", "low", "close", "volume"):
        assert enrichi[col].equals(brut[col]), f"colonne {col} altérée"
    print(f"  ✓ {len(COLONNES_AJOUTEES)} colonnes ajoutées, OHLCV intact")


def test_entree_non_mutee():
    """`prepare_price_frame` travaille sur une copie : le DataFrame que le
    hub garde en cache ne doit pas se retrouver enrichi par effet de bord,
    sans quoi un second passage calculerait des indicateurs d'indicateurs."""
    brut = frame_de_demo(250)
    colonnes_avant = list(brut.columns)
    prepare_price_frame(brut)

    assert list(brut.columns) == colonnes_avant, "l'entrée a été mutée"
    print("  ✓ le DataFrame d'entrée ressort inchangé")


def test_bornes_et_ordre_des_indicateurs():
    df = prepare_price_frame(frame_de_demo())

    #: RSI et Connors RSI sont des oscillateurs bornés sur [0, 100].
    for col in ("rsi", "crsi"):
        valides = df[col].dropna()
        assert not valides.empty, f"{col} entièrement NaN"
        assert valides.between(0, 100).all(), f"{col} hors de [0, 100]"

    #: Les bandes de Bollinger encadrent leur médiane, dans cet ordre.
    bandes = df[["bb_lower", "bb_mid", "bb_upper"]].dropna()
    assert (bandes["bb_lower"] <= bandes["bb_mid"]).all(), "bb_lower > bb_mid"
    assert (bandes["bb_mid"] <= bandes["bb_upper"]).all(), "bb_mid > bb_upper"

    #: Volatilité annualisée et ATR sont des grandeurs positives.
    for col in ("vol_252", "atr"):
        valides = df[col].dropna()
        assert not valides.empty, f"{col} entièrement NaN"
        assert (valides >= 0).all(), f"{col} négatif"
    print("  ✓ oscillateurs bornés, bandes ordonnées, volatilités positives")


def test_ma200_respecte_sa_periode_de_chauffe():
    """Avec 400 bougies, la MA 200 doit être muette sur les 199 premières
    et parler ensuite — c'est ce qui a dicté la profondeur d'historique
    par intervalle héritée des dashboards."""
    df = prepare_price_frame(frame_de_demo(400))

    assert df["ma200"].iloc[:199].isna().all(), "MA200 avant sa période"
    assert df["ma200"].iloc[199:].notna().all(), "MA200 trouée après chauffe"

    #: Et sa valeur est bien la moyenne des 200 dernières clôtures.
    attendu = df["close"].iloc[:200].mean()
    assert np.isclose(df["ma200"].iloc[199], attendu), \
        (df["ma200"].iloc[199], attendu)
    print("  ✓ MA200 : 199 bougies de chauffe, puis moyenne exacte")


def test_signal_gradue_dans_sa_gamme():
    df = prepare_price_frame(frame_de_demo(1_000))

    assert df["signal"].isin([-2, -1, 0, 1, 2]).all(), "signal hors gamme"
    assert (df["signal"] == 0).any(), "aucune bougie neutre sur 1 000"

    #: Sur mille bougies de marche aléatoire, les croisements existent :
    #: un signal partout nul indiquerait un branchement mort.
    assert (df["signal"] != 0).any(), "aucun signal émis sur 1 000 bougies"
    print("  ✓ signal gradué confiné à {-2..2}, émissions présentes")


def test_deterministe_sur_memes_donnees():
    """Deux enrichissements des mêmes bougies doivent être identiques au
    bit près : le pousseur compare les paquets d'un appel à l'autre, et la
    moindre instabilité numérique renverrait tout à chaque cadence."""
    brut = frame_de_demo()
    premier = prepare_price_frame(brut)
    second = prepare_price_frame(brut)

    pd.testing.assert_frame_equal(premier, second)
    print("  ✓ deux appels, deux DataFrames identiques")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nEnrichissement du panneau prix — {len(tests)} vérifications\n"
          + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le contrat d'enrichissement est tenu : colonnes, bornes, "
          "chauffe, stabilité.\n")
