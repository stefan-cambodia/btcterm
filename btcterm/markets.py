"""
Le bitcoin parmi les marchés : actions, or, dollar, taux.

C'est la question qu'un poste de marché pose avant toute autre : l'actif
bouge-t-il seul, ou avec le reste ? Le bitcoin a passé des années à suivre
le Nasdaq comme une valeur technologique à levier, d'autres à s'en
détacher ; l'or et le dollar disent s'il se comporte en réserve ou en
actif risqué. La réponse change, et seule une corrélation glissante la
suit.

Les marchés traditionnels ne cotent que cinq jours sur sept : tout se lit
sur leurs séances, le bitcoin pris à la clôture UTC du même jour — trois à
quatre heures après la cloche de New York, l'écart qu'on accepte plutôt
que d'inventer des séances le week-end. Les rendements sont
logarithmiques ; un taux, lui, se compare en variations de points — le
rendement d'un rendement n'a pas de sens.

Source : Yahoo Finance (`sources.fetch_yahoo_daily`). Le module ne fait
que des calculs, sans réseau.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

__all__ = ["ASSETS", "Asset", "LONG_WINDOW", "MONTH_SESSIONS",
           "SHORT_WINDOW", "align", "changes", "performance",
           "rolling_correlation", "snapshot"]


@dataclass(frozen=True)
class Asset:
    """Un marché confronté au bitcoin."""

    key: str
    symbol: str      #: symbole Yahoo
    label: str
    kind: str        #: `price` (rendements) ou `rate` (variations en points)
    charted: bool    #: tracé au graphique, ou seulement au tableau


#: Trois marchés au graphique, deux au tableau seulement. Au-delà de trois
#: courbes qui se croisent, aucune palette ne les garde discernables pour un
#: œil daltonien ; le Nasdaq redit presque le S&P 500, et un taux n'a pas
#: l'unité des autres.
ASSETS: tuple[Asset, ...] = (
    Asset("spx", "^GSPC", "S&P 500", "price", True),
    Asset("gold", "GC=F", "Or", "price", True),
    Asset("dxy", "DX-Y.NYB", "Dollar (DXY)", "price", True),
    Asset("ndx", "^NDX", "Nasdaq 100", "price", False),
    Asset("us10y", "^TNX", "Taux US 10 ans", "rate", False),
)

#: Fenêtres de corrélation, en séances : un mois et demi de bourse, et un
#: trimestre et demi — la courte suit le régime, la longue le confirme.
SHORT_WINDOW = 30
LONG_WINDOW = 90
#: Un mois de bourse.
MONTH_SESSIONS = 21


def changes(values: pd.Series, kind: str = "price") -> pd.Series:
    """Rendements logarithmiques d'un prix, variations en points d'un taux."""
    return np.log(values).diff() if kind == "price" else values.diff()


def _daily(frame: pd.DataFrame) -> pd.Series:
    """La dernière clôture de chaque date."""
    dates = pd.to_datetime(frame["time"]).dt.normalize()
    return frame.assign(date=dates).groupby("date")["close"].last()


def align(btc: pd.DataFrame, other: pd.DataFrame) -> pd.DataFrame:
    """Le bitcoin et un marché, sur les seules séances de ce dernier.

    Colonnes `btc` et `other`, indexées par date. Les week-ends et jours
    fériés de la place disparaissent : le bitcoin y a bougé, mais le
    marché comparé n'a rien dit.
    """
    if btc.empty or other.empty:
        return pd.DataFrame(columns=["btc", "other"])
    return (pd.concat({"btc": _daily(btc), "other": _daily(other)},
                      axis=1, join="inner").dropna())


def rolling_correlation(aligned: pd.DataFrame, kind: str = "price",
                        window: int = SHORT_WINDOW) -> pd.Series:
    """Corrélation glissante des variations quotidiennes, sur `window` séances."""
    if len(aligned) <= window:
        return pd.Series(dtype=float)
    ours = changes(aligned["btc"], "price")
    theirs = changes(aligned["other"], kind)
    return ours.rolling(window).corr(theirs).dropna()


def performance(series: pd.Series, since: pd.Timestamp) -> pd.Series:
    """La série ramenée à 100 à sa première valeur depuis `since`."""
    part = series[series.index >= since]
    if part.empty or not part.iloc[0]:
        return pd.Series(dtype=float)
    return part / part.iloc[0] * 100


def _change(series: pd.Series, lag: int, kind: str) -> Optional[float]:
    """Variation sur `lag` points : en % pour un prix, en points pour un taux."""
    if len(series) <= lag:
        return None
    last, before = float(series.iloc[-1]), float(series.iloc[-1 - lag])
    if kind == "rate":
        return last - before
    return (last / before - 1) * 100 if before else None


def _last_corr(aligned: pd.DataFrame, kind: str, window: int) -> Optional[float]:
    corr = rolling_correlation(aligned, kind, window)
    return None if corr.empty else float(corr.iloc[-1])


def snapshot(btc: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> list[dict]:
    """Le tableau du panneau : une ligne pour le bitcoin, une par marché.

    Pour chaque ligne : `label`, `last`, `day` et `month` (variation sur une
    séance et sur un mois — trente jours pour le bitcoin, qui cote tous les
    jours, vingt et une séances ailleurs), `kind`, et pour les marchés la
    corrélation au bitcoin sur 30 et 90 séances. Un marché sans données
    garde sa ligne, vide : son absence se voit.
    """
    rows = []
    if not btc.empty:
        daily = _daily(btc)
        rows.append({"key": "btc", "label": "Bitcoin", "kind": "price",
                     "last": float(daily.iloc[-1]),
                     "day": _change(daily, 1, "price"),
                     "month": _change(daily, 30, "price"),
                     "corr30": None, "corr90": None})
    for asset in ASSETS:
        frame = frames.get(asset.key)
        if frame is None or frame.empty:
            rows.append({"key": asset.key, "label": asset.label,
                         "kind": asset.kind, "last": None, "day": None,
                         "month": None, "corr30": None, "corr90": None})
            continue
        daily = _daily(frame)
        aligned = align(btc, frame)
        rows.append({"key": asset.key, "label": asset.label,
                     "kind": asset.kind, "last": float(daily.iloc[-1]),
                     "day": _change(daily, 1, asset.kind),
                     "month": _change(daily, MONTH_SESSIONS, asset.kind),
                     "corr30": _last_corr(aligned, asset.kind, SHORT_WINDOW),
                     "corr90": _last_corr(aligned, asset.kind, LONG_WINDOW)})
    return rows
