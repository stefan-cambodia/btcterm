"""
Ce que disent les indicateurs, et ce qu'ils valent.

Deux lectures, sans réseau ni rendu, pour le panneau ANALYSE :

- `timeframe_summary` résume un intervalle : tendance, écart à la MA 200,
  RSI, dernier signal ;
- `signal_stats` mesure les signaux du panneau prix sur l'historique. Le
  graphique pose des flèches depuis le début sans que rien ne dise si
  elles valent quelque chose ;
- `rule_stats` compare quelques règles simples au fait de rester investi, en
  rendement comme en repli : le bilan des flèches disant qu'elles n'apportent
  rien, reste à savoir ce qui apporte quelque chose.

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

__all__ = ["FEE_PCT", "GRADES", "HORIZONS", "MIN_SAMPLE", "RULES",
           "monthly_returns", "position_size", "position_state",
           "price_signals", "rule_stats", "signal_stats", "timeframe_summary"]

#: Les grades de `graded_signals`, du plus acheteur au plus vendeur.
GRADES = (2, 1, -1, -2)

#: Horizons mesurés, en bougies.
HORIZONS = (5, 20)

#: Effectif sous lequel une ligne n'est pas à prendre au sérieux.
MIN_SAMPLE = 30

#: Fenêtre de la moyenne de tendance : en deçà, ni signal gradué ni écart.
TREND_WINDOW = 200

#: Frais d'un passage, à l'entrée comme à la sortie : le tarif de base d'un
#: compte Binance sans remise, en %.
FEE_PCT = 0.1

#: Les règles comparées, par nom et libellé.
RULES = (("hold", "rester investi"),
         ("ma200", "au-dessus de la MA 200"),
         ("cross", "MA 9 au-dessus de la MA 26"))


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


def _rule_row(returns: pd.Series, position: pd.Series, fee_pct: float,
              years: float) -> dict:
    """La tenue d'une règle : `position` vaut 1 quand elle est investie."""
    turnover = position.diff().abs().fillna(position.iloc[0])
    net = position * returns - turnover * fee_pct / 100
    equity = (1 + net).cumprod()
    return {
        "total": float((equity.iloc[-1] - 1) * 100),
        "annual": float((equity.iloc[-1] ** (1 / years) - 1) * 100),
        "drawdown": float((equity / equity.cummax().clip(lower=1) - 1).min() * 100),
        "exposure": float(position.mean() * 100),
        "entries": int((position.diff().fillna(position.iloc[0]) > 0).sum()),
    }


def _rule_now(wanted: pd.Series, time: pd.Series) -> dict:
    """Ce que dit la règle à la dernière bougie, et depuis quand."""
    changes = wanted.ne(wanted.shift()).to_numpy().nonzero()[0]
    return {"invested": bool(wanted.iloc[-1]),
            "since": pd.Timestamp(time.iloc[int(changes[-1])])}


def rule_stats(df: pd.DataFrame, fee_pct: float = FEE_PCT) -> Optional[dict]:
    """Chaque règle de `RULES` tenue sur la même période, frais compris.

    Même garde que pour les signaux : la règle lit la clôture, et sa
    position vaut d'une ouverture à la suivante, à partir de la bougie
    d'après. Elle encaisse donc toute la bougie qui la fait sortir. La
    période commence là où la MA 200 existe, pour toutes les règles : la
    détention n'a pas à profiter de mois que les autres ne pouvaient pas
    jouer.
    """
    if df.attrs.get("demo", False) or len(df) <= TREND_WINDOW + 2:
        return None
    work = price_signals(df)
    # D'une ouverture à la suivante ; la dernière bougie n'a pas de suite.
    returns = work["open"].shift(-1) / work["open"] - 1
    # `shift(1)` : la clôture de t décide de ce qu'on tient à partir de
    # l'ouverture de t + 1, ligne t + 1 de cette série de rendements.
    wanted = {
        "hold": pd.Series(1.0, index=work.index),
        "ma200": (work["close"] > work["ma200"]).astype(float),
        "cross": (work["ma9"] > work["ma26"]).astype(float),
    }
    rules = {name: series.shift(1) for name, series in wanted.items()}
    start = work["ma200"].first_valid_index() + 1
    span = df.iloc[start:]
    returns = returns.iloc[start:-1]
    years = (span["time"].iloc[-1] - span["time"].iloc[0]).days / 365.25
    if len(returns) < 2 or years <= 0:
        return None
    return {
        "first": span["time"].iloc[0], "last": span["time"].iloc[-1],
        "years": years, "fee": fee_pct,
        "rows": [{"rule": name, "label": label,
                  **_rule_row(returns, rules[name].iloc[start:-1], fee_pct, years),
                  **_rule_now(wanted[name].iloc[start:], df["time"].iloc[start:])}
                 for name, label in RULES],
    }


def position_state(side: str, quantity: Optional[float],
                   entry: Optional[float], price: Optional[float],
                   stop: Optional[float] = None) -> Optional[dict]:
    """Ce que vaut une position au cours `price`, ou rien s'il manque un champ.

    `pnl` et `pnl_pct` sont le résultat latent. Avec un stop, `stop_pct` dit la
    distance qu'il reste, négative s'il est franchi, et `stop_loss` le résultat
    par rapport au cours actuel si le stop est touché. `r` rapporte le résultat
    à la perte prévue à l'entrée : un stop placé du mauvais côté n'a pas de
    multiple.
    """
    if not quantity or not entry or not price or quantity <= 0 or entry <= 0:
        return None
    sign = -1 if side == "short" else 1
    pnl = sign * (price - entry) * quantity
    state = {"value": quantity * price, "pnl": pnl,
             "pnl_pct": sign * (price / entry - 1) * 100,
             "stop_pct": None, "stop_loss": None, "r": None}
    if stop and stop > 0:
        state["stop_pct"] = sign * (price - stop) / price * 100
        state["stop_loss"] = sign * (stop - price) * quantity
        risk = sign * (entry - stop) * quantity
        if risk > 0:
            state["r"] = pnl / risk
    return state


def position_size(capital: Optional[float], risk_pct: Optional[float],
                  distance: Optional[float],
                  price: Optional[float]) -> Optional[dict]:
    """La taille qui perd `risk_pct` du capital si le cours va à `distance`.

    `distance` est l'écart au stop, en dollars. Le levier implicite dit ce que
    cette taille demande : au-delà de 1, le capital ne la couvre pas.
    """
    if (not capital or not risk_pct or not distance or not price
            or capital <= 0 or risk_pct <= 0 or distance <= 0):
        return None
    risk = capital * risk_pct / 100
    quantity = risk / distance
    return {"risk": risk, "quantity": quantity, "value": quantity * price,
            "price": price, "distance": distance,
            "distance_pct": distance / price * 100,
            "leverage": quantity * price / capital}


def monthly_returns(df: pd.DataFrame) -> Optional[dict]:
    """Le rendement de chaque mois, par année, à partir de bougies mensuelles.

    `grid[année][mois]` en %, le mois en cours compris. `years` compose les
    mois de chaque année ; `months` donne pour chaque mois sa moyenne et sa
    part de mois haussiers, sur le nombre d'années disponibles : une
    saisonnalité sur neuf points se lit avec prudence.
    """
    if df.attrs.get("demo", False) or df.empty:
        return None
    time = pd.to_datetime(df["time"])
    returns = (df["close"] / df["open"] - 1) * 100
    grid: dict[int, dict[int, float]] = {}
    for stamp, value in zip(time, returns):
        grid.setdefault(stamp.year, {})[stamp.month] = float(value)
    years = {
        year: float(((1 + pd.Series(months) / 100).prod() - 1) * 100)
        for year, months in grid.items()
    }
    months = {}
    for month in range(1, 13):
        values = [row[month] for row in grid.values() if month in row]
        if values:
            months[month] = {
                "mean": sum(values) / len(values),
                "up": sum(v > 0 for v in values) / len(values) * 100,
            }
    return {"grid": grid, "years": years, "months": months}


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
    atr = (ind.atr(df, 14).iloc[-1] if {"high", "low"} <= set(df.columns)
           else float("nan"))
    return {
        "close": float(last["close"]),
        "atr_pct": None if pd.isna(atr) else float(atr / last["close"] * 100),
        "gap": float((last["close"] / last["ma200"] - 1) * 100),
        "rsi": float(last["rsi"]),
        "trend": "up" if above and rising
                 else "down" if not above and not rising else "mixed",
        "signal": int(work["signal"].iloc[fired[-1]]) if len(fired) else 0,
        "signal_age": int(len(work) - 1 - fired[-1]) if len(fired) else None,
    }
