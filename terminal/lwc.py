"""
Sérialisation du panneau prix pour Lightweight Charts.

Le rendu Plotly sérialise une figure entière ; Lightweight Charts (la
bibliothèque de TradingView, vendorée dans `assets/vendor/`, v5.2.1) ne
veut que des données : des barres `{time, open, high, low, close}` et des
lignes `{time, value}`, le temps en secondes epoch UTC, triées et sans
doublon. Ce module est l'unique traducteur entre les DataFrames du
serveur — `hub.klines()` enrichi par `prepare_price_frame` — et ce
contrat. Le serveur reste la seule source de vérité pour les indicateurs ;
le navigateur ne fait que dessiner.

Aucun appel réseau, aucun calcul d'indicateur : uniquement de la mise en
forme, testable sans navigateur (tests/test_lwc_serialize.py). La
sérialisation doit être **stable** — deux appels sur les mêmes données
produisent le même JSON — car le canal push différentiel compare des
chaînes pour décider ce qui repart.
"""

from __future__ import annotations

import pandas as pd

__all__ = ["OVERLAY_COLUMNS", "PANE_COLUMNS", "VOLUME_MA_COLUMN",
           "frame_to_bars", "frame_to_lines", "frame_to_volume",
           "serialize_price_frame"]

#: Indicateurs tracés par-dessus le cours, dans l'ordre où le client les
#: pose. Les noms sont les colonnes de `prepare_price_frame` — le client
#: ne connaît que ces clés, jamais le calcul derrière.
OVERLAY_COLUMNS = ("ma9", "ma26", "ma200", "bb_upper", "bb_mid", "bb_lower")

#: Oscillateurs bornés 0-100, chacun dans son pane sous le cours.
PANE_COLUMNS = ("rsi", "crsi")

#: Moyenne mobile tracée sur l'histogramme de volume.
VOLUME_MA_COLUMN = "vol_ma20"


def _val(value: float) -> float:
    """Un flottant borné à 10 chiffres significatifs.

    La précision complète du float64 double le poids du JSON sans rien
    apporter : dix chiffres, c'est déjà mille fois plus fin que le tick
    Binance. L'arrondi passe par le format `g` pour valoir autant sur un
    prix à cinq chiffres que sur un RSI à deux.
    """
    return float(f"{value:.10g}")


def _with_epoch(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Les colonnes demandées, indexées par le temps en secondes epoch UTC.

    Le contrat de Lightweight Charts : timestamps entiers, strictement
    croissants, sans doublon. Les bougies Binance arrivent déjà ainsi,
    mais le contrat est garanti ici plutôt que supposé — un doublon garde
    sa dernière valeur, la plus fraîche.
    """
    frame = df.loc[:, ["time"] + columns].copy()
    # Les horodatages du hub sont naïfs et déjà en UTC (open_time Binance).
    # Le passage explicite par la nanoseconde rend la conversion en
    # secondes indépendante de la résolution du DataFrame d'entrée.
    frame["time"] = (pd.to_datetime(frame["time"]).astype("datetime64[ns]")
                     .astype("int64") // 1_000_000_000)
    frame = frame.drop_duplicates(subset="time", keep="last").sort_values("time")
    return frame


def frame_to_bars(df: pd.DataFrame) -> list[dict]:
    """Barres OHLC au format `setData` d'une série de chandeliers."""
    frame = _with_epoch(df, ["open", "high", "low", "close"]).dropna()
    return [
        {"time": int(t), "open": _val(o), "high": _val(h),
         "low": _val(l), "close": _val(c)}
        for t, o, h, l, c in frame.itertuples(index=False)
    ]


def frame_to_lines(
    df: pd.DataFrame, columns: tuple[str, ...]
) -> dict[str, list[dict]]:
    """Une liste `{time, value}` par colonne demandée, NaN filtrés.

    Les fenêtres glissantes laissent des NaN en tête de série (la MA 200
    n'existe qu'à partir de la 200e bougie) : Lightweight Charts refuse
    les valeurs nulles, chaque ligne commence donc à son premier point
    défini. Une colonne absente du DataFrame est simplement omise.
    """
    present = [name for name in columns if name in df.columns]
    frame = _with_epoch(df, present)
    return {
        name: [
            {"time": int(t), "value": _val(v)}
            for t, v in frame.loc[:, ["time", name]].dropna()
                             .itertuples(index=False)
        ]
        for name in present
    }


def frame_to_volume(df: pd.DataFrame) -> list[dict]:
    """Histogramme de volume : `{time, value, up}`.

    `up` dit si la bougie est haussière ; la couleur appartient au client
    — le thème n'a qu'une définition, côté navigateur, et le serveur n'a
    pas à sérialiser un code hexadécimal par bougie.
    """
    frame = _with_epoch(df, ["volume", "open", "close"]).dropna()
    return [
        {"time": int(t), "value": _val(v), "up": bool(c >= o)}
        for t, v, o, c in frame.itertuples(index=False)
    ]


def serialize_price_frame(df: pd.DataFrame) -> dict:
    """Le panneau prix entier, prêt pour `/api/klines` et le canal push.

    `df` est la sortie de `prepare_price_frame` ; le résultat regroupe
    les barres, le volume et toutes les séries d'indicateurs, plus le
    drapeau `demo` que le hub pose sur sa série de repli hors ligne —
    le client s'en sert pour afficher le bandeau d'avertissement.
    """
    return {
        "bars": frame_to_bars(df),
        "volume": frame_to_volume(df),
        "overlays": frame_to_lines(df, OVERLAY_COLUMNS),
        "panes": frame_to_lines(df, PANE_COLUMNS),
        "volume_ma": frame_to_lines(df, (VOLUME_MA_COLUMN,)).get(
            VOLUME_MA_COLUMN, []),
        "demo": bool(df.attrs.get("demo", False)),
    }
