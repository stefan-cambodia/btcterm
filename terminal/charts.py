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

__all__ = ["VOL_BINS", "prepare_price_frame", "build_price_chart",
           "build_depth_chart", "prepare_macro_frame", "macro_stats",
           "build_macro_chart", "build_perp_chart"]

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


SUBPANELS = ("rsi", "crsi", "volume")

#: Part de hauteur laissée au cours selon le nombre de sous-graphiques
#: affichés, dans la grille puis en plein écran. Le cours domine toujours,
#: et d'autant plus quand la place ne manque pas.
PRICE_SHARE = {
    0: (1.00, 1.00),
    1: (0.80, 0.86),
    2: (0.72, 0.80),
    3: (0.66, 0.75),
}


def _row_heights(count: int, maximized: bool) -> list[float]:
    price = PRICE_SHARE[count][1 if maximized else 0]
    if count == 0:
        return [1.0]
    return [price] + [(1 - price) / count] * count


def build_price_chart(
    df: pd.DataFrame,
    currency: str = "USD",
    eur_rate: float = 1.0,
    uirevision: str = "price",
    subpanels: tuple[str, ...] = ("rsi", "volume"),
    profile: bool = True,
    maximized: bool = False,
    log_scale: bool = False,
) -> go.Figure:
    """Chandeliers et moyennes, avec sous-graphiques optionnels.

    `subpanels` choisit lesquels de RSI, CRSI et volume accompagnent le
    cours ; `profile` le profil de volume en colonne de droite. Moins il
    y en a, plus le cours occupe de hauteur — et il en occupe davantage
    en plein écran, où l'analyse fine est l'objet même de l'agrandissement.

    `log_scale` passe l'axe des prix en logarithmique : sur les échelles
    longues — la mensuelle couvre dix ans — une progression de 4 000 à
    80 000 dollars écrase tout le début du graphique en linéaire.
    """
    mult = eur_rate if currency == "EUR" else 1.0
    sym  = "€" if currency == "EUR" else "$"

    subpanels = tuple(s for s in SUBPANELS if s in subpanels)
    rows = 1 + len(subpanels)
    #: Numéro de rangée de chaque sous-graphique, ou None s'il est masqué.
    row_of = {name: i + 2 for i, name in enumerate(subpanels)}

    centers, vp_vols, poc, va_lo, va_hi = volume_profile(df)

    # ── Grille de sous-graphiques ─────────────────
    #   col 1 : cours, puis les sous-graphiques demandés
    #   col 2 : profil de volume, aligné sur l'axe des prix
    kinds = {"rsi": "scatter", "crsi": "scatter", "volume": "bar"}
    if profile:
        specs = [[{"type": "candlestick"}, {"type": "bar", "rowspan": rows}]]
        specs += [[{"type": kinds[name]}, None] for name in subpanels]
        grid = dict(cols=2, column_widths=[0.84, 0.16], specs=specs)
    else:
        specs = [[{"type": "candlestick"}]] + [[{"type": kinds[n]}] for n in subpanels]
        grid = dict(cols=1, specs=specs)

    fig = make_subplots(
        rows=rows,
        row_heights=_row_heights(len(subpanels), maximized),
        shared_xaxes=True,
        vertical_spacing=0.018,
        horizontal_spacing=0.008,
        **grid,
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
    if profile:
        vp_norm = vp_vols / vp_vols.max()
        vp_clr = [
            C["poc"]   if abs(c - poc) < (poc * 0.004) else
            C["green"] if va_lo <= c <= va_hi else
            "rgba(100,116,139,0.5)"
            for c in centers
        ]
        fig.add_trace(go.Bar(
            x=vp_norm, y=centers * mult,
            orientation="h", name="Liq. Clusters", showlegend=False,
            marker=dict(color=vp_clr, opacity=0.75),
            hovertemplate="Vol: %{x:.2f}<br>%{y:,.0f}<extra></extra>",
        ), row=1, col=2)

    # ── 7. Sous-graphique RSI ────────────────────
    if "rsi" in row_of:
        row = row_of["rsi"]
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["rsi"], name="RSI 14", showlegend=False,
            line=dict(color=C["blue"], width=1.4), mode="lines",
            hovertemplate="RSI: %{y:.1f}<extra></extra>",
        ), row=row, col=1)
        fig.add_hrect(y0=70, y1=100, fillcolor="rgba(255,61,107,0.08)",
                      line_width=0, row=row, col=1)
        fig.add_hrect(y0=0, y1=30, fillcolor="rgba(0,212,170,0.08)",
                      line_width=0, row=row, col=1)
        for level, color in ((30, C["green"]), (50, C["muted"]), (70, C["red"])):
            fig.add_hline(y=level, line=dict(color=color, width=0.6, dash="dot"),
                          row=row, col=1)

    # ── 8. Sous-graphique CRSI ───────────────────
    if "crsi" in row_of:
        row = row_of["crsi"]
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["crsi"], name="CRSI", showlegend=False,
            line=dict(color=C["purple"], width=1.4), mode="lines",
            hovertemplate="CRSI: %{y:.1f}<extra></extra>",
        ), row=row, col=1)
        for level in (20, 50, 80):
            fig.add_hline(y=level, line=dict(color=C["muted"], width=0.5, dash="dot"),
                          row=row, col=1)
        fig.add_hrect(y0=80, y1=100, fillcolor="rgba(255,61,107,0.07)",
                      line_width=0, row=row, col=1)
        fig.add_hrect(y0=0, y1=20, fillcolor="rgba(0,212,170,0.07)",
                      line_width=0, row=row, col=1)

    # ── 9. Sous-graphique volume ─────────────────
    if "volume" in row_of:
        row = row_of["volume"]
        colors = [
            C["green"] if close_v >= open_v else C["red"]
            for close_v, open_v in zip(df["close"], df["open"])
        ]
        fig.add_trace(go.Bar(
            x=df["time"], y=df["volume"], name="Volume", showlegend=False,
            marker_color=colors, opacity=0.75,
            hovertemplate="Vol: %{y:,.0f}<extra></extra>",
        ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=df["time"], y=df["vol_ma20"], name="Vol MA20",
            line=dict(color=C["orange"], width=1.2), mode="lines", showlegend=False,
        ), row=row, col=1)

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
    fig.update_yaxes(title_text=f"Price ({sym})", row=1, col=1,
                     type="log" if log_scale else "linear", **axis_common)
    titles = {"rsi": "RSI", "crsi": "CRSI", "volume": "Volume"}
    for name, row in row_of.items():
        bounded = {"range": [0, 100]} if name in ("rsi", "crsi") else {}
        fig.update_yaxes(title_text=titles[name], row=row, col=1,
                         **bounded, **axis_common)
    if profile:
        fig.update_yaxes(showticklabels=False, showgrid=False, row=1, col=2)

    for row in range(1, rows + 1):
        fig.update_xaxes(row=row, col=1, **axis_common)

    # Repli hors ligne : le hub sert une série synthétique quand la source
    # est injoignable. Un graphique de démonstration qui ne se distingue
    # pas d'un vrai serait pire que pas de graphique du tout — d'où ce
    # bandeau, posé en coordonnées de domaine — donc dans le coin du
    # graphique du cours, quel que soit le zoom, et hors de la bande
    # supérieure qu'occupe déjà la légende.
    if df.attrs.get("demo"):
        fig.add_annotation(
            text="⚠ DONNÉES DE DÉMONSTRATION · source injoignable",
            xref="x domain", yref="y domain", x=0.99, y=0.98,
            xanchor="right", yanchor="top", showarrow=False,
            font=dict(family=MONO, size=10, color=C["bg"]),
            bgcolor=C["orange"], borderpad=3,
        )

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


# ─────────────────────────────────────────────────────────────
# Macro : cours contre masse monétaire
# ─────────────────────────────────────────────────────────────

def prepare_macro_frame(
    btc: pd.DataFrame, m2: pd.DataFrame, lag_months: int = 0
) -> pd.DataFrame:
    """Aligne le cours mensuel et la masse monétaire sur le même calendrier.

    Les deux séries ne tombent pas aux mêmes dates — Binance ouvre ses
    bougies au premier du mois, la Fed publie M2 en fin de mois — d'où
    l'alignement par période mensuelle plutôt que par horodatage.

    `lag_months` décale la masse monétaire **vers l'avant** : c'est la
    forme qu'a l'hypothèse courante, où le cours réagit à la liquidité
    avec un ou deux trimestres de retard. La jointure est faite à gauche
    sur le cours, pour que les derniers mois — M2 paraît avec deux mois
    de décalage — restent tracés côté Bitcoin.
    """
    columns = ["time", "btc", "m2"]
    if btc.empty or m2.empty:
        return pd.DataFrame(columns=columns)

    prix = pd.DataFrame({
        "time": pd.to_datetime(btc["time"]),
        "btc": btc["close"].astype(float),
    })
    prix["mois"] = prix["time"].dt.to_period("M")

    masse = pd.DataFrame({
        "mois": pd.to_datetime(m2["time"]).dt.to_period("M") + lag_months,
        "m2": m2["m2"].astype(float),
    })

    frame = prix.merge(masse, on="mois", how="left")
    return frame[columns]


def macro_stats(frame: pd.DataFrame) -> dict:
    """Corrélations et croissance annuelle de la masse monétaire.

    Deux corrélations valent mieux qu'une. Celle des **niveaux** (log du
    cours contre M2) est toujours forte, deux séries qui montent depuis
    dix ans ne pouvant qu'aller ensemble ; elle dit peu. Celle des
    **variations sur trois mois** enlève cette tendance commune et dit ce
    qui reste : les deux séries accélèrent-elles vraiment ensemble ?
    """
    stats = {"niveaux": None, "variations": None, "m2_yoy": None, "points": 0}
    valid = frame.dropna(subset=["btc", "m2"])
    stats["points"] = len(valid)
    if len(valid) < 12:
        return stats

    stats["niveaux"] = float(np.log(valid["btc"]).corr(valid["m2"]))

    variations_btc = valid["btc"].pct_change(3)
    variations_m2 = valid["m2"].pct_change(3)
    stats["variations"] = float(variations_btc.corr(variations_m2))

    if len(valid) >= 13:
        recent, ancien = valid["m2"].iloc[-1], valid["m2"].iloc[-13]
        stats["m2_yoy"] = float((recent / ancien - 1) * 100)
    return stats


def build_macro_chart(
    frame: pd.DataFrame, uirevision: str = "macro", maximized: bool = False
) -> go.Figure:
    """Cours et masse monétaire sur deux axes, l'un logarithmique.

    Deux axes plutôt qu'une normalisation : normaliser oblige à choisir
    une date de départ, et le graphique change de forme selon ce choix.
    L'axe des prix est logarithmique — le Bitcoin a fait ×20 quand M2
    faisait ×1,4, et en linéaire la masse monétaire serait une ligne
    plate collée au bas du cadre.
    """
    fig = go.Figure()

    if not frame.empty:
        fig.add_trace(go.Scatter(
            x=frame["time"], y=frame["btc"], name="BTC",
            line=dict(color=C["yellow"], width=1.8), mode="lines",
            hovertemplate="BTC $%{y:,.0f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=frame["time"], y=frame["m2"] / 1000, name="M2 (US)",
            line=dict(color=C["cyan"], width=1.8), mode="lines", yaxis="y2",
            connectgaps=False,
            hovertemplate="M2 %{y:,.2f} T$<extra></extra>",
        ))

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["grid"],
                       tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=8, t=18 if maximized else 4, b=4),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom",
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(**axis_common),
        yaxis=dict(title_text="BTC ($, log)", type="log", **axis_common),
        yaxis2=dict(title_text="M2 (T$)", overlaying="y", side="right",
                    showgrid=False, tickfont=dict(size=9, color=C["cyan"])),
        uirevision=uirevision,
    )
    if frame.empty:
        fig.add_annotation(
            text="masse monétaire indisponible", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(family=MONO, size=11, color=C["muted"]),
        )
    return fig


# ─────────────────────────────────────────────────────────────
# Perpétuels : financement et open interest
# ─────────────────────────────────────────────────────────────

def build_perp_chart(
    funding: pd.DataFrame,
    open_interest: pd.DataFrame,
    uirevision: str = "perp",
    maximized: bool = False,
) -> go.Figure:
    """Financement en barres, open interest en ligne, sur deux axes.

    Les deux se lisent ensemble : un financement qui grimpe pendant que
    l'open interest gonfle signale un marché à terme qui s'endette d'un
    seul côté — la configuration d'où sortent les liquidations en
    cascade. Le signe colore les barres : positif, les longs paient les
    shorts.
    """
    fig = go.Figure()

    if not funding.empty:
        rates = funding["rate"] * 100
        fig.add_trace(go.Bar(
            x=funding["time"], y=rates, name="financement",
            marker_color=[C["green"] if r >= 0 else C["red"] for r in rates],
            hovertemplate="%{y:.4f} %<extra></extra>",
        ))

    if not open_interest.empty:
        fig.add_trace(go.Scatter(
            x=open_interest["time"], y=open_interest["oi_usd"] / 1e9,
            name="open interest", yaxis="y2", mode="lines",
            line=dict(color=C["cyan"], width=1.8),
            hovertemplate="OI %{y:.2f} Md$<extra></extra>",
        ))

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["border"],
                       tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=8, t=18 if maximized else 4, b=4),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom",
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        bargap=0.2,
        xaxis=dict(**axis_common),
        yaxis=dict(title_text="financement (%)", **axis_common),
        yaxis2=dict(title_text="OI (Md$)", overlaying="y", side="right",
                    showgrid=False, tickfont=dict(size=9, color=C["cyan"])),
        uirevision=uirevision,
    )
    if funding.empty and open_interest.empty:
        fig.add_annotation(
            text="marché à terme indisponible", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(family=MONO, size=11, color=C["muted"]),
        )
    return fig
