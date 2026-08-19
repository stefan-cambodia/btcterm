#!/usr/bin/env python3
"""
Test de non-régression de l'extraction du socle (phase 1).

Rejoue les implémentations telles qu'elles étaient dans `btc-dash.py` et
`btc_dashboard2.py` avant l'extraction, et vérifie que `btcterm.indicators`
produit exactement les mêmes valeurs. C'est la garantie que la
factorisation n'a rien changé au comportement.

Le seul écart assumé est documenté par `test_connors_rsi_ecart_btc_dash` :
la troisième composante du Connors RSI de `btc-dash.py` ne suivait pas la
définition standard.

Lancement :
    python tests/test_indicators_parity.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import indicators as ind  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Jeu de données déterministe
# ─────────────────────────────────────────────────────────────

def make_ohlcv(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-01", periods=n, freq="h")
    close = pd.Series(80_000 * np.exp(np.cumsum(rng.normal(0, 0.012, n))), index=index)
    df = pd.DataFrame({
        "open": close.shift(1).fillna(close),
        "high": close * (1 + abs(rng.normal(0, 0.004, n))),
        "low": close * (1 - abs(rng.normal(0, 0.004, n))),
        "close": close,
        "volume": rng.lognormal(18, 0.8, n),
    }, index=index)
    # Quelques prix rigoureusement identiques, pour exercer la gestion des
    # ex æquo dans le rang centile et des plateaux dans les séries de
    # hausses consécutives.
    df.loc[df.index[50:55], "close"] = df["close"].iloc[50]
    return df


# ─────────────────────────────────────────────────────────────
# Implémentations historiques (copies conformes, avant extraction)
# ─────────────────────────────────────────────────────────────

def legacy_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def legacy_streak_btc_dash(series):
    s = np.zeros(len(series))
    for i in range(1, len(series)):
        if series.iloc[i] > series.iloc[i - 1]:
            s[i] = max(1, s[i - 1] + 1)
        elif series.iloc[i] < series.iloc[i - 1]:
            s[i] = min(-1, s[i - 1] - 1)
    return pd.Series(s, index=series.index)


def legacy_streak_dashboard2(close):
    streak = pd.Series(0.0, index=close.index)
    for i in range(1, len(close)):
        if close.iloc[i] > close.iloc[i - 1]:
            streak.iloc[i] = max(streak.iloc[i - 1], 0) + 1
        elif close.iloc[i] < close.iloc[i - 1]:
            streak.iloc[i] = min(streak.iloc[i - 1], 0) - 1
    return streak


def legacy_crsi_dashboard2(close, rsi_p=3, streak_p=2, pct_p=100):
    rsi3 = legacy_rsi(close, rsi_p)
    streak_rsi = legacy_rsi(legacy_streak_dashboard2(close), streak_p)
    pct_rank = close.pct_change().rolling(pct_p).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    )
    return ((rsi3 + streak_rsi + pct_rank) / 3).clip(0, 100)


def legacy_atr(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def legacy_volume_profile(df, bins=60):
    lo, hi = df["low"].min(), df["high"].max()
    edges = np.linspace(lo, hi, bins + 1)
    vols = np.zeros(bins)
    for _, row in df.iterrows():
        idx_lo = max(0, min(np.searchsorted(edges, row["low"], "left"), bins - 1))
        idx_hi = max(0, min(np.searchsorted(edges, row["high"], "right"), bins))
        span = max(1, idx_hi - idx_lo)
        vols[idx_lo:idx_hi] += row["volume"] / span
    centers = (edges[:-1] + edges[1:]) / 2
    poc = centers[vols.argmax()]
    total = vols.sum()
    ranked = np.argsort(vols)[::-1]
    cumsum = 0.0
    va_idx = []
    for i in ranked:
        cumsum += vols[i]
        va_idx.append(i)
        if cumsum >= 0.7 * total:
            break
    return centers, vols, poc, centers[min(va_idx)], centers[max(va_idx)]


def legacy_graded_signals(df):
    sig = pd.Series(0, index=df.index)
    cross_up = (df["ma9"] > df["ma26"]) & (df["ma9"].shift(1) <= df["ma26"].shift(1))
    cross_down = (df["ma9"] < df["ma26"]) & (df["ma9"].shift(1) >= df["ma26"].shift(1))
    above_200 = df["close"] > df["ma200"]
    rsi_os_exit = (df["rsi"] > 30) & (df["rsi"].shift(1) <= 30)
    rsi_ob_exit = (df["rsi"] < 70) & (df["rsi"].shift(1) >= 70)
    sig[cross_up] = 1
    sig[cross_up & above_200 & (df["rsi"] < 65)] = 2
    sig[cross_down] = -1
    sig[cross_down & ~above_200 & (df["rsi"] > 35)] = -2
    sig[rsi_os_exit] = 2
    sig[rsi_ob_exit] = -2
    return sig


def legacy_marker_signals(df):
    buy_dates, buy_prices = [], []
    sell_dates, sell_prices = [], []
    if "ma9" in df.columns and "ma26" in df.columns:
        prev_above = df["ma9"].shift(1) > df["ma26"].shift(1)
        cross_up = (df["ma9"] > df["ma26"]) & (~prev_above)
        cross_down = (df["ma9"] < df["ma26"]) & (prev_above)
        buy_dates += list(df.index[cross_up])
        buy_prices += list(df["close"][cross_up])
        sell_dates += list(df.index[cross_down])
        sell_prices += list(df["close"][cross_down])
    if "rsi" in df.columns:
        rsi_buy = (df["rsi"] < 30) & (df["rsi"].shift(1) >= 30)
        rsi_sell = (df["rsi"] > 70) & (df["rsi"].shift(1) <= 70)
        buy_dates += list(df.index[rsi_buy])
        buy_prices += list(df["close"][rsi_buy])
        sell_dates += list(df.index[rsi_sell])
        sell_prices += list(df["close"][rsi_sell])
    return buy_dates, buy_prices, sell_dates, sell_prices


# ─────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────

DF = make_ohlcv()
CLOSE = DF["close"]


MIN_VALEURS = 100


def assert_series_equal(left, right, label):
    """Compare deux séries, en refusant les comparaisons vides.

    Sans ce garde-fou, deux séries entièrement `NaN` — cas typique d'une
    fixture mal indexée — se compareraient comme égales et le test
    passerait sans rien vérifier.
    """
    assert left.notna().sum() >= MIN_VALEURS, (
        f"{label} : {left.notna().sum()} valeurs exploitables, "
        f"le test ne prouverait rien"
    )
    pd.testing.assert_series_equal(
        left, right, check_names=False, check_dtype=False, rtol=1e-12
    )
    print(f"  ✓ {label}")


def test_rsi():
    assert_series_equal(ind.rsi(CLOSE, 14), legacy_rsi(CLOSE, 14), "RSI 14")
    assert_series_equal(ind.rsi(CLOSE, 3), legacy_rsi(CLOSE, 3), "RSI 3")


def test_streak():
    """Les deux écritures historiques sont algébriquement équivalentes."""
    assert_series_equal(
        ind.streak(CLOSE), legacy_streak_btc_dash(CLOSE), "streak (btc-dash)"
    )
    assert_series_equal(
        ind.streak(CLOSE), legacy_streak_dashboard2(CLOSE), "streak (dashboard2)"
    )


def test_percent_rank():
    """Version vectorisée contre la version à base de `Series.rank`."""
    legacy = CLOSE.pct_change().rolling(100).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1] * 100, raw=False
    )
    assert_series_equal(
        ind.percent_rank(CLOSE.pct_change(), 100), legacy, "rang centile roulant"
    )


def test_connors_rsi():
    assert_series_equal(
        ind.connors_rsi(CLOSE), legacy_crsi_dashboard2(CLOSE), "Connors RSI"
    )


def test_bollinger():
    mid, upper, lower = ind.bollinger(CLOSE, 20, 2)
    legacy_mid = CLOSE.rolling(20).mean()
    legacy_std = CLOSE.rolling(20).std()
    assert_series_equal(mid, legacy_mid, "Bollinger — médiane")
    assert_series_equal(upper, legacy_mid + 2 * legacy_std, "Bollinger — haute")
    assert_series_equal(lower, legacy_mid - 2 * legacy_std, "Bollinger — basse")


def test_atr():
    assert_series_equal(ind.atr(DF, 14), legacy_atr(DF, 14), "ATR 14")


def test_volatility():
    log_ret = np.log(CLOSE / CLOSE.shift(1))
    assert_series_equal(
        ind.volatility(CLOSE, 252),
        log_ret.rolling(252).std() * np.sqrt(252) * 100,
        "volatilité log 252 (btc-dash)",
    )
    assert_series_equal(
        ind.volatility(CLOSE, 10, log_returns=False),
        CLOSE.pct_change().rolling(10).std() * np.sqrt(252) * 100,
        "volatilité simple 10 (dashboard2)",
    )


def test_volume_profile():
    centers, vols, poc, va_lo, va_hi = ind.volume_profile(DF, 60)
    l_centers, l_vols, l_poc, l_lo, l_hi = legacy_volume_profile(DF, 60)
    np.testing.assert_allclose(centers, l_centers)
    np.testing.assert_allclose(vols, l_vols)
    assert (poc, va_lo, va_hi) == (l_poc, l_lo, l_hi)
    print("  ✓ volume profile (centres, volumes, POC, Value Area)")


def _with_indicators(df):
    df = df.copy()
    df["ma9"] = df["close"].rolling(9).mean()
    df["ma26"] = df["close"].rolling(26).mean()
    df["ma200"] = df["close"].rolling(200).mean()
    df["rsi"] = ind.rsi(df["close"], 14)
    return df


def test_graded_signals():
    df = _with_indicators(DF)
    assert_series_equal(
        ind.graded_signals(df), legacy_graded_signals(df), "signaux gradués (-2..+2)"
    )
    assert set(ind.graded_signals(df).unique()) <= {-2, -1, 0, 1, 2}


def test_marker_signals():
    df = _with_indicators(DF)
    assert ind.marker_signals(df) == legacy_marker_signals(df)
    print("  ✓ marqueurs achat/vente")


def test_connors_rsi_ecart_btc_dash():
    """Écart assumé : la variante de `btc-dash.py` n'était pas standard.

    Elle classait un ROC 100 périodes sur **tout** l'historique chargé au
    lieu d'un rang centile roulant de la variation d'une période. La
    valeur dépendait donc du nombre de bougies affichées : changer de
    fenêtre temporelle changeait le CRSI d'une bougie passée.
    """
    legacy = (
        legacy_rsi(CLOSE, 3)
        + legacy_rsi(legacy_streak_btc_dash(CLOSE), 2)
        + CLOSE.pct_change(100).rank(pct=True) * 100
    ) / 3
    standard = ind.connors_rsi(CLOSE)
    ecart = (standard - legacy).abs().dropna()
    assert ecart.max() > 1, "l'écart documenté devrait être visible"

    tronque = ind.connors_rsi(CLOSE.iloc[:-50])
    pd.testing.assert_series_equal(
        tronque.iloc[-10:], standard.iloc[-60:-50], check_names=False, rtol=1e-12
    )
    print(f"  ✓ écart documenté vs btc-dash (max {ecart.max():.1f} pts),"
          " et la version standard est stable par troncature de l'historique")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nParité du socle d'indicateurs — {len(tests)} groupes\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Tous les tests passent : l'extraction est sans régression.\n")
