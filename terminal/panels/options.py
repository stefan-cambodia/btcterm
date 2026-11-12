"""
Panneau options : ce que le marché craint, et ce qu'il paie pour s'en couvrir.

Trois vues, de la plus synthétique à la plus fine :

- **VOL** — DVOL, la volatilité implicite à trente jours de Deribit, contre
  la volatilité réalisée sur le même horizon. L'écart est la prime des
  options ; il se retourne quand le marché bouge plus qu'il ne l'attendait ;
- **TERME** — la volatilité à la monnaie de chaque échéance et ses ailes
  25 delta, au-dessus de la base annualisée des futures à échéance ;
- **STRIKES** — l'open interest d'une échéance par strike, calls contre
  puts, avec le forward et le max pain ;
- **HISTO** — ce que seul le journal garde : la volatilité à un mois et le
  skew 25 delta dans le temps, notés toutes les cinq minutes avec
  l'instantané de marché (§2.7). Deribit ne sert gratuitement l'historique
  que de DVOL.

Les strikes restent en dollars, quelle que soit la devise choisie au panneau
prix : ce sont les termes des contrats, pas un prix du moment (§4.3).

Source : Deribit, sans clé (`btcterm.options` pour les calculs).
"""

from __future__ import annotations

import pandas as pd
from dash import Input, Output, State, dcc, html, no_update

from btcterm import options as opt

from ..charts import (OPTIONS_DEFAULT_VIEW, OPTIONS_VIEWS,
                      build_options_history, build_strikes_chart,
                      build_term_chart, build_vol_chart)
from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Rafraîchissement propre au panneau : la chaîne d'options vit à la
#: minute, l'horloge rare du terminal en compte cinq. La minuterie n'existe
#: que si le panneau est affiché, comme ses callbacks.
REFRESH_MS = 60_000

#: Échéances proposées à la vue des strikes : celles qui pèsent au moins
#: cette part de l'open interest, dans la limite du nombre — une échéance
#: quotidienne de quelques centaines de bitcoins n'a rien à montrer.
EXPIRY_SHARE = 0.02
EXPIRY_CHOICES = 6

#: Profondeur de la vue HISTO : un mois de points à cinq minutes.
HISTORY_DAYS = 30

#: Le sélecteur d'échéances a sa ligne, sous la barre de titre : il y
#: prenait place à côté des badges et des vues, et dans la vue STRIKES la
#: barre débordait de 141 px dans la grille, 132 en plein écran — STRIKES
#: et HISTO passaient sous le bouton ⛶, l'onglet CYCLE touchait les
#: badges. Une ligne de plus sur le graphique des strikes, et une barre de
#: titre identique d'une vue à l'autre.
_PICKER_SHOWN = {"display": "block", "fontFamily": MONO, "fontSize": "9px",
                 "letterSpacing": "0.06em", "margin": "2px 0 2px 2px"}
_PICKER_HIDDEN = {"display": "none"}


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Options BTC"),
            html.Div([
                html.Span(id="options-badges", style={"fontSize": "9px"}),
                dcc.RadioItems(
                    id="options-view",
                    options=[{"label": k, "value": k} for k in OPTIONS_VIEWS],
                    value=OPTIONS_DEFAULT_VIEW, inline=True,
                    className="tf-radio", style={"marginLeft": "8px"},
                    persistence=True, persistence_type="local",
                ),
            ], style={"display": "flex", "alignItems": "center",
                      "whiteSpace": "nowrap", "overflow": "hidden"}),
        ], style=TITLE_STYLE),
        dcc.RadioItems(
            id="options-expiry", options=[], value=None, inline=True,
            className="tf-radio", style=_PICKER_HIDDEN,
            persistence=True, persistence_type="local",
        ),
        dcc.Graph(id="options-chart", style={"flex": "1", "minHeight": "0"},
                  config={"displaylogo": False, "scrollZoom": True,
                          "modeBarButtonsToRemove": ["select2d", "lasso2d"]}),
        dcc.Interval(id="options-tick", interval=REFRESH_MS),
    ], style=PANEL_STYLE)


def expiry_choices(chain: pd.DataFrame, now: pd.Timestamp | None = None
                   ) -> list[dict]:
    """Les échéances qui pèsent, de la plus proche à la plus lointaine.

    Valeur : la date ISO de l'échéance, qui survit au rechargement tant que
    l'échéance n'est pas passée.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if chain.empty:
        return []
    by_expiry = chain.groupby("expiry")["oi"].sum()
    by_expiry = by_expiry[by_expiry.index > now + pd.Timedelta(days=1)]
    total = by_expiry.sum()
    kept = [expiry for expiry, oi in by_expiry.items()
            if total and oi / total >= EXPIRY_SHARE][:EXPIRY_CHOICES]
    return [{"label": f"{expiry:%d/%m}", "value": expiry.date().isoformat()}
            for expiry in kept]


def _number(value, fmt: str, color: str, title: str):
    return html.Span(format(value, fmt), style={"color": color}, title=title)


def _muted(text: str):
    return html.Span(text, style={"color": C["muted"]})


def badges(summary: dict, dvol: float | None, realized: float | None,
           verbose: bool):
    """DVOL, skew et put/call dans la grille ; tout le tableau en plein écran."""
    if dvol is None and summary.get("atm30") is None:
        return _muted("Deribit indisponible")
    children = []
    if dvol is not None:
        children += [_muted("DVOL "),
                     _number(dvol, ".1f", C["yellow"],
                             "volatilité implicite à 30 jours (Deribit)")]
        if verbose and realized is not None:
            spread = dvol - realized
            children += [
                _muted(" · réal. "),
                _number(realized, ".1f", C["cyan"],
                        "volatilité réalisée sur 30 jours"),
                _muted(" · prime "),
                _number(spread, "+.1f",
                        C["green"] if spread >= 0 else C["orange"],
                        "implicite moins réalisée : négative, le marché bouge "
                        "plus que ce que les options attendaient"),
            ]
    if verbose and summary.get("atm30") is not None:
        children += [_muted(f" · IV {summary['expiry30']:%d/%m} "),
                     _number(summary["atm30"] * 100, ".1f", C["text"],
                             "volatilité à la monnaie de l'échéance à un mois")]
    rr = summary.get("rr25_30")
    if rr is not None:
        children += [_muted(" · RR25 "),
                     _number(rr, "+.1f", C["green"] if rr >= 0 else C["red"],
                             "call 25Δ moins put 25Δ à un mois : négatif, les "
                             "puts sont plus chers — on se couvre à la baisse")]
    pc = summary.get("pc_oi")
    if pc is not None:
        children += [_muted(" · P/C "),
                     _number(pc, ".2f", C["text"],
                             "open interest des puts sur celui des calls")]
    if verbose:
        if summary.get("pc_volume") is not None:
            children += [_muted(" · vol. "),
                         _number(summary["pc_volume"], ".2f", C["text"],
                                 "volume des puts sur celui des calls, 24 h")]
        if summary.get("oi_btc"):
            children += [_muted(" · OI "),
                         _number(summary["oi_btc"] / 1000, ",.0f", C["text"],
                                 "open interest total, en milliers de BTC"),
                         _muted(" k BTC")]
        if summary.get("max_pain") is not None:
            children += [_muted(f" · max pain {summary['front']:%d/%m} "),
                         _number(summary["max_pain"], ",.0f", C["yellow"],
                                 "strike où les acheteurs d'options perdraient "
                                 "le plus à l'échéance — une description, pas "
                                 "une prévision")]
        if summary.get("basis90") is not None:
            expiry, rate = summary["basis90"]
            children += [_muted(f" · base {expiry:%m/%y} "),
                         _number(rate * 100, ".1f", C["purple"],
                                 "base annualisée du future le plus proche de "
                                 "trois mois"),
                         _muted(" %/an")]
    return html.Span(children, style={"fontFamily": MONO})


def register(app, hub):
    @app.callback(
        Output("options-expiry", "options"),
        Output("options-expiry", "value"),
        Output("options-expiry", "style"),
        Input("options-tick", "n_intervals"),
        Input("options-view", "value"),
        State("options-expiry", "value"),
    )
    def _expiries(_tick, view, current):
        """Les échéances au choix, dans la vue des strikes seulement.

        L'échéance retenue survit au rechargement ; passée, ou jamais
        choisie, elle cède la place à l'échéance par défaut, cochée pour
        que le sélecteur dise ce que montre le graphique.
        """
        if view != "STRIKES":
            return [], no_update, _PICKER_HIDDEN
        chain = hub.option_chain()
        choices = expiry_choices(chain)
        if current in {choice["value"] for choice in choices}:
            return choices, no_update, _PICKER_SHOWN
        front = opt.front_expiry(chain)
        return (choices, front.date().isoformat() if front is not None else None,
                _PICKER_SHOWN)

    @app.callback(
        Output("options-chart", "figure"),
        Output("options-badges", "children"),
        Input("options-tick", "n_intervals"),
        Input("options-view", "value"),
        Input("options-expiry", "value"),
        Input("expanded", "data"),
    )
    def _refresh(_tick, view, expiry_value, expanded):
        maximized = expanded == "options"
        view = view if view in OPTIONS_VIEWS else OPTIONS_DEFAULT_VIEW
        chain = hub.option_chain()
        futures = hub.futures_curve()
        dvol = hub.dvol()
        realized = hub.realized_volatility()
        now = pd.Timestamp.now(tz="UTC")
        summary = opt.summary(chain, futures, now)

        # La révision suit la vue, l'échéance et l'agrandissement : en
        # changer recadre, la minute qui passe non.
        if view == "VOL":
            figure = build_vol_chart(dvol, realized, maximized=maximized,
                                     uirevision=f"options:vol:{maximized}")
        elif view == "HISTO":
            figure = build_options_history(
                hub.market_snapshots(HISTORY_DAYS), maximized=maximized,
                uirevision=f"options:history:{maximized}")
        elif view == "TERME":
            figure = build_term_chart(opt.term_structure(chain, now), futures,
                                      maximized=maximized,
                                      uirevision=f"options:term:{maximized}")
        else:
            expiry = _chosen_expiry(chain, expiry_value, summary["front"])
            table = (opt.strike_table(chain, expiry) if expiry is not None
                     else opt.strike_table(chain.iloc[0:0]))
            forward = (float(chain.loc[chain["expiry"] == expiry,
                                       "forward"].median())
                       if expiry is not None else None)
            figure = build_strikes_chart(
                table, forward, opt.max_pain(table), maximized=maximized,
                uirevision=f"options:strikes:{expiry}:{maximized}")

        last_dvol = float(dvol["close"].iloc[-1]) if not dvol.empty else None
        last_rv = (float(realized["value"].iloc[-1])
                   if not realized.empty else None)
        return figure, badges(summary, last_dvol, last_rv, verbose=maximized)


def _chosen_expiry(chain: pd.DataFrame, value, front):
    """L'échéance choisie si elle est encore cotée, sinon celle par défaut."""
    if value and not chain.empty:
        for expiry in chain["expiry"].unique():
            if expiry.date().isoformat() == value:
                return expiry
    return front
