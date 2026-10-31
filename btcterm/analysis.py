"""
Ce que disent les indicateurs, et ce qu'ils valent.

Deux lectures, sans réseau ni rendu, pour le panneau ANALYSE :

- `timeframe_summary` résume un intervalle : tendance, écart à la MA 200,
  RSI, dernier signal ;
- `signal_stats` mesure les signaux du panneau prix sur l'historique. Le
  graphique pose des flèches depuis le début sans que rien ne dise si
  elles valent quelque chose.

Trois règles gardent cette mesure honnête :

- **Pas de regard sur le futur.** Un signal se forme à la clôture de sa
  bougie : l'entrée se fait à l'ouverture de la suivante, pas à ce cours
  qu'on ne pouvait plus avoir.
- **Une référence.** Le bitcoin a monté sur la période : n'importe quel
  signal d'achat y gagne, et n'importe quel signal de vente y perd. Seul
  l'écart à la référence — toutes les bougies, sans aucun signal — dit
  quelque chose. Un signal de vente réussit quand le cours baisse.
- **L'effectif en face du chiffre.** En dessous de `MIN_SAMPLE`, une moyenne
  n'est qu'une anecdote. Les fenêtres de deux signaux proches se recouvrent,
  et les frais ne sont pas comptés : ces chiffres se lisent comme une
  description du passé, pas comme la promesse d'un rendement.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from . import indicators as ind

__all__ = ["GRADES", "HORIZONS", "MIN_SAMPLE", "price_signals",
           "signal_stats", "timeframe_summary"]

#: Les grades de `graded_signals`, du plus acheteur au plus vendeur.
GRADES = (2, 1, -1, -2)

#: Horizons mesurés, en bougies.
HORIZONS = (5, 20)

#: Effectif sous lequel une ligne n'est pas à prendre au sérieux.
MIN_SAMPLE = 30

#: Fenêtre de la moyenne de tendance : en deçà, ni signal gradué ni écart.
TREND_WINDOW = 200


def price_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Les signaux du panneau prix, avec les colonnes qui les fondent.

    Les mêmes réglages que `terminal.charts.prepare_price_frame`, que
    `tests/test_analysis.py` compare à celui-ci. Sans le reste de
    l'enrichissement : le Connors RSI coûterait cher sur vingt mille bougies,
    pour une colonne que le signal ne lit pas.
    """
    work = df[["open", "close"]].copy()
    work["ma9"] = ind.sma(df["close"], 9)
    work["ma26"] = ind.sma(df["close"], 26)
    work["ma200"] = ind.sma(df["close"], TREND_WINDOW)
    work["rsi"] = ind.rsi(df["close"], 14)
    work["signal"] = ind.graded_signals(work, fast="ma9", slow="ma26",
                                        trend="ma200")
    return work


def forward_return(df: pd.DataFrame, horizon: int) -> pd.Series:
    """Rendement en %, de l'ouverture suivante à la clôture `horizon` plus loin."""
    entry = df["open"].shift(-1)
    return (df["close"].shift(-horizon) / entry - 1) * 100


def signal_stats(df: pd.DataFrame,
                 horizons: tuple[int, ...] = HORIZONS) -> Optional[dict]:
    """Le bilan des signaux sur `df`, ou rien sans historique suffisant.

    `df` ne doit porter que des bougies closes. Pour chaque grade et chaque
    horizon : `count` signaux mesurables, rendement moyen `mean`, part de
    cas favorables `hit`, et `edge`, l'avance sur la référence dans le sens
    du signal — positive quand le signal a fait mieux que de rester investi,
    qu'il soit d'achat ou de vente.
    """
    if df.attrs.get("demo", False) or len(df) <= TREND_WINDOW + max(horizons):
        return None
    work = price_signals(df)
    returns = {h: forward_return(work, h) for h in horizons}
    base = {h: float(returns[h].mean()) for h in horizons}

    rows = []
    for grade in GRADES:
        fired = work["signal"] == grade
        sign = 1 if grade > 0 else -1
        cells = {}
        for horizon in horizons:
            sample = returns[horizon][fired].dropna()
            cells[horizon] = None if sample.empty else {
                "count": int(len(sample)),
                "mean": float(sample.mean()),
                "hit": float((sign * sample > 0).mean() * 100),
                "edge": float(sign * (sample.mean() - base[horizon])),
            }
        rows.append({"grade": grade, "count": int(fired.sum()), "cells": cells})

    return {"bars": len(df), "first": df["time"].iloc[0],
            "last": df["time"].iloc[-1], "horizons": list(horizons),
            "base": base, "rows": rows}


def timeframe_summary(df: pd.DataFrame) -> Optional[dict]:
    """L'état d'un intervalle à sa dernière bougie, ou rien sans MA 200."""
    if df.attrs.get("demo", False) or len(df) < TREND_WINDOW:
        return None
    work = price_signals(df)
    last = work.iloc[-1]
    if pd.isna(last["ma200"]) or pd.isna(last["rsi"]):
        return None
    above = last["close"] > last["ma200"]
    rising = last["ma9"] > last["ma26"]
    fired = work["signal"].to_numpy().nonzero()[0]
    return {
        "close": float(last["close"]),
        "gap": float((last["close"] / last["ma200"] - 1) * 100),
        "rsi": float(last["rsi"]),
        "trend": "up" if above and rising
                 else "down" if not above and not rising else "mixed",
        "signal": int(work["signal"].iloc[fired[-1]]) if len(fired) else 0,
        "signal_age": int(len(work) - 1 - fired[-1]) if len(fired) else None,
    }
