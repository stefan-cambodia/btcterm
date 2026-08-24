"""
Constructeurs de figures Plotly du terminal.

C'est la couche de rendu graphique : elle prend un DataFrame déjà enrichi
et produit une figure. Aucun appel réseau, aucun calcul d'indicateur —
ceux-ci viennent de `btcterm`. Le panneau prix n'est plus construit ici :
il dessine en Lightweight Charts côté navigateur (terminal/lwc.py et
assets/lwc-price.js) — ne restent que `prepare_price_frame`, l'étape
d'enrichissement que ce rendu consomme, et les figures des autres
panneaux.

Le paramètre `uirevision` des figures est ce qui rend l'analyse possible
pendant que les données coulent : sans lui, chaque rafraîchissement
réinitialiserait le zoom de l'utilisateur.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from btcterm import indicators as ind

from .theme import C, MONO

__all__ = ["VOL_BINS", "prepare_price_frame",
           "build_depth_chart", "prepare_macro_frame", "macro_stats",
           "build_macro_chart", "build_perp_chart", "build_dominance_chart",
           "build_chain_chart"]

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


# ─────────────────────────────────────────────────────────────
# Dominance : parts de capitalisation
# ─────────────────────────────────────────────────────────────

#: Actifs traités comme des dollars : leur part mesure le cash qui attend
#: sur le marché, pas une concurrence faite au Bitcoin.
STABLES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD"}


def build_dominance_chart(
    shares: dict, top: int = 8, uirevision: str = "dominance"
) -> go.Figure:
    """Parts de capitalisation, en barres horizontales.

    Un instantané, pas une série : CoinGecko réserve l'historique de ces
    agrégats à son offre payante. Le Bitcoin garde sa couleur, les
    stablecoins la leur — leur part ne dit pas la même chose que celle
    d'un concurrent —, et tout ce qui n'entre pas dans le classement est
    regroupé, pour que les barres somment bien à cent.
    """
    fig = go.Figure()
    if not shares:
        fig.add_annotation(
            text="agrégats de marché indisponibles", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(family=MONO, size=11, color=C["muted"]),
        )
    else:
        classement = sorted(shares.items(), key=lambda kv: -kv[1])[:top]
        reste = max(0.0, 100 - sum(part for _, part in classement))
        if reste > 0.05:
            classement.append(("AUTRES", reste))

        noms = [nom for nom, _ in classement][::-1]
        parts = [part for _, part in classement][::-1]
        couleurs = [
            C["yellow"] if nom == "BTC" else
            C["blue"] if nom in STABLES else
            C["muted"] if nom == "AUTRES" else C["cyan"]
            for nom in noms
        ]
        fig.add_trace(go.Bar(
            x=parts, y=noms, orientation="h",
            marker_color=couleurs,
            text=[f"{part:.1f} %" for part in parts],
            textposition="outside", textfont=dict(size=9, color=C["text"]),
            cliponaxis=False,
            hovertemplate="%{y} · %{x:.2f} %<extra></extra>",
        ))

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["grid"],
                       tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=42, t=6, b=4),
        showlegend=False,
        bargap=0.25,
        xaxis=dict(title_text="part de la capitalisation (%)", **axis_common),
        yaxis=dict(**axis_common),
        uirevision=uirevision,
    )
    return fig


# ─────────────────────────────────────────────────────────────
# On-chain : puissance de calcul et difficulté
# ─────────────────────────────────────────────────────────────

def build_chain_chart(
    hashrate: pd.DataFrame,
    difficulty: pd.DataFrame,
    uirevision: str = "onchain",
    maximized: bool = False,
) -> go.Figure:
    """Hashrate en aire, difficulté en marches, sur deux axes.

    Les deux disent la même chose à des rythmes différents : le hashrate
    varie d'heure en heure au gré des mineurs branchés, la difficulté ne
    bouge que tous les 2 016 blocs, en marches d'escalier. L'écart entre
    les deux courbes annonce le sens du prochain ajustement.
    """
    fig = go.Figure()

    if not hashrate.empty:
        fig.add_trace(go.Scatter(
            x=hashrate["time"], y=hashrate["value"] / 1e6, name="hashrate",
            mode="lines", line=dict(color=C["green"], width=1.5),
            fill="tozeroy", fillcolor="rgba(0,212,170,0.07)",
            hovertemplate="%{y:,.0f} EH/s<extra></extra>",
        ))

    if not difficulty.empty:
        fig.add_trace(go.Scatter(
            x=difficulty["time"], y=difficulty["value"] / 1e12, name="difficulté",
            mode="lines", line=dict(color=C["purple"], width=1.6, shape="hv"),
            yaxis="y2", hovertemplate="%{y:,.1f} T<extra></extra>",
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
        yaxis=dict(title_text="hashrate (EH/s)", **axis_common),
        yaxis2=dict(title_text="difficulté (T)", overlaying="y", side="right",
                    showgrid=False, tickfont=dict(size=9, color=C["purple"])),
        uirevision=uirevision,
    )
    if hashrate.empty and difficulty.empty:
        fig.add_annotation(
            text="données de chaîne indisponibles", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(family=MONO, size=11, color=C["muted"]),
        )
    return fig
