"""
Constructeurs de figures Plotly du terminal.

C'est la couche de rendu graphique : elle prend un DataFrame déjà enrichi
et produit une figure. Aucun appel réseau, aucun calcul d'indicateur —
ceux-ci viennent de `btcterm`.

Le paramètre `uirevision` des figures est ce qui rend l'analyse possible
pendant que les données coulent : sans lui, chaque rafraîchissement
réinitialiserait le zoom de l'utilisateur.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from btcterm import indicators as ind

from .theme import C, MONO

__all__ = ["VOL_BINS", "prepare_price_frame", "build_price_chart", "build_depth_chart"]

VOL_BINS = 60


def prepare_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Enrichit un OHLCV brut des colonnes attendues par le graphique prix."""
    df = df.copy()

    df["ma9"] = ind.sma(df["close"], 9)
    df["ma26"] = ind.sma(df["close"], 26)
    df["ma200"] = ind.sma(df["close"], 200)

    df["bb_mid"], df["bb_upper"], df["bb_lower"] = ind.bollinger(df["close"], 20, 2)

    df["rsi"] = ind.rsi(df["close"], 14)
    df["crsi"] = ind.connors_rsi(df["close"])

    df["vol_252"] = ind.volatility(df["close"], 252)
    df["atr"] = ind.atr(df, 14)
    df["vol_ma20"] = ind.sma(df["volume"], 20)

    df["signal"] = ind.graded_signals(df, fast="ma9", slow="ma26", trend="ma200")
    return df


def volume_profile(df: pd.DataFrame, bins: int = VOL_BINS):
    """Profil de volume : centres, volumes, POC et bornes de la Value Area."""
    return ind.volume_profile(df, bins)


def build_price_chart(
    df: pd.DataFrame,
    currency: str = "USD",
    eur_rate: float = 1.0,
    uirevision: str = "price",
) -> go.Figure:
    """Chandeliers, moyennes, Bollinger, POC/Value Area, RSI, CRSI, volume."""
    mult = eur_rate if currency == "EUR" else 1.0
    sym  = "€" if currency == "EUR" else "$"

    centers, vp_vols, poc, va_lo, va_hi = volume_profile(df)

    # ── Subplot grid ──────────────────────────────
    #   col 1 (82%) : candles / RSI / CRSI / Volume
    #   col 2 (18%) : Volume Profile (shares row 1 y-axis, spanning all rows)
    fig = make_subplots(
        rows=4, cols=2,
        column_widths=[0.82, 0.18],
        row_heights=[0.54, 0.17, 0.13, 0.16],
        shared_xaxes=True,
        vertical_spacing=0.018,
        horizontal_spacing=0.008,
        specs=[
            [{"type": "candlestick"}, {"type": "bar", "rowspan": 4}],
            [{"type": "scatter"},     None],
            [{"type": "scatter"},     None],
            [{"type": "bar"},         None],
        ],
    )

    px  = lambda s: s * mult          # price in chosen currency
    pxv = lambda v: v                 # volumes stay raw

    close = px(df["close"]); high = px(df["high"])
    low   = px(df["low"]);   open_ = px(df["open"])

    # ── 1. Candlesticks ──────────────────────────
    fig.add_trace(go.Candlestick(
        x=df["time"], open=open_, high=high, low=low, close=close,
        name="BTC", showlegend=False,
        increasing_fillcolor=C["green"], increasing_line_color=C["green"],
        decreasing_fillcolor=C["red"],   decreasing_line_color=C["red"],
        line_width=1, whiskerwidth=0.4,
    ), row=1, col=1)

    # ── 2. Moving Averages ───────────────────────
    for col, name, color, width, dash in [
        ("ma9",   "MA 9",   C["ma9"],   1.6, "solid"),
        ("ma26",  "MA 26",  C["ma26"],  1.6, "solid"),
        ("ma200", "MA 200", C["ma200"], 2.0, "dot"),
    ]:
        if df[col].notna().any():
            fig.add_trace(go.Scatter(
                x=df["time"], y=px(df[col]), name=name,
                line=dict(color=color, width=width, dash=dash), mode="lines",
                hovertemplate=f"{name}: {sym}%{{y:,.0f}}<extra></extra>",
            ), row=1, col=1)

    # ── 3. Bollinger Bands ───────────────────────
    fig.add_trace(go.Scatter(
        x=df["time"], y=px(df["bb_upper"]), name="BB Upper",
        line=dict(color=C["bb"], width=0.7, dash="dash"),
        mode="lines", showlegend=False,
        hovertemplate=f"BB Upper: {sym}%{{y:,.0f}}<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df["time"], y=px(df["bb_lower"]), name="BB Lower",
        line=dict(color=C["bb"], width=0.7, dash="dash"),
        fill="tonexty", fillcolor="rgba(59,130,246,0.04)",
        mode="lines", showlegend=False,
        hovertemplate=f"BB Lower: {sym}%{{y:,.0f}}<extra></extra>",
    ), row=1, col=1)

    # ── 4. Point of Control & Value Area ─────────
    fig.add_hline(
        y=poc * mult, row=1, col=1,
        line=dict(color=C["poc"], width=1.4, dash="dash"),
        annotation_text=f" POC  {sym}{poc*mult:,.0f}",
        annotation_position="left",
        annotation_font=dict(color=C["poc"], size=10),
    )
    fig.add_hrect(
        y0=va_lo * mult, y1=va_hi * mult, row=1, col=1,
        fillcolor=C["va"], line_width=0,
    )
    fig.add_hline(
        y=va_hi * mult, row=1, col=1,
        line=dict(color=C["poc"], width=0.5, dash="dot"),
        annotation_text=f" VAH  {sym}{va_hi*mult:,.0f}",
        annotation_position="left",
        annotation_font=dict(color=C["poc"], size=9),
    )
    fig.add_hline(
        y=va_lo * mult, row=1, col=1,
        line=dict(color=C["poc"], width=0.5, dash="dot"),
        annotation_text=f" VAL  {sym}{va_lo*mult:,.0f}",
        annotation_position="left",
        annotation_font=dict(color=C["poc"], size=9),
    )

    # ── 5. Buy / Sell signal markers ─────────────
    for sig_val, label, marker, offset, color in [
        ( 2, "STRONG BUY",  "triangle-up",   0.994, C["buy"]),
        ( 1, "BUY",         "triangle-up",   0.997, C["green"]),
        (-1, "SELL",        "triangle-down", 1.003, C["red"]),
        (-2, "STRONG SELL", "triangle-down", 1.006, C["sell"]),
    ]:
        subset = df[df["signal"] == sig_val]
        if subset.empty:
            continue
        y_pos = (subset["low"] if sig_val > 0 else subset["high"]) * mult * offset
        fig.add_trace(go.Scatter(
            x=subset["time"], y=y_pos,
            mode="markers", name=label,
            marker=dict(
                symbol=marker,
                size=13 if abs(sig_val) == 2 else 9,
                color=color,
                opacity=1.0 if abs(sig_val) == 2 else 0.7,
                line=dict(width=0),
            ),
            hovertemplate=f"{label}<br>{sym}%{{y:,.0f}}<extra></extra>",
        ), row=1, col=1)

    # ── 6. Volume Profile (horizontal bars) ──────
    vp_norm = vp_vols / vp_vols.max()
    vp_clr  = [
        C["poc"]    if abs(c - poc) < (poc * 0.004) else
        C["green"]  if va_lo <= c <= va_hi else
        "rgba(100,116,139,0.5)"
        for c in centers
    ]
    fig.add_trace(go.Bar(
        x=vp_norm, y=centers * mult,
        orientation="h", name="Liq. Clusters", showlegend=False,
        marker=dict(color=vp_clr, opacity=0.75),
        hovertemplate="Vol: %{x:.2f}<br>%{y:,.0f}<extra></extra>",
    ), row=1, col=2)

    # ── 7. RSI panel ─────────────────────────────
    rsi_clr = [
        C["red"]   if v > 70 else
        C["green"] if v < 30 else
        C["blue"]
        for v in df["rsi"].fillna(50)
    ]
    fig.add_trace(go.Scatter(
        x=df["time"], y=df["rsi"], name="RSI 14", showlegend=False,
        line=dict(color=C["blue"], width=1.4), mode="lines",
        hovertemplate="RSI: %{y:.1f}<extra></extra>",
    ), row=2, col=1)
    fig.add_hrect(y0=70, y1=100, fillcolor="rgba(255,61,107,0.08)", line_width=0, row=2, col=1)
    fig.add_hrect(y0=0,  y1=30,  fillcolor="rgba(0,212,170,0.08)",  line_width=0, row=2, col=1)
    for lvl, c in ((30, C["green"]), (50, C["muted"]), (70, C["red"])):
        fig.add_hline(y=lvl, line=dict(color=c, width=0.6, dash="dot"), row=2, col=1)

    # ── 8. CRSI panel ────────────────────────────
    fig.add_trace(go.Scatter(
        x=df["time"], y=df["crsi"], name="CRSI", showlegend=False,
        line=dict(color=C["purple"], width=1.4), mode="lines",
        hovertemplate="CRSI: %{y:.1f}<extra></extra>",
    ), row=3, col=1)
    for lvl in (20, 50, 80):
        fig.add_hline(y=lvl, line=dict(color=C["muted"], width=0.5, dash="dot"), row=3, col=1)
    fig.add_hrect(y0=80, y1=100, fillcolor="rgba(255,61,107,0.07)", line_width=0, row=3, col=1)
    fig.add_hrect(y0=0,  y1=20,  fillcolor="rgba(0,212,170,0.07)",  line_width=0, row=3, col=1)

    # ── 9. Volume bars ───────────────────────────
    v_clr = [
        C["green"] if df["close"].iloc[i] >= df["open"].iloc[i] else C["red"]
        for i in range(len(df))
    ]
    fig.add_trace(go.Bar(
        x=df["time"], y=df["volume"], name="Volume", showlegend=False,
        marker_color=v_clr, opacity=0.75,
        hovertemplate="Vol: %{y:,.0f}<extra></extra>",
    ), row=4, col=1)
    fig.add_trace(go.Scatter(
        x=df["time"], y=df["vol_ma20"], name="Vol MA20",
        line=dict(color=C["orange"], width=1.2), mode="lines", showlegend=False,
    ), row=4, col=1)

    # ── Layout ───────────────────────────────────
    axis_common = dict(
        gridcolor=C["grid"], zerolinecolor=C["grid"],
        showgrid=True, tickfont=dict(size=9, color=C["muted"]),
    )
    fig.update_layout(
        paper_bgcolor=C["bg"],
        plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=11),
        # La marge haute accueille la légende : posée sur le tracé, elle
        # recouvrait les bougies dès que le panneau était petit.
        margin=dict(l=12, r=12, t=44, b=8),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=11),
        legend=dict(
            bgcolor="rgba(0,0,0,0)", orientation="h",
            x=0.0, y=1.0, yanchor="bottom", font=dict(size=10),
            itemsizing="constant",
        ),
        xaxis_rangeslider_visible=False,
        dragmode="zoom",
        newshape_line_color=C["cyan"],
        modebar=dict(bgcolor="rgba(0,0,0,0)", color=C["muted"],
                     activecolor=C["text"]),
        # Conserve zoom, pan et état de la légende d'un rafraîchissement à
        # l'autre : sans cela, analyser une zone serait impossible pendant
        # que les données coulent. La valeur ne change que si l'on change
        # de série (intervalle, devise), ce qui recadre alors volontairement.
        uirevision=uirevision,
    )

    # Axes configuration
    fig.update_yaxes(title_text=f"Price ({sym})",  row=1, col=1, **axis_common)
    fig.update_yaxes(title_text="RSI",     row=2, col=1, range=[0, 100], **axis_common)
    fig.update_yaxes(title_text="CRSI",    row=3, col=1, range=[0, 100], **axis_common)
    fig.update_yaxes(title_text="Volume",  row=4, col=1, **axis_common)
    fig.update_yaxes(showticklabels=False, showgrid=False, row=1, col=2)

    for r in range(1, 5):
        fig.update_xaxes(row=r, col=1, **axis_common)

    return fig


def build_depth_chart(books: dict, uirevision: str = "depth") -> go.Figure:
    """Profondeur cumulée superposée, une couleur par plateforme.

    Les carnets sont recentrés en pourcentage d'écart au prix médian : les
    plateformes ne cotent pas exactement le même prix, et les superposer
    en valeur absolue rendrait la comparaison illisible.
    """
    palette = [C["yellow"], C["blue"], C["purple"], C["cyan"], C["green"]]
    fig = go.Figure()

    for (name, book), color in zip(books.items(), palette):
        mid = book.mid
        if mid is None:
            continue
        for side, dash in (("bids", "solid"), ("asks", "solid")):
            prices, cumulated = book.cumulative_depth(side)
            if not prices:
                continue
            fig.add_trace(go.Scatter(
                x=[(p - mid) / mid * 100 for p in prices],
                y=cumulated,
                name=f"{name} {side}",
                line=dict(color=color, width=1.4, dash=dash, shape="hv"),
                fill="tozeroy",
                fillcolor="rgba(255,255,255,0.03)",
                showlegend=(side == "bids"),
                legendgroup=name,
                hovertemplate=f"{name} · %{{x:.3f}} %<br>%{{y:.3f}} BTC<extra></extra>",
            ))

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["border"],
                       tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=8, t=4, b=4),
        showlegend=True,
        legend=dict(orientation="h", y=1.12, x=0, font=dict(size=9),
                    bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        uirevision=uirevision,
    )
    fig.update_xaxes(title_text="écart au prix médian (%)", **axis_common)
    fig.update_yaxes(title_text="volume cumulé (BTC)", **axis_common)
    return fig
