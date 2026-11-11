"""
Panneau macro : le cours face à la masse monétaire.

La thèse est banale et vaut d'être regardée plutôt que crue : le Bitcoin
suivrait la liquidité en dollars, avec un ou deux trimestres de retard.
Le panneau met les deux séries côte à côte, laisse choisir ce décalage,
et affiche deux corrélations dont la seconde est la seule intéressante
(voir `macro_stats`).

La donnée est mensuelle et paraît avec deux mois de retard : ce panneau
vit sur l'horloge rare, et la ligne M2 s'arrête avant celle du cours.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from ..charts import build_macro_chart, macro_stats, prepare_macro_frame
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Fenêtres d'observation, en mois. Binance ne cote le Bitcoin que depuis
#: 2017 : au-delà de dix ans, il n'y aurait plus de bougies à montrer.
WINDOWS = {"2A": 24, "5A": 60, "10A": 120}
DEFAULT_WINDOW = "5A"

#: La série de liquidité mise en regard du cours. M2 dit la liquidité du
#: dollar, mensuelle et publiée avec deux mois de retard ; l'offre de
#: stablecoins dit celle du marché lui-même, à jour de la veille — le cash
#: avec lequel on achète du bitcoin sur les plateformes.
SERIES = ("M2", "STB")

#: Décalage appliqué à M2, en mois — l'hypothèse d'un cours qui réagit à
#: la liquidité avec un trimestre de retard se vérifie en le bougeant.
LAGS = (0, 1, 2, 3)


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else
            html.Span("BTC × masse monétaire M2 (US)"),
            html.Div([
                # 9 px et des marges courtes, comme les badges des autres
                # panneaux : la rangée basse porte sept onglets, et la
                # barre débordait de 30 px à l'arrivée du septième.
                html.Span(id="macro-stats", style={"fontSize": "9px",
                                                   "marginRight": "8px"}),
                # `persistence` : fenêtre et décalage survivent au
                # rechargement — et au passage par un autre onglet de la
                # cellule, qui reconstruit ce layout à ses défauts.
                dcc.RadioItems(
                    id="macro-series",
                    options=[{"label": k, "value": k} for k in SERIES],
                    value=SERIES[0], inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginRight": "6px"},
                ),
                dcc.RadioItems(
                    id="macro-window",
                    options=[{"label": k, "value": k} for k in WINDOWS],
                    value=DEFAULT_WINDOW, inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px"},
                ),
                dcc.RadioItems(
                    id="macro-lag",
                    options=[{"label": f"+{lag}M" if lag else "0", "value": lag}
                             for lag in LAGS],
                    value=0, inline=True, className="tf-radio",
                    persistence=True, persistence_type="local",
                    style={"display": "inline-block", "fontSize": "9px",
                           "marginLeft": "6px"},
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap"}),
        ], style=TITLE_STYLE),
        dcc.Graph(
            id="macro-chart",
            style={"flex": "1", "minHeight": "0"},
            config={"scrollZoom": True, "displaylogo": False,
                    "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
        ),
    ], style=PANEL_STYLE)


def monthly_supply(daily):
    """L'offre de stablecoins au format de M2 : `time` / `m2`, un point par
    mois — le dernier relevé du mois, le mois en cours compris."""
    if daily is None or daily.empty:
        return daily.rename(columns={"supply": "m2"}) if daily is not None \
            else daily
    mois = daily.assign(mois=daily["time"].dt.to_period("M"))
    dernier = mois.groupby("mois", as_index=False).last()
    return dernier.assign(time=dernier["mois"].dt.to_timestamp())[
        ["time", "supply"]].rename(columns={"supply": "m2"})


def _stats_badge(stats: dict, label: str = "M2"):
    """Les deux corrélations et la croissance annuelle de la série."""
    if stats["niveaux"] is None:
        return html.Span("historique insuffisant", style={"color": C["muted"]})

    def colored(value: float):
        color = (C["green"] if value >= 0.5 else
                 C["red"] if value <= -0.2 else C["muted"])
        return html.Span(f"{value:+z.2f}", style={"color": color})

    children = [
        # Des libellés courts, le sens au survol : la rangée basse porte
        # sept onglets, et le sélecteur de série a pris la place qui restait.
        html.Span("r niv. ", style={"color": C["muted"]},
                  title="corrélation des niveaux (log du cours contre la série)"),
        colored(stats["niveaux"]),
        html.Span(" · var. 3M ", style={"color": C["muted"]},
                  title="corrélation des variations sur trois mois"),
        colored(stats["variations"]),
    ]
    if stats["m2_yoy"] is not None:
        children += [
            html.Span(" · ", style={"color": C["muted"]}),
            html.Span(f"{stats['m2_yoy']:+z.1f} %/an",
                      style={"color": C["cyan"]},
                      title=f"croissance de la série ({label}) sur un an"),
        ]
    return html.Span(children, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("macro-chart", "figure"),
        Output("macro-stats", "children"),
        Input("tick-rare", "n_intervals"),
        Input("macro-window", "value"),
        Input("macro-lag", "value"),
        Input("macro-series", "value"),
        Input("maximized", "data"),
    )
    def _refresh(_tick, window, lag, series, maximized):
        months = WINDOWS.get(window, WINDOWS[DEFAULT_WINDOW])
        lag = lag if lag in LAGS else 0
        stables = series == "STB"
        liquidity = (monthly_supply(hub.stablecoin_supply()) if stables
                     else hub.m2_supply())

        frame = prepare_macro_frame(
            hub.klines("1M", limit=120), liquidity, lag_months=lag
        ).tail(months)

        # La révision suit la série, la fenêtre et le décalage : changer
        # l'un d'eux doit recadrer, un rafraîchissement mensuel non.
        described = (dict(name="stablecoins ($)", scale=1.0, unit="Md$",
                          empty="offre de stablecoins indisponible")
                     if stables else {})
        return (
            build_macro_chart(frame, uirevision=f"{series}:{window}:{lag}",
                              maximized=(maximized == "macro"), **described),
            _stats_badge(macro_stats(frame), "stables" if stables else "M2"),
        )
