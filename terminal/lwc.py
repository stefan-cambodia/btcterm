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
from flask import jsonify, request

from btcterm import sources

from .charts import prepare_price_frame

__all__ = ["OVERLAY_COLUMNS", "PANE_COLUMNS", "VOLUME_MA_COLUMN",
           "TAIL_BARS", "frame_to_bars", "frame_to_lines", "frame_to_volume",
           "serialize_price_frame", "tail_points", "push_payload",
           "register_api"]

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


# ─────────────────────────────────────────────────────────────
# Dernier point — le contrat du canal push
# ─────────────────────────────────────────────────────────────

#: Fenêtre de calcul du dernier point. La plus longue mémoire des séries
#: envoyées au client est la MA 200, puis le rang centile du CRSI (100) ;
#: 250 lignes les couvrent. Les lissages exponentiels (RSI, ATR) portent
#: en théorie toute l'histoire, mais leur poids décroît en (1-α)^n : à
#: 250 lignes l'écart avec le recalcul complet est de l'ordre de 1e-8 —
#: le test de parité le borne.
TAIL_BARS = 250

#: Mémo du dernier point par intervalle : tant que le hub sert le même
#: DataFrame (cache TTL), le calcul ne repart pas — c'est ce qui ramène
#: le coût serveur du panneau prix à un point par rafraîchissement de
#: la source, quelle que soit la cadence du pousseur.
_push_memo: dict[str, tuple[object, dict]] = {}


def tail_points(df: pd.DataFrame, interval: str) -> dict:
    """La dernière bougie et les derniers points d'indicateurs.

    Le calcul ne parcourt que les `TAIL_BARS` dernières lignes : c'est
    la mutation que le canal push transmet à chaque tick, elle n'a pas
    à payer le recalcul de la série entière. `interval` voyage dans le
    paquet pour que le client écarte une mise à jour dépassée quand il
    vient de changer d'échelle.
    """
    prepared = prepare_price_frame(df.iloc[-TAIL_BARS:].reset_index(drop=True))
    last = prepared.iloc[[-1]]
    lines = frame_to_lines(last, OVERLAY_COLUMNS + PANE_COLUMNS
                           + (VOLUME_MA_COLUMN,))
    volume_ma = lines.pop(VOLUME_MA_COLUMN, [])
    return {
        "interval": interval,
        "bar": frame_to_bars(last)[0],
        "volume": frame_to_volume(last)[0],
        "overlays": {name: points[0] for name, points in lines.items()
                     if name in OVERLAY_COLUMNS and points},
        "panes": {name: points[0] for name, points in lines.items()
                  if name in PANE_COLUMNS and points},
        "volume_ma": volume_ma[0] if volume_ma else None,
        "demo": bool(df.attrs.get("demo", False)),
    }


def push_payload(hub, interval: str, limit: int) -> dict:
    """Le dernier point pour le pousseur, mémoïsé sur l'identité du cache.

    Le hub sert le même objet DataFrame pendant toute la durée de vie de
    son cache : tant qu'il n'a pas changé, le paquet précédent ressort
    tel quel. Seule la série de démonstration — régénérée à chaque appel
    en repli hors ligne — repaie le calcul, à la cadence du pousseur.
    """
    df = hub.klines(interval, limit=limit)
    cached = _push_memo.get(interval)
    if cached is not None and cached[0] is df:
        return cached[1]
    payload = tail_points(df, interval)
    _push_memo[interval] = (df, payload)
    return payload


# ─────────────────────────────────────────────────────────────
# Route d'historique
# ─────────────────────────────────────────────────────────────

#: Marge de bougies chargées en amont d'une page d'historique : la MA 200
#: — la plus longue fenêtre du panneau — est ainsi juste dès la première
#: bougie servie, au lieu de mettre 200 bougies à converger sur chaque
#: page. La marge est tronquée après calcul, jamais renvoyée.
WARMUP_BARS = 200

#: Bornes de la taille de page — Binance plafonne `/api/v3/klines` à 1000.
PAGE_MAX = 1000
PAGE_DEFAULT = 365


def register_api(app, hub) -> None:
    """Pose `GET /api/klines` sur le serveur Flask de Dash.

        /api/klines?interval=1h&limit=350           les N dernières bougies
        /api/klines?interval=1h&limit=350&before=T  la page antérieure à T

    `before` est en secondes epoch UTC — le `time` des barres servies —
    et la page rendue s'arrête strictement avant lui : c'est la clé du
    chargement à la volée au pan. Sans `before`, la série passe par
    `hub.klines()` et son repli de démonstration (champ `demo` du JSON) ;
    avec, par le cache d'historique du hub, et une page injoignable ou
    épuisée revient simplement vide.
    """

    @app.server.get("/api/klines")
    def _klines():
        interval = request.args.get("interval", default="1d")
        if interval not in sources.KLINE_FREQ:
            return jsonify({"error": f"intervalle inconnu : {interval}"}), 400
        limit = request.args.get("limit", type=int) or PAGE_DEFAULT
        limit = max(1, min(limit, PAGE_MAX))
        before = request.args.get("before", type=int)

        if before is None:
            df = hub.klines(interval, limit=limit)
        else:
            # endTime est inclusif sur l'heure d'ouverture : la borne à
            # `before - 1 ms` exclut la bougie que le client tient déjà.
            df = hub.klines_history(interval, before * 1000 - 1,
                                    limit + WARMUP_BARS)

        if df.empty:
            frame = pd.DataFrame(columns=["time"] + sources.OHLCV_COLUMNS)
            payload = serialize_price_frame(prepare_price_frame(
                frame.astype({c: float for c in sources.OHLCV_COLUMNS})))
        else:
            prepared = prepare_price_frame(df)
            if before is not None:
                cutoff = pd.Timestamp(before, unit="s")
                prepared = (prepared[prepared["time"] < cutoff]
                            .iloc[-limit:])
            payload = serialize_price_frame(prepared)

        payload["interval"] = interval
        # Le taux voyage avec les données : basculer $/€ est une
        # multiplication côté client, jamais un refetch.
        payload["eur_rate"] = float(hub.eur_rate())
        return jsonify(payload)
