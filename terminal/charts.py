"""
Constructeurs de figures Plotly du terminal.

C'est la couche de rendu graphique : elle prend un DataFrame déjà enrichi
et produit une figure. Aucun appel réseau, aucun calcul d'indicateur —
ceux-ci viennent de `btcterm`. Les panneaux prix et perpétuel ne sont
plus construits ici : ils dessinent en Lightweight Charts côté navigateur
(terminal/lwc.py, assets/lwc-price.js et lwc-perp.js) — ne restent que
`prepare_price_frame`, l'étape d'enrichissement que le rendu du prix
consomme, et les figures des autres panneaux.

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
from btcterm.sources import STABLES

from .theme import C, MONO

__all__ = ["VOL_BINS", "prepare_price_frame",
           "build_depth_chart", "prepare_macro_frame", "macro_stats",
           "build_macro_chart", "build_dominance_chart",
           "build_chain_chart", "build_fear_greed_chart",
           "fear_greed_color", "FEAR_GREED_ZONES",
           "build_etf_chart", "etf_stats", "etf_issuers",
           "etf_total_column", "format_flow",
           "ETF_WINDOWS", "ETF_DEFAULT_WINDOW"]

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

# ─────────────────────────────────────────────────────────────
# Dominance : parts de capitalisation
# ─────────────────────────────────────────────────────────────

def build_dominance_chart(
    shares: dict,
    history: pd.DataFrame | None = None,
    top: int = 8,
    uirevision: str = "dominance",
) -> go.Figure:
    """Parts de capitalisation en barres, tendance journalisée en dessous.

    CoinGecko réserve l'historique de ces agrégats à son offre payante :
    l'instantané vient de l'API, la tendance vient du journal local
    (§ hub.market_snapshots) — elle n'apparaît qu'une fois deux
    instantanés accumulés, et s'allonge séance après séance. Le Bitcoin
    garde sa couleur, les stablecoins la leur — leur part ne dit pas la
    même chose que celle d'un concurrent —, et tout ce qui n'entre pas
    dans le classement est regroupé, pour que les barres somment à cent.
    """
    trend = (history is not None and not history.empty
             and history["btc_dominance"].notna().sum() >= 2)
    if trend:
        fig = make_subplots(rows=2, cols=1, row_heights=[0.60, 0.40],
                            vertical_spacing=0.16)
        bar_target = dict(row=1, col=1)
    else:
        fig = go.Figure()
        bar_target = {}

    if not shares:
        fig.add_annotation(
            text="agrégats de marché indisponibles", xref="paper", yref="paper",
            x=0.5, y=0.85 if trend else 0.5, showarrow=False,
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
            cliponaxis=False, showlegend=False,
            hovertemplate="%{y} · %{x:.2f} %<extra></extra>",
        ), **bar_target)

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["grid"],
                       tickfont=dict(size=9, color=C["muted"]))

    if trend:
        btc = history.dropna(subset=["btc_dominance"])
        fig.add_trace(go.Scatter(
            x=btc["time"], y=btc["btc_dominance"], name="BTC",
            mode="lines", line=dict(color=C["yellow"], width=1.6),
            hovertemplate="BTC %{y:.1f} %<extra></extra>",
        ), row=2, col=1)
        stables = history.dropna(subset=["stable_share"])
        if not stables.empty:
            fig.add_trace(go.Scatter(
                x=stables["time"], y=stables["stable_share"],
                name="stables", mode="lines",
                line=dict(color=C["blue"], width=1.4),
                hovertemplate="stables %{y:.1f} %<extra></extra>",
            ), row=2, col=1)
        fig.update_xaxes(title_text="part de la capitalisation (%)",
                         row=1, col=1, **axis_common)
        fig.update_yaxes(row=1, col=1, **axis_common)
        fig.update_xaxes(row=2, col=1, **axis_common)
        fig.update_yaxes(title_text="dominance (%)", row=2, col=1,
                         **axis_common)
        fig.update_layout(
            showlegend=True,
            legend=dict(orientation="h", y=0.36, x=0, yanchor="bottom",
                        font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                            font_color=C["text"], font_size=10),
        )
    else:
        fig.update_layout(
            showlegend=False,
            xaxis=dict(title_text="part de la capitalisation (%)",
                       **axis_common),
            yaxis=dict(**axis_common),
        )

    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=42, t=6, b=4),
        bargap=0.25,
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


# ─────────────────────────────────────────────────────────────
# Fear & Greed : l'humeur du marché dans le temps
# ─────────────────────────────────────────────────────────────

#: Découpage d'alternative.me, borne haute exclue : intitulé, couleur et
#: opacité de la bande. Trois couleurs pour cinq zones — les deux zones
#: extrêmes ne sont que la teinte de leur voisine, appuyée. C'est la
#: convention du badge du panneau news (rouge sous 45, vert au-dessus de
#: 55), et une seule vaut mieux que deux : le chiffre du jour et la
#: bande où la courbe le pose doivent être de la même couleur, sans quoi
#: le panneau se contredit tout seul.
FEAR_GREED_ZONES = (
    (0, 25, "peur extrême", "red", 0.13),
    (25, 45, "peur", "red", 0.06),
    (45, 55, "neutre", "yellow", 0.05),
    (55, 75, "avidité", "green", 0.06),
    (75, 101, "avidité extrême", "green", 0.13),
)


def fear_greed_color(value: int) -> str:
    """Couleur de la zone où tombe une valeur de l'indice.

    Source unique de la règle : le badge du panneau news s'en sert aussi.
    """
    for low, high, _, color, _opacity in FEAR_GREED_ZONES:
        if low <= value < high:
            return C[color]
    return C["muted"]


def build_fear_greed_chart(
    history: list[dict],
    uirevision: str = "fng",
) -> go.Figure:
    """L'indice Fear & Greed dans le temps, sur ses bandes de lecture.

    Le chiffre du jour ne dit presque rien seul : 30 n'a pas le même sens
    selon qu'on descende de 70 ou qu'on remonte de 15. La courbe rend
    visibles les deux choses qui comptent — la pente, et le temps passé
    dans une zone, une capitulation étant un plancher tenu plusieurs
    semaines, pas une journée rouge.

    Les bandes colorées portent la lecture ; l'axe est figé sur 0–100
    pour qu'un mois calme ne se dilate pas en montagnes russes.
    """
    fig = go.Figure()

    if not history:
        fig.add_annotation(
            text="indice Fear & Greed indisponible", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False,
            font=dict(family=MONO, size=11, color=C["muted"]),
        )
    else:
        for low, high, _, color, opacity in FEAR_GREED_ZONES:
            fig.add_hrect(y0=low, y1=min(high, 100), line_width=0,
                          fillcolor=C[color], opacity=opacity, layer="below")

        times = [point["time"] for point in history]
        values = [point["value"] for point in history]
        labels = [point["label"] for point in history]

        fig.add_trace(go.Scatter(
            x=times, y=values, mode="lines",
            line=dict(color=C["text"], width=1.6),
            customdata=labels,
            hovertemplate="%{x|%d %b %Y}<br>%{y}/100 · %{customdata}"
                          "<extra></extra>",
        ))
        # Le dernier point marqué et chiffré : c'est celui qu'on vient
        # lire, et il doit se trouver sans suivre la courbe des yeux.
        dernier = history[-1]
        couleur = fear_greed_color(dernier["value"])
        fig.add_annotation(
            text=f"Fear &amp; Greed · {len(history)} j", xref="paper", yref="paper",
            x=0, y=1, xanchor="left", yanchor="top", showarrow=False,
            font=dict(family=MONO, size=9, color=C["muted"]),
        )
        fig.add_trace(go.Scatter(
            x=[dernier["time"]], y=[dernier["value"]], mode="markers+text",
            marker=dict(color=couleur, size=8,
                        line=dict(color=C["panel"], width=1)),
            text=[f" {dernier['value']}"], textposition="middle right",
            textfont=dict(family=MONO, size=11, color=couleur),
            cliponaxis=False, hoverinfo="skip",
        ))

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["grid"],
                       tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=26, t=6, b=4),
        showlegend=False,
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        xaxis=dict(**axis_common),
        yaxis=dict(range=[0, 100], dtick=25, fixedrange=True, **axis_common),
        uirevision=uirevision,
    )
    return fig


# ─────────────────────────────────────────────────────────────
# Flux ETF : ce que les institutions achètent
# ─────────────────────────────────────────────────────────────

#: Fenêtres d'observation, en jours **ouvrés** — le tableau de farside
#: n'a pas de lignes le week-end. `None` prend tout l'historique, qui
#: remonte au lancement des ETF en janvier 2024.
ETF_WINDOWS = {"30 J": 30, "90 J": 90, "1 AN": 252, "TOUT": None}
ETF_DEFAULT_WINDOW = "30 J"


def etf_total_column(frame: pd.DataFrame) -> str:
    """Colonne du flux net agrégé — la dernière si farside la renomme."""
    return "Total" if "Total" in frame.columns else frame.columns[-1]


def etf_issuers(frame: pd.DataFrame) -> list[str]:
    """Colonnes des émetteurs, le total et la date exclus."""
    total = etf_total_column(frame)
    return [c for c in frame.columns if c not in ("Date", total)]


def format_flow(value: float) -> str:
    """Montant de flux, en M$ ou en Md$ selon l'ordre de grandeur."""
    if abs(value) >= 1000:
        return f"{value / 1000:+,.1f} Md$"
    return f"{value:+,.0f} M$"


def etf_stats(frame: pd.DataFrame, days: int | None = None) -> dict:
    """Chiffres clés des flux, sur la fenêtre et depuis le lancement.

    Le cumul depuis le lancement est calculé sur l'historique entier, pas
    sur la fenêtre : c'est le stock détenu par les ETF qui compte, et il
    ne dépend pas de la période qu'on regarde.
    """
    stats = {"last": None, "last_date": None, "five": None, "window": None,
             "days": 0, "cumul": None, "best": None, "worst": None,
             "full": False}
    if frame.empty:
        return stats

    total = etf_total_column(frame)
    view = frame if days is None else frame.tail(days)
    flux = view[total]

    stats["days"] = len(view)
    # Fenêtre couvrant tout l'historique : le net de la fenêtre et le
    # stock sont alors le même chiffre, et l'afficher deux fois n'apprend
    # rien.
    stats["full"] = len(view) >= len(frame)
    stats["cumul"] = float(frame[total].sum())
    stats["window"] = float(flux.sum())
    stats["five"] = float(frame[total].tail(5).sum())
    stats["last"] = float(frame[total].iloc[-1])
    stats["last_date"] = frame["Date"].iloc[-1]
    if not flux.empty:
        stats["best"] = (view["Date"].iloc[flux.argmax()], float(flux.max()))
        stats["worst"] = (view["Date"].iloc[flux.argmin()], float(flux.min()))
    return stats


def build_etf_chart(
    frame: pd.DataFrame,
    days: int | None = 30,
    maximized: bool = False,
    uirevision: str = "etf",
) -> go.Figure:
    """Flux quotidiens et, en plein écran, le cumul et les émetteurs.

    Trois lectures que la barre du jour ne donne pas :

    - le **cumul** depuis le lancement, en courbe sur l'axe droit. Les
      barres quotidiennes sont bruyantes ; c'est la pente du cumul qui
      dit si les institutions accumulent ou distribuent ;
    - la **répartition par émetteur** sur la fenêtre. Le total masque
      des mouvements opposés — GBTC a passé deux ans à saigner pendant
      qu'IBiT encaissait, et un total nul peut cacher les deux ;
    - la fenêtre elle-même, réglable, l'histoire de ces flux ne se lisant
      pas à la même échelle sur un mois et sur deux ans.

    Dans la grille, seules les barres restent : la cellule y fait deux
    cents pixels de haut, et un graphique à deux volets n'y serait qu'un
    brouillon illisible.
    """
    if frame.empty:
        return _etf_empty("flux ETF indisponibles")

    total = etf_total_column(frame)
    view = frame if days is None else frame.tail(days)
    cumul = frame[total].cumsum().tail(len(view))

    if maximized:
        fig = make_subplots(
            rows=1, cols=2, column_widths=[0.76, 0.24], horizontal_spacing=0.07,
            specs=[[{"secondary_y": True}, {}]],
            subplot_titles=("flux quotidiens et cumul depuis le lancement",
                            "cumul par émetteur sur la fenêtre"),
        )
        cible = dict(row=1, col=1)
    else:
        fig = go.Figure()
        cible = {}

    fig.add_trace(go.Bar(
        x=view["Date"], y=view[total], name="flux net",
        marker_color=[C["green"] if v >= 0 else C["red"] for v in view[total]],
        hovertemplate="%{x|%d %b %Y}<br>%{y:+,.1f} M$<extra></extra>",
        showlegend=False,
    ), **({**cible, "secondary_y": False} if maximized else {}))

    if maximized:
        fig.add_trace(go.Scatter(
            x=view["Date"], y=cumul / 1000, name="cumul",
            mode="lines", line=dict(color=C["yellow"], width=1.8),
            hovertemplate="cumul %{y:,.1f} Md$<extra></extra>",
            showlegend=False,
        ), row=1, col=1, secondary_y=True)

        _etf_ranking(fig, view, etf_issuers(frame))

    axis = dict(gridcolor=C["grid"], zerolinecolor=C["border"],
                tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=34 if maximized else 8,
                    t=22 if maximized else 4, b=4),
        showlegend=False, bargap=0.2,
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        uirevision=uirevision,
    )
    if maximized:
        fig.update_xaxes(**axis)
        fig.update_yaxes(title_text="flux net (M$)", row=1, col=1,
                         secondary_y=False, **axis)
        # Ni grille ni ligne de zéro sur l'axe du cumul : celui-ci part
        # de zéro, et sa ligne de zéro barrait le bas du cadre d'un trait
        # blanc que rien ne justifiait.
        fig.update_yaxes(title_text="cumul (Md$)", row=1, col=1,
                         secondary_y=True, showgrid=False, zeroline=False,
                         tickfont=dict(size=9, color=C["yellow"]))
        fig.update_yaxes(row=1, col=2, **axis)
        fig.update_xaxes(row=1, col=2, showticklabels=False, **axis)
        for note in fig.layout.annotations:
            note.font = dict(family=MONO, size=9, color=C["muted"])
    else:
        fig.update_xaxes(**axis)
        fig.update_yaxes(**axis)
    return fig


def _etf_ranking(fig: go.Figure, view: pd.DataFrame, issuers: list[str]) -> None:
    """Classement horizontal des cumuls par émetteur, sur la fenêtre.

    Trié croissant : Plotly empile les barres horizontales du bas vers le
    haut, et c'est le plus gros collecteur qu'on veut lire en premier.
    """
    cumuls = view[issuers].sum().sort_values()
    cumuls = cumuls[cumuls != 0]
    if cumuls.empty:
        return

    fig.add_trace(go.Bar(
        x=cumuls.values, y=list(cumuls.index), orientation="h",
        marker_color=[C["green"] if v >= 0 else C["red"] for v in cumuls.values],
        text=[format_flow(v) for v in cumuls.values],
        textposition="outside", textfont=dict(size=9, color=C["text"]),
        cliponaxis=False, showlegend=False,
        hovertemplate="%{y} · %{x:+,.0f} M$<extra></extra>",
    ), row=1, col=2)

    # Les montants sont écrits au bout de la barre, donc à *gauche* du
    # zéro pour un émetteur qui décollecte : sans marge, ils viendraient
    # se coucher sur les tickers de l'axe. La marge est prise sur la
    # plus grande barre, la seule échelle qui vaille ici — IBIT pèse dix
    # fois ses concurrents.
    ampleur = max(abs(float(cumuls.min())), abs(float(cumuls.max())))
    fig.update_xaxes(
        range=[min(0.0, float(cumuls.min())) - ampleur * 0.42,
               max(0.0, float(cumuls.max())) + ampleur * 0.30],
        row=1, col=2,
    )


def _etf_empty(message: str) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        margin=dict(l=8, r=8, t=4, b=4),
        annotations=[dict(text=message, showarrow=False,
                          font=dict(family=MONO, color=C["muted"], size=11))],
        xaxis=dict(visible=False), yaxis=dict(visible=False),
    )
    return fig
