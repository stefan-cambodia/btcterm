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
           "build_depth_chart", "depth_window", "prepare_macro_frame", "macro_stats",
           "build_macro_chart", "build_dominance_chart",
           "build_chain_chart", "build_fear_greed_chart",
           "fear_greed_color", "FEAR_GREED_ZONES",
           "build_etf_chart", "etf_stats", "etf_issuers",
           "etf_total_column", "format_flow",
           "ETF_WINDOWS", "ETF_DEFAULT_WINDOW",
           "OPTIONS_VIEWS", "OPTIONS_DEFAULT_VIEW", "build_vol_chart",
           "build_term_chart", "build_strikes_chart",
           "MARKETS_VIEWS", "MARKETS_WINDOWS", "MARKETS_DEFAULT_WINDOW",
           "MARKET_COLORS", "build_markets_chart", "build_options_history",
           "CYCLE_VIEWS", "CYCLE_DEFAULT_VIEW", "build_cycle_chart"]

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
    # Le volume agressif : Binance le fournit, pas la série de démonstration.
    if "taker_buy_base" in df.columns:
        df["delta"] = ind.volume_delta(df["volume"], df["taker_buy_base"])
    return df


def depth_window(extents: list[float]) -> float | None:
    """La demi-largeur du cadre de la profondeur comparée, en % du prix.

    Les carnets ne portent pas la même distance : mesuré le 11 novembre
    2026, 0,004 % pour les vingt niveaux de Binance, 0,03 % pour Bybit,
    0,06 % pour Kraken — et 3 % pour Coinbase, dont les cent niveaux
    s'étalent sur un marché peu fourni. Un axe cadré sur le plus étendu
    écrasait les quatre autres dans quelques pixels autour de zéro, et la
    comparaison, raison d'être du panneau, ne se lisait plus.

    Le cadre est donc l'étendue **médiane** des carnets : la moitié
    d'entre eux le remplit au moins. Elle est arrondie au cran supérieur
    d'une échelle 1-2-5, pour que l'axe ne tremble pas à chaque trame.
    """
    extents = sorted(e for e in extents if e and e > 0)
    if not extents:
        return None
    milieu = len(extents) // 2
    mediane = (extents[milieu] if len(extents) % 2
               else (extents[milieu - 1] + extents[milieu]) / 2)
    decade = 10.0 ** np.floor(np.log10(mediane))
    for cran in (1, 2, 5, 10):
        if mediane <= cran * decade * (1 + 1e-9):
            return float(cran * decade)
    return float(10 * decade)


def build_depth_chart(books: dict, uirevision: str = "depth") -> dict:
    """Profondeur cumulée superposée, une couleur par plateforme.

    Les carnets sont recentrés en pourcentage d'écart au prix médian : les
    plateformes ne cotent pas exactement le même prix, et les superposer
    en valeur absolue rendrait la comparaison illisible. Le cadre est
    celui que la moitié des carnets couvre (`depth_window`), et le volume
    s'y ajuste ; les courbes restent entières, un zoom arrière ou un
    glissement montre ce qui dépasse.

    La figure est un dictionnaire brut, pas un `go.Figure` : Dash comme
    l'encodeur JSON de Plotly la prennent telle quelle, et c'est la
    validation de `go.Figure` — chaque propriété de chaque trace — qui
    coûtait vingt millisecondes par rendu, sur une figure que le
    pousseur et l'horloge rebâtissent sans cesse (§3.10).
    """
    palette = [C["yellow"], C["blue"], C["purple"], C["cyan"], C["green"]]
    data = []
    courbes = []        # (écarts, cumuls) de chaque côté tracé
    etendues = []       # la plus grande distance au prix médian, par carnet

    for (name, book), color in zip(books.items(), palette):
        mid = book.mid
        if mid is None:
            continue
        etendue = 0.0
        for side in ("bids", "asks"):
            prices, cumulated = book.cumulative_depth(side)
            if not prices:
                continue
            ecarts = [(p - mid) / mid * 100 for p in prices]
            courbes.append((ecarts, cumulated))
            etendue = max(etendue, -ecarts[0], ecarts[-1])
            data.append({
                "type": "scatter",
                "x": ecarts,
                "y": list(cumulated),
                # Une entrée de légende par plateforme, pour ses deux
                # côtés : elle s'appelait « Binance bids » et masquait
                # aussi les asks.
                "name": name,
                "line": {"color": color, "width": 1.4, "dash": "solid",
                         "shape": "hv"},
                "fill": "tozeroy",
                "fillcolor": "rgba(255,255,255,0.03)",
                "showlegend": side == "bids",
                "legendgroup": name,
                "hovertemplate": f"{name} · %{{x:.3f}} %<br>%{{y:.3f}} BTC"
                                 "<extra></extra>",
            })
        etendues.append(etendue)

    # `automargin` : c'est le gabarit Plotly que `go.Figure` posait sans le
    # dire qui l'activait. Sans lui, la marge de 8 px coupait les
    # graduations du volume, et l'écart au prix perdait les siennes sous
    # son titre — vu sur les captures du 7 novembre 2026.
    axis_common = {"gridcolor": C["grid"], "zerolinecolor": C["border"],
                   "tickfont": {"size": 9, "color": C["muted"]},
                   "automargin": True}
    xaxis = {"title": {"text": "écart au prix médian (%)"}, **axis_common}
    yaxis = {"title": {"text": "volume cumulé (BTC)"}, **axis_common}

    cadre = depth_window(etendues)
    if cadre is not None:
        # Le volume suit le cadre : l'échelle automatique de Plotly
        # regarde toute la courbe, et les trente bitcoins qu'un carnet
        # cumule à 2 % du prix aplatiraient ce qui s'échange à 0,02 %.
        sommet = max((volume for ecarts, cumuls in courbes
                      for ecart, volume in zip(ecarts, cumuls)
                      if abs(ecart) <= cadre), default=0.0)
        xaxis["range"] = [-cadre, cadre]
        if sommet > 0:
            yaxis["range"] = [0, sommet * 1.08]
        hors = sum(1 for e in etendues if e > cadre)
        if hors:
            xaxis["title"]["text"] += (
                f" · cadre ±{cadre:g} %, {hors} carnet"
                f"{'s vont' if hors > 1 else ' va'} au-delà")

    layout = {
        "paper_bgcolor": C["panel"], "plot_bgcolor": C["panel"],
        "font": {"family": MONO, "color": C["text"], "size": 10},
        "margin": {"l": 8, "r": 8, "t": 4, "b": 4},
        "showlegend": True,
        "legend": {"orientation": "h", "y": 1.12, "x": 0,
                   "font": {"size": 9}, "bgcolor": "rgba(0,0,0,0)"},
        "hovermode": "x unified",
        "hoverlabel": {"bgcolor": "#1a2035", "bordercolor": C["border"],
                       "font": {"color": C["text"], "size": 10}},
        "uirevision": uirevision,
        "xaxis": xaxis,
        "yaxis": yaxis,
    }
    return {"data": data, "layout": layout}


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
    maximized: bool = False,
) -> go.Figure:
    """Parts de capitalisation en barres, tendance journalisée à côté.

    CoinGecko réserve l'historique de ces agrégats à son offre payante :
    l'instantané vient de l'API, la tendance vient du journal local
    (§ hub.market_snapshots) — elle n'apparaît qu'une fois deux
    instantanés accumulés, et s'allonge séance après séance. Le Bitcoin
    garde sa couleur, les stablecoins la leur — leur part ne dit pas la
    même chose que celle d'un concurrent —, et tout ce qui n'entre pas
    dans le classement est regroupé, pour que les barres somment à cent.

    **Dans la grille, côte à côte.** Le panneau y est large et bas — 950 px
    sur 170 dans la rangée du bas — et deux graphiques empilés s'y
    partageaient cent pixels : une barre sur deux perdait son nom, le
    titre de l'axe des barres s'écrivait sur la tendance, la légende sur
    les graduations. En plein écran, la hauteur est là, et ils s'empilent.

    **Chaque courbe a son échelle.** Sur un axe commun de 0 à 60, la
    dominance du Bitcoin (vers 59 %) et la part des stablecoins (vers
    8 %) étaient deux traits plats, en haut et en bas : un point de
    dominance, qui est un mouvement, y valait un pixel. Le Bitcoin se lit
    à gauche, les stablecoins à droite, chacun cadré sur ce qu'il a
    parcouru, et les graduations portent la couleur de leur courbe.
    """
    trend = (history is not None and not history.empty
             and history["btc_dominance"].notna().sum() >= 2)
    if trend and maximized:
        fig = make_subplots(rows=2, cols=1, row_heights=[0.5, 0.5],
                            vertical_spacing=0.14,
                            specs=[[{}], [{"secondary_y": True}]])
        bar_target, trend_target = dict(row=1, col=1), dict(row=2, col=1)
    elif trend:
        fig = make_subplots(rows=1, cols=2, column_widths=[0.42, 0.58],
                            horizontal_spacing=0.07,
                            specs=[[{}, {"secondary_y": True}]])
        bar_target, trend_target = dict(row=1, col=1), dict(row=1, col=2)
    else:
        fig = go.Figure()
        bar_target = trend_target = {}

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["grid"],
                       tickfont=dict(size=9, color=C["muted"]))
    bar_x = dict(ticksuffix=" %", **axis_common)
    if maximized or not trend:
        bar_x["title_text"] = "part de la capitalisation (%)"
        bar_x["ticksuffix"] = ""

    if not shares:
        fig.add_annotation(
            text="agrégats de marché indisponibles", xref="paper", yref="paper",
            x=(0.5 if maximized or not trend else 0.2),
            y=(0.85 if trend and maximized else 0.5), showarrow=False,
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
        # La plus longue barre garde la place de son étiquette, qui
        # sinon déborde sur la tendance voisine.
        bar_x["range"] = [0, max(parts) * 1.2]

    # Chaque barre garde son nom : l'axe en sautait un sur deux dès que
    # le panneau manquait de hauteur.
    bar_y = dict(tickmode="linear", dtick=1, **axis_common)

    if trend:
        # Les lignes de rupture (`gap`, § hub.market_snapshots) restent :
        # un NaN dans la série interrompt le trait, c'est voulu.
        rupture = history["gap"] if "gap" in history else False
        btc = history[history["btc_dominance"].notna() | rupture]
        fig.add_trace(go.Scatter(
            x=btc["time"], y=btc["btc_dominance"], name="BTC",
            mode="lines", line=dict(color=C["yellow"], width=1.6),
            hovertemplate="BTC %{y:.2f} %<extra></extra>",
        ), secondary_y=False, **trend_target)
        stables = history[history["stable_share"].notna() | rupture]
        if stables["stable_share"].notna().any():
            fig.add_trace(go.Scatter(
                x=stables["time"], y=stables["stable_share"],
                name="stables", mode="lines",
                line=dict(color=C["blue"], width=1.4),
                hovertemplate="stables %{y:.2f} %<extra></extra>",
            ), secondary_y=True, **trend_target)
        fig.update_xaxes(**bar_x, **bar_target)
        fig.update_yaxes(**bar_y, **bar_target)
        fig.update_xaxes(**axis_common, **trend_target)
        titre = dict(size=9)
        fig.update_yaxes(
            title_text="BTC %", title_font=dict(color=C["yellow"], **titre),
            title_standoff=2, secondary_y=False, **trend_target,
            **{**axis_common, "tickfont": dict(size=9, color=C["yellow"])})
        fig.update_yaxes(
            title_text="stables %", title_font=dict(color=C["blue"], **titre),
            title_standoff=2, showgrid=False, secondary_y=True,
            **trend_target,
            **{**axis_common, "tickfont": dict(size=9, color=C["blue"])})
        fig.update_layout(
            showlegend=False,
            hovermode="x unified",
            hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                            font_color=C["text"], font_size=10),
        )
    else:
        fig.update_layout(showlegend=False, xaxis=bar_x, yaxis=bar_y)

    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=8 if trend else 42, t=6, b=4),
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


# ─────────────────────────────────────────────────────────────
# Options : volatilité, échéances, strikes
# ─────────────────────────────────────────────────────────────

#: Les trois vues du panneau options, dans l'ordre du sélecteur.
OPTIONS_VIEWS = {"VOL": "vol", "TERME": "term", "STRIKES": "strikes",
                 "HISTO": "history"}
OPTIONS_DEFAULT_VIEW = "VOL"

#: Strikes montrés autour du forward, en fraction : au-delà, l'open interest
#: n'est plus qu'une poussière de paris lointains qui écrase l'échelle.
STRIKES_SPAN = (0.6, 1.5)


def _options_layout(fig: go.Figure, uirevision: str, maximized: bool) -> None:
    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["grid"],
                       tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=8, t=18 if maximized else 4, b=4),
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom",
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(**axis_common), yaxis=dict(**axis_common),
        uirevision=uirevision,
    )


def _options_empty(fig: go.Figure, message: str) -> go.Figure:
    fig.add_annotation(text=message, xref="paper", yref="paper", x=0.5, y=0.5,
                       showarrow=False,
                       font=dict(family=MONO, size=11, color=C["muted"]))
    return fig


def build_vol_chart(dvol: pd.DataFrame, realized: pd.DataFrame,
                    uirevision: str = "options:vol",
                    maximized: bool = False) -> go.Figure:
    """DVOL contre la volatilité réalisée à trente jours.

    DVOL dit la volatilité que les options attendent pour le mois qui
    vient, la réalisée celle que le mois écoulé a livrée. L'écart est la
    prime que paient les acheteurs d'options : positive d'ordinaire — on
    paie pour se couvrir —, elle se retourne quand le marché bouge plus
    que ce qu'il avait prévu.
    """
    fig = go.Figure()
    if not dvol.empty:
        fig.add_trace(go.Scatter(
            x=dvol["time"], y=dvol["close"], name="DVOL (implicite 30 j)",
            mode="lines", line=dict(color=C["yellow"], width=1.6),
            hovertemplate="DVOL %{y:.1f}<extra></extra>",
        ))
    if not realized.empty:
        fig.add_trace(go.Scatter(
            x=realized["time"], y=realized["value"], name="réalisée 30 j",
            mode="lines", line=dict(color=C["cyan"], width=1.3),
            hovertemplate="réalisée %{y:.1f}<extra></extra>",
        ))
    _options_layout(fig, uirevision, maximized)
    # Le titre d'axe ne tient qu'en plein écran : dans la rangée basse, il
    # est coupé, et la légende dit déjà ce que sont les courbes.
    fig.update_layout(hovermode="x unified",
                      yaxis_title_text=("volatilité annualisée (%)"
                                        if maximized else None))
    if dvol.empty and realized.empty:
        _options_empty(fig, "volatilité indisponible")
    return fig


def build_term_chart(curve: pd.DataFrame, futures: pd.DataFrame,
                     uirevision: str = "options:term",
                     maximized: bool = False) -> go.Figure:
    """La courbe des échéances : volatilité implicite en haut, base en bas.

    En haut, la volatilité à la monnaie de chaque échéance, et ses deux
    ailes 25 delta : l'aile des puts au-dessus de celle des calls, c'est
    la couverture à la baisse qui se paie. En bas, la base annualisée des
    futures, le coût du portage d'une position longue jusqu'à l'échéance.
    L'axe est le temps, pas le rang de l'échéance : la courbe a la forme
    du calendrier.
    """
    fig = go.Figure()
    if not curve.empty:
        days = curve["days"].round().astype(int)
        for column, name, color, dash in (
                ("put25", "put 25Δ", C["red"], "dot"),
                ("atm", "à la monnaie", C["yellow"], "solid"),
                ("call25", "call 25Δ", C["green"], "dot")):
            fig.add_trace(go.Scatter(
                x=curve["expiry"], y=curve[column] * 100, name=name,
                mode="lines+markers", customdata=days,
                line=dict(color=color, width=1.6 if dash == "solid" else 1.1,
                          dash=dash),
                marker=dict(size=4),
                hovertemplate=(f"{name} %{{y:.1f}} · "
                               "%{x|%d %b %y} (%{customdata} j)<extra></extra>"),
            ))
    if not futures.empty:
        # Une courbe et non des barres : sur un axe de dates, deux échéances
        # à un jour d'écart réduisaient les barres à des traits.
        fig.add_trace(go.Scatter(
            x=futures["expiry"], y=futures["annualized"] * 100,
            name="base annualisée", yaxis="y2", mode="lines+markers",
            line=dict(color=C["purple"], width=1.4), marker=dict(size=4),
            fill="tozeroy", fillcolor="rgba(167,139,250,0.08)",
            customdata=futures["days"].round().astype(int),
            hovertemplate=("base %{y:.2f} %/an · %{x|%d %b %y} "
                           "(%{customdata} j)<extra></extra>"),
        ))
    _options_layout(fig, uirevision, maximized)
    axis = dict(gridcolor=C["grid"], zerolinecolor=C["border"])
    fig.update_layout(
        hovermode="closest",
        # L'axe des dates sous l'étage du bas, qu'il partage avec le haut.
        xaxis=dict(anchor="y2", gridcolor=C["grid"],
                   tickfont=dict(size=9, color=C["muted"])),
        yaxis=dict(domain=[0.34, 1.0], tickfont=dict(size=9, color=C["muted"]),
                   title_text="vol. implicite (%)" if maximized else None,
                   **axis),
        yaxis2=dict(domain=[0.0, 0.26], tickfont=dict(size=9, color=C["purple"]),
                    title_text="base %/an" if maximized else None,
                    rangemode="tozero", **axis),
    )
    if curve.empty and futures.empty:
        _options_empty(fig, "options indisponibles")
    return fig


def build_strikes_chart(table: pd.DataFrame, forward: float | None,
                        pain: float | None, uirevision: str = "options:strikes",
                        maximized: bool = False) -> go.Figure:
    """L'open interest par strike d'une échéance, calls contre puts.

    Le forward de l'échéance et son max pain en verticales : l'écart entre
    les deux dit de quel côté la plupart des options finiraient sans
    valeur si le prix ne bougeait pas.
    """
    fig = go.Figure()
    view = table
    if forward and not table.empty:
        low, high = STRIKES_SPAN
        view = table[(table["strike"] >= forward * low)
                     & (table["strike"] <= forward * high)]
    if not view.empty:
        for column, name, color in (("call_oi", "calls", C["green"]),
                                    ("put_oi", "puts", C["red"])):
            fig.add_trace(go.Bar(
                x=view["strike"], y=view[column], name=name,
                marker=dict(color=color, opacity=0.8),
                hovertemplate=(f"{name} %{{y:,.0f}} BTC · strike "
                               "%{x:,.0f}<extra></extra>"),
            ))
    _options_layout(fig, uirevision, maximized)
    fig.update_layout(barmode="group", bargap=0.25, hovermode="closest",
                      yaxis_title_text=("open interest (BTC)" if maximized
                                        else None))
    for value, label, color, dash in ((forward, "forward", C["text"], "dash"),
                                      (pain, "max pain", C["yellow"], "dot")):
        if value and not view.empty:
            fig.add_vline(x=value, line=dict(color=color, width=1, dash=dash),
                          annotation_text=f"{label} {value:,.0f}",
                          annotation_font=dict(size=9, color=color),
                          annotation_position="top")
    if view.empty:
        _options_empty(fig, "open interest indisponible")
    return fig


# ─────────────────────────────────────────────────────────────
# Marchés : le bitcoin face aux actions, à l'or et au dollar
# ─────────────────────────────────────────────────────────────

#: Les vues et les fenêtres du panneau marchés.
MARKETS_VIEWS = ("PERF", "CORR")
MARKETS_WINDOWS = {"1M": 31, "3M": 92, "6M": 183, "1A": 366}
MARKETS_DEFAULT_WINDOW = "6M"

#: Trois couleurs au plus, les trois premiers pas sombres de la palette de
#: référence du guide de visualisation, validés ensemble sur la surface
#: des panneaux : séparation daltonienne ΔE 9,4, vision normale 20,9. Le
#: bitcoin, la référence, est en encre claire et en trait épais.
MARKET_COLORS = {"spx": "#3987e5", "gold": "#d95926", "dxy": "#199e70"}


def _spread_labels(ends: list, span: float, gap: float = 0.035) -> list:
    """Écarte les étiquettes de bout de courbe d'au moins `gap` de `span`,
    la plage de tout ce qui est tracé, en remontant celle du dessus : deux
    séries qui finissent au même niveau gardent des noms lisibles."""
    if len(ends) < 2:
        return ends
    step = (span or 1.0) * gap
    placed, previous = [], None
    for x, y, name in sorted(ends, key=lambda end: end[1]):
        if previous is not None and y < previous + step:
            y = previous + step
        placed.append((x, y, name))
        previous = y
    return placed


def build_markets_chart(view: str, btc: pd.DataFrame,
                        frames: dict[str, pd.DataFrame], window: str,
                        uirevision: str = "markets",
                        maximized: bool = False) -> go.Figure:
    """Performance comparée, ou corrélation glissante au bitcoin.

    **PERF** ramène chaque série à 100 au début de la fenêtre : un seul
    axe, des unités communes. **CORR** trace la corrélation des variations
    quotidiennes sur trente séances, de −1 à 1 : au-dessus de zéro, le
    bitcoin bouge avec ce marché. Les courbes portent leur nom au bout,
    en plus de la légende : l'identité ne tient pas à la seule couleur.
    """
    from btcterm import markets as mkt

    fig = go.Figure()
    days = MARKETS_WINDOWS.get(window, MARKETS_WINDOWS[MARKETS_DEFAULT_WINDOW])
    since = pd.Timestamp.now().normalize() - pd.Timedelta(days=days)
    ends, extent = [], []

    def trace(x, y, name, color, width, hover):
        fig.add_trace(go.Scatter(
            x=x, y=y, name=name, mode="lines",
            line=dict(color=color, width=width), hovertemplate=hover))
        ends.append((x[-1], float(y.iloc[-1]), name))
        extent.extend((float(y.min()), float(y.max())))

    if view == "CORR":
        for asset in mkt.ASSETS:
            frame = frames.get(asset.key)
            if not asset.charted or frame is None or frame.empty:
                continue
            corr = mkt.rolling_correlation(mkt.align(btc, frame), asset.kind,
                                           mkt.SHORT_WINDOW)
            corr = corr[corr.index >= since]
            if not corr.empty:
                trace(corr.index, corr, asset.label, MARKET_COLORS[asset.key],
                      1.6, f"{asset.label} %{{y:+.2f}}<extra></extra>")
        title = "corrélation 30 séances" if maximized else None
    else:
        if not btc.empty:
            series = (btc.assign(date=pd.to_datetime(btc["time"]).dt.normalize())
                      .groupby("date")["close"].last())
            perf = mkt.performance(series, since)
            if not perf.empty:
                trace(perf.index, perf, "Bitcoin", C["text"], 2.4,
                      "Bitcoin %{y:.1f}<extra></extra>")
        for asset in mkt.ASSETS:
            frame = frames.get(asset.key)
            if not asset.charted or frame is None or frame.empty:
                continue
            series = frame.set_index(pd.to_datetime(frame["time"]))["close"]
            perf = mkt.performance(series, since)
            if not perf.empty:
                trace(perf.index, perf, asset.label, MARKET_COLORS[asset.key],
                      1.6, f"{asset.label} %{{y:.1f}}<extra></extra>")
        title = "base 100" if maximized else None

    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["border"],
                       tickfont=dict(size=9, color=C["muted"]))
    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=78, t=18 if maximized else 4, b=4),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom",
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        xaxis=dict(**axis_common),
        yaxis=dict(title_text=title, **axis_common,
                   **({"range": [-1, 1], "zeroline": True}
                      if view == "CORR" else {})),
        uirevision=uirevision,
    )
    if view != "CORR" and ends:
        fig.add_hline(y=100, line=dict(color=C["border"], width=1))
    # Le nom au bout de chaque courbe, en encre du texte : la couleur
    # porte l'identité à côté, jamais le texte lui-même. En plein écran
    # seulement — dans la rangée basse, deux courbes qui finissent au même
    # niveau y superposaient leurs noms, et la légende suffit.
    if maximized:
        span = (2.0 if view == "CORR"
                else max(extent) - min(extent) if extent else 1.0)
        for x, y, name in _spread_labels(ends, span):
            fig.add_annotation(x=x, y=y, text=name, showarrow=False,
                               xanchor="left", xshift=4,
                               font=dict(size=9, color=C["text"]))
    if not ends:
        fig.add_annotation(text="marchés indisponibles", xref="paper",
                           yref="paper", x=0.5, y=0.5, showarrow=False,
                           font=dict(family=MONO, size=11, color=C["muted"]))
    return fig


def build_options_history(snapshots: pd.DataFrame,
                          uirevision: str = "options:history",
                          maximized: bool = False) -> go.Figure:
    """Ce que seul le journal garde : la volatilité et le skew dans le temps.

    Deribit ne sert gratuitement l'historique que de DVOL ; la volatilité
    à un mois et le skew 25 delta n'existent qu'au présent, et le journal
    les note toutes les cinq minutes. En haut, DVOL et la volatilité à la
    monnaie de l'échéance à un mois ; en bas, le skew, dans son propre
    étage plutôt que sur un second axe. Les lignes de rupture du journal
    — machine endormie, service arrêté — y coupent les courbes.
    """
    fig = go.Figure()
    columns = ["dvol", "atm30_iv", "rr25"]
    view, points, first = snapshots, 0, None
    if not snapshots.empty:
        # Les instantanés d'avant l'historique des options n'ont aucune de
        # ces colonnes : écartés. Les lignes de rupture du journal, toutes
        # vides mais marquées `gap`, restent : elles coupent les courbes.
        has = snapshots[columns].notna().any(axis=1)
        points = int(has.sum())
        if points:
            first = snapshots.loc[has, "time"].min()
            view = snapshots[(snapshots["time"] >= first)
                             & (has | snapshots["gap"].astype(bool))]
    if points >= 2:
        for column, name, color, width in (
                ("dvol", "DVOL", C["yellow"], 1.6),
                ("atm30_iv", "à la monnaie, 1 mois", C["text"], 1.2)):
            fig.add_trace(go.Scatter(
                x=view["time"], y=view[column], name=name, mode="lines",
                connectgaps=False, line=dict(color=color, width=width),
                hovertemplate=f"{name} %{{y:.1f}}<extra></extra>"))
        fig.add_trace(go.Scatter(
            x=view["time"], y=view["rr25"], name="skew 25Δ", yaxis="y2",
            mode="lines", connectgaps=False,
            line=dict(color=C["cyan"], width=1.3),
            hovertemplate="skew %{y:+.1f}<extra></extra>"))
    _options_layout(fig, uirevision, maximized)
    axis = dict(gridcolor=C["grid"], zerolinecolor=C["border"])
    fig.update_layout(
        hovermode="x unified",
        xaxis=dict(anchor="y2", gridcolor=C["grid"],
                   tickfont=dict(size=9, color=C["muted"])),
        yaxis=dict(domain=[0.36, 1.0], tickfont=dict(size=9, color=C["muted"]),
                   title_text="vol. implicite" if maximized else None, **axis),
        yaxis2=dict(domain=[0.0, 0.28], tickfont=dict(size=9, color=C["cyan"]),
                    title_text="skew 25Δ" if maximized else None,
                    zeroline=True, **axis),
    )
    if points < 2:
        since = f" depuis le {first:%d/%m %H:%M}" if first is not None else ""
        _options_empty(fig, "historique local en constitution" + since +
                       " — un point toutes les cinq minutes")
    return fig


# ─────────────────────────────────────────────────────────────
# Cycle : le halving, le sommet, la moyenne de 200 jours
# ─────────────────────────────────────────────────────────────

CYCLE_VIEWS = ("CYCLES", "MAYER", "REPLI")
CYCLE_DEFAULT_VIEW = "CYCLES"

#: Une teinte par cycle passé ; le cycle en cours est à l'encre du texte,
#: plus épais — l'identité ne tient pas à la seule couleur, chaque courbe
#: porte aussi son année au bout.
_CYCLE_COLORS = {"2012": C["orange"], "2016": C["purple"], "2020": C["blue"]}


def build_cycle_chart(view: str, closes: pd.Series,
                      uirevision: str = "cycle",
                      maximized: bool = False) -> go.Figure:
    """Le cycle en trois vues (§ cycle).

    **CYCLES** superpose les cycles, en multiple du cours du jour du
    halving, jour pour jour, sur une échelle logarithmique : une même
    hauteur y vaut un même multiple. **MAYER** trace le cours rapporté à
    sa moyenne de 200 jours, entre ses deux bornes d'usage. **REPLI**
    trace la distance au plus haut couru, en % — la profondeur et la
    durée des marchés baissiers s'y lisent d'un coup.
    """
    from btcterm import cycle

    fig = go.Figure()
    axis_common = dict(gridcolor=C["grid"], zerolinecolor=C["border"],
                       tickfont=dict(size=9, color=C["muted"]))
    xaxis, yaxis = dict(**axis_common), dict(**axis_common)
    right = 8

    if view == "MAYER":
        ratio = cycle.mayer(closes)
        if len(ratio):
            fig.add_trace(go.Scatter(
                x=ratio.index, y=ratio, name="multiple de Mayer",
                mode="lines", line=dict(color=C["text"], width=1.4),
                hovertemplate="%{y:.2f}<extra></extra>"))
            for level, color, text in (
                    (cycle.MAYER_HIGH, C["red"], "détaché de sa moyenne"),
                    (1.0, C["muted"], "moyenne de 200 j"),
                    (cycle.MAYER_LOW, C["green"], "loin sous sa moyenne")):
                fig.add_hline(
                    y=level, line=dict(color=color, width=1, dash="dot"),
                    annotation_text=f"{level:g} · {text}",
                    annotation_position="top left",
                    annotation_font=dict(size=9, color=C["muted"]))
        yaxis["title_text"] = "cours / MA 200 j" if maximized else None
    elif view == "REPLI":
        depth = cycle.drawdown(closes)
        if len(depth):
            fig.add_trace(go.Scatter(
                x=depth.index, y=depth, name="repli depuis le sommet",
                mode="lines", line=dict(color=C["red"], width=1.3),
                fill="tozeroy", fillcolor="rgba(255,61,107,0.10)",
                hovertemplate="%{y:.1f} %<extra></extra>"))
        yaxis.update(title_text="% du plus haut" if maximized else None,
                     ticksuffix=" %")
    else:
        right = 44
        for path in cycle.cycle_paths(closes):
            current = path["current"]
            color = (C["text"] if current
                     else _CYCLE_COLORS.get(path["label"], C["cyan"]))
            fig.add_trace(go.Scatter(
                x=path["days"], y=path["multiple"],
                name=f"halving {path['label']}", mode="lines",
                line=dict(color=color, width=2.4 if current else 1.4),
                hovertemplate=f"{path['label']} ×%{{y:.2f}}<extra></extra>"))
            # L'année au bout de la courbe, à l'encre du texte.
            fig.add_annotation(
                x=int(path["days"][-1]),
                y=float(np.log10(path["multiple"][-1])),
                text=path["label"], showarrow=False, xanchor="left",
                xshift=4, font=dict(family=MONO, size=9, color=C["text"]))
        if fig.data:
            fig.add_hline(y=1, line=dict(color=C["border"], width=1))
        xaxis.update(title_text="jours depuis le halving"
                     if maximized else None, rangemode="tozero")
        yaxis.update(type="log", tickprefix="×",
                     title_text="multiple du cours du halving"
                     if maximized else None)

    fig.update_layout(
        paper_bgcolor=C["panel"], plot_bgcolor=C["panel"],
        font=dict(family=MONO, color=C["text"], size=10),
        margin=dict(l=8, r=right, t=18 if maximized else 4, b=4),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#1a2035", bordercolor=C["border"],
                        font_color=C["text"], font_size=10),
        showlegend=view not in ("MAYER", "REPLI"),
        legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom",
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        xaxis=xaxis, yaxis=yaxis, uirevision=uirevision,
    )
    if not fig.data:
        fig.add_annotation(
            text="historique journalier indisponible", xref="paper",
            yref="paper", x=0.5, y=0.5, showarrow=False,
            font=dict(family=MONO, size=11, color=C["muted"]))
    return fig
