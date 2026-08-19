"""
Indicateurs techniques — socle commun du terminal.

Ce module centralise les calculs qui étaient dupliqués dans les deux
dashboards d'origine, `btc-dash.py` et `btc_dashboard2.py`, supprimés
depuis au profit du terminal. Les fonctions sont volontairement
granulaires et sans effet de bord : elles prennent des `Series`/`DataFrame`
et retournent des `Series`/`DataFrame`, sans jamais écrire dans le
DataFrame d'entrée. La composition (quelles colonnes, quels paramètres)
reste à la charge de chaque panneau.

Convention OHLCV : les fonctions attendant un DataFrame utilisent les
colonnes `open`, `high`, `low`, `close`, `volume` en minuscules.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "sma", "ema", "moving_average",
    "rsi", "streak", "percent_rank", "connors_rsi",
    "bollinger", "atr", "volatility",
    "volume_profile",
    "cross_up", "cross_down", "crosses_above", "crosses_below",
    "graded_signals", "marker_signals",
]


# ─────────────────────────────────────────────────────────────
# Moyennes mobiles
# ─────────────────────────────────────────────────────────────

def sma(series: pd.Series, period: int) -> pd.Series:
    """Moyenne mobile simple."""
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Moyenne mobile exponentielle (span = période)."""
    return series.ewm(span=period, adjust=False).mean()


def moving_average(series: pd.Series, period: int, method: str = "sma") -> pd.Series:
    """Moyenne mobile, `method` valant "sma" ou "ema".

    Les deux dashboards d'origine divergeaient sur ce point : SMA pour
    `btc-dash.py`, EMA pour `btc_dashboard2.py` sur les MA 9 et 26. La
    fusion a tranché pour la SMA, que le panneau prix appelle
    directement ; le paramètre garde l'autre variante à portée et permet
    aux tests de parité de la rejouer.
    """
    if method == "sma":
        return sma(series, period)
    if method == "ema":
        return ema(series, period)
    raise ValueError(f"méthode de moyenne mobile inconnue : {method!r}")


# ─────────────────────────────────────────────────────────────
# RSI et Connors RSI
# ─────────────────────────────────────────────────────────────

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder, lissage exponentiel (alpha = 1/période)."""
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def streak(series: pd.Series) -> pd.Series:
    """Série de hausses/baisses consécutives (composante du Connors RSI).

    Positive et croissante tant que la valeur monte, négative et
    décroissante tant qu'elle baisse, remise à ±1 au changement de sens.
    """
    values = np.zeros(len(series))
    arr = series.to_numpy()
    for i in range(1, len(arr)):
        if arr[i] > arr[i - 1]:
            values[i] = max(values[i - 1], 0) + 1
        elif arr[i] < arr[i - 1]:
            values[i] = min(values[i - 1], 0) - 1
    return pd.Series(values, index=series.index)


def _percent_rank_last(window: np.ndarray) -> float:
    """Rang centile (0-100) du dernier élément d'une fenêtre.

    Reproduit exactement `pd.Series(w).rank(pct=True).iloc[-1] * 100`
    (méthode "average" pour les ex æquo) sans construire de Series à
    chaque pas, ce qui rend le calcul roulant utilisable sur de longues
    séries.
    """
    last = window[-1]
    less = np.count_nonzero(window < last)
    equal = np.count_nonzero(window == last)
    return (less + (equal + 1) / 2) / len(window) * 100


def percent_rank(series: pd.Series, window: int = 100) -> pd.Series:
    """Rang centile roulant de la valeur courante sur `window` périodes."""
    return series.rolling(window).apply(_percent_rank_last, raw=True)


def connors_rsi(
    close: pd.Series,
    rsi_period: int = 3,
    streak_period: int = 2,
    rank_period: int = 100,
) -> pd.Series:
    """Connors RSI = moyenne de trois composantes 0-100.

    1. RSI court du prix (3 périodes par défaut)
    2. RSI de la série de hausses/baisses consécutives (2 périodes)
    3. Rang centile roulant de la variation d'une période (100 périodes)

    Note : `btc-dash.py` calculait auparavant la troisième composante
    comme le rang **global** d'un ROC 100 périodes, ce qui n'est pas la
    définition de Connors et rendait la valeur dépendante de la longueur
    de l'historique chargé. C'est la définition standard qui est retenue
    ici.
    """
    components = (
        rsi(close, rsi_period)
        + rsi(streak(close), streak_period)
        + percent_rank(close.pct_change(), rank_period)
    )
    return (components / 3).clip(0, 100)


# ─────────────────────────────────────────────────────────────
# Volatilité et enveloppes
# ─────────────────────────────────────────────────────────────

def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bandes de Bollinger : retourne (médiane, bande haute, bande basse)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    return mid, mid + num_std * std, mid - num_std * std


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range, lissage de Wilder."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def volatility(
    close: pd.Series,
    window: int = 252,
    periods_per_year: int = 252,
    log_returns: bool = True,
) -> pd.Series:
    """Volatilité réalisée annualisée, en pourcentage.

    `log_returns=True` utilise les rendements logarithmiques (usage de
    `btc-dash.py`), `False` les variations relatives simples (usage de
    `btc_dashboard2.py`).
    """
    returns = (
        np.log(close / close.shift(1)) if log_returns else close.pct_change()
    )
    return returns.rolling(window).std() * np.sqrt(periods_per_year) * 100


# ─────────────────────────────────────────────────────────────
# Volume profile
# ─────────────────────────────────────────────────────────────

def volume_profile(
    df: pd.DataFrame, bins: int = 60, value_area: float = 0.70
) -> tuple[np.ndarray, np.ndarray, float, float, float]:
    """Répartition du volume par niveau de prix.

    Le volume de chaque bougie est étalé uniformément sur les paliers
    couverts par son range `low`→`high`.

    Retourne `(centres, volumes, poc, va_bas, va_haut)` où `poc` est le
    palier le plus échangé (Point of Control) et `[va_bas, va_haut]` la
    Value Area, plus petit ensemble de paliers cumulant `value_area` du
    volume total.
    """
    low, high = df["low"].min(), df["high"].max()
    edges = np.linspace(low, high, bins + 1)
    volumes = np.zeros(bins)

    for _, row in df.iterrows():
        idx_lo = max(0, min(np.searchsorted(edges, row["low"], "left"), bins - 1))
        idx_hi = max(0, min(np.searchsorted(edges, row["high"], "right"), bins))
        span = max(1, idx_hi - idx_lo)
        volumes[idx_lo:idx_hi] += row["volume"] / span

    centers = (edges[:-1] + edges[1:]) / 2
    poc = centers[volumes.argmax()]

    total = volumes.sum()
    cumulative = 0.0
    selected: list[int] = []
    for i in np.argsort(volumes)[::-1]:
        cumulative += volumes[i]
        selected.append(i)
        if cumulative >= value_area * total:
            break

    return centers, volumes, poc, centers[min(selected)], centers[max(selected)]


# ─────────────────────────────────────────────────────────────
# Détection de croisements
# ─────────────────────────────────────────────────────────────

def cross_up(fast: pd.Series, slow: pd.Series, first_bar: bool = False) -> pd.Series:
    """`fast` passe au-dessus de `slow`.

    `first_bar=True` signale aussi la toute première barre exploitable,
    quand la valeur précédente est indéfinie (comportement historique de
    `btc_dashboard2.py`) ; `False` exige un état antérieur connu et
    strictement inférieur ou égal (comportement de `btc-dash.py`).
    """
    if first_bar:
        return (fast > slow) & ~(fast.shift(1) > slow.shift(1))
    return (fast > slow) & (fast.shift(1) <= slow.shift(1))


def cross_down(fast: pd.Series, slow: pd.Series, first_bar: bool = False) -> pd.Series:
    """`fast` passe sous `slow`. Voir `cross_up` pour `first_bar`."""
    if first_bar:
        return (fast < slow) & (fast.shift(1) > slow.shift(1))
    return (fast < slow) & (fast.shift(1) >= slow.shift(1))


def crosses_above(series: pd.Series, level: float) -> pd.Series:
    """La série franchit `level` vers le haut (sortie de zone basse)."""
    return (series > level) & (series.shift(1) <= level)


def crosses_below(series: pd.Series, level: float) -> pd.Series:
    """La série franchit `level` vers le bas (entrée en zone basse)."""
    return (series < level) & (series.shift(1) >= level)


# ─────────────────────────────────────────────────────────────
# Signaux composites
# ─────────────────────────────────────────────────────────────

def graded_signals(
    df: pd.DataFrame,
    fast: str = "ma9",
    slow: str = "ma26",
    trend: str = "ma200",
    rsi_col: str = "rsi",
) -> pd.Series:
    """Signal gradué de -2 à +2.

     2 = achat fort   (croisement haussier confirmé par la tendance, ou
                       sortie de survente)
     1 = achat        (croisement haussier)
     0 = neutre
    -1 = vente        (croisement baissier)
    -2 = vente forte  (croisement baissier sous la tendance, ou sortie de
                       surachat)
    """
    signal = pd.Series(0, index=df.index)

    up = cross_up(df[fast], df[slow])
    down = cross_down(df[fast], df[slow])
    above_trend = df["close"] > df[trend]
    oversold_exit = crosses_above(df[rsi_col], 30)
    overbought_exit = crosses_below(df[rsi_col], 70)

    signal[up] = 1
    signal[up & above_trend & (df[rsi_col] < 65)] = 2
    signal[down] = -1
    signal[down & ~above_trend & (df[rsi_col] > 35)] = -2

    signal[oversold_exit] = 2
    signal[overbought_exit] = -2

    return signal


def marker_signals(
    df: pd.DataFrame,
    fast: str = "ma9",
    slow: str = "ma26",
    rsi_col: str = "rsi",
) -> tuple[list, list, list, list]:
    """Points d'achat/vente à porter sur un graphique.

    Combine les croisements de moyennes mobiles et les entrées en zone de
    survente (< 30) ou de surachat (> 70) du RSI.

    Retourne `(dates_achat, prix_achat, dates_vente, prix_vente)`.
    """
    buy_dates: list = []
    buy_prices: list = []
    sell_dates: list = []
    sell_prices: list = []

    def collect(mask: pd.Series, dates: list, prices: list) -> None:
        dates += list(df.index[mask])
        prices += list(df["close"][mask])

    if fast in df.columns and slow in df.columns:
        collect(cross_up(df[fast], df[slow], first_bar=True), buy_dates, buy_prices)
        collect(cross_down(df[fast], df[slow], first_bar=True), sell_dates, sell_prices)

    if rsi_col in df.columns:
        collect(crosses_below(df[rsi_col], 30), buy_dates, buy_prices)
        collect(crosses_above(df[rsi_col], 70), sell_dates, sell_prices)

    return buy_dates, buy_prices, sell_dates, sell_prices
