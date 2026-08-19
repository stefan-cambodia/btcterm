"""
Panneau calendrier macro : les prochaines dates qui font bouger le marché.

Les dates viennent du socle (`btcterm.macrocal`) : une liste tenue à la
main, transcrite des calendriers officiels — la Fed pour le FOMC, l'OMB
pour les statistiques fédérales. La barre de titre porte le compte à
rebours des deux rendez-vous les plus lourds, FOMC et CPI ; le pied du
panneau dit jusqu'où court la liste, une liste épuisée devant se voir.

Les heures affichées sont celles de la machine : les publications sont
définies en heure de New York, et c'est le socle qui fait la conversion.
"""

from __future__ import annotations

import datetime as dt

from dash import Input, Output, html

from btcterm import macrocal

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Nombre d'événements affichés.
ROWS = 14

KIND_COLOR = {
    "FOMC": C["yellow"],
    "CPI": C["orange"],
    "NFP": C["cyan"],
    "PCE": C["purple"],
}

_JOURS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_MOIS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.",
         "août", "sept.", "oct.", "nov.", "déc.")


def layout(title=None):
    return html.Div([
        html.Div([
            title if title is not None else html.Span("Calendrier macro"),
            html.Span(id="cal-next",
                      style={"fontSize": "10px", "whiteSpace": "nowrap",
                             "marginLeft": "10px"}),
        ], style=TITLE_STYLE),
        html.Div(id="cal-list", style={"flex": "1", "overflowY": "auto"}),
    ], style=PANEL_STYLE)


def _countdown(days: int) -> tuple[str, str]:
    """Texte et couleur du compte à rebours : l'imminent doit ressortir."""
    if days <= 0:
        return "auj.", C["red"]
    if days == 1:
        return "demain", C["orange"]
    return f"J-{days}", C["text"] if days <= 7 else C["muted"]


def _row(event: macrocal.MacroEvent, today: dt.date):
    when = event.when_utc.astimezone()
    date_txt = (f"{_JOURS[event.date.weekday()]} {event.date.day:2d} "
                f"{_MOIS[event.date.month - 1]:5s} {when:%H:%M}")
    countdown, color = _countdown(event.days_until(today))
    return html.Div([
        html.Span(date_txt, style={"color": C["muted"], "marginRight": "10px",
                                   "whiteSpace": "pre"}),
        html.Span(f"{countdown:>6s}", style={"color": color, "fontWeight": "600",
                                             "marginRight": "10px",
                                             "whiteSpace": "pre"}),
        html.Span(event.kind, style={"color": KIND_COLOR[event.kind],
                                     "fontWeight": "600", "marginRight": "8px"}),
        html.Span(event.label),
        html.Span(f"  {event.note}" if event.note else "",
                  style={"color": C["muted"]}),
    ], style={
        "fontFamily": MONO, "fontSize": "11px", "color": C["text"],
        "padding": "3px 4px", "borderBottom": f"1px solid {C['border']}",
    })


def _badges(today: dt.date):
    """FOMC et CPI en compte à rebours : les deux rendez-vous qui pèsent."""
    children = []
    for kind in ("FOMC", "CPI"):
        event = macrocal.next_of(kind, today)
        if event is None:
            continue
        countdown, color = _countdown(event.days_until(today))
        if children:
            children.append(html.Span(" · ", style={"color": C["muted"]}))
        children += [
            html.Span(f"{kind} ", style={"color": KIND_COLOR[kind]}),
            html.Span(countdown, style={"color": color}),
        ]
    return html.Span(children, style={"fontFamily": MONO})


def _footer(today: dt.date):
    """Jusqu'où court la liste tenue à la main — et quand la compléter."""
    horizon = macrocal.last_date()
    if (horizon - today).days < 30:
        return html.Div(
            "liste de dates presque épuisée — compléter btcterm/macrocal.py",
            style={"color": C["red"], "fontFamily": MONO, "fontSize": "10px",
                   "padding": "6px 4px"})
    return html.Div(
        f"dates officielles transcrites à la main · "
        f"jusqu'au {horizon.day} {_MOIS[horizon.month - 1]} {horizon.year}",
        style={"color": C["muted"], "fontFamily": MONO, "fontSize": "10px",
               "padding": "6px 4px"})


def register(app, hub):
    @app.callback(
        Output("cal-list", "children"),
        Output("cal-next", "children"),
        # L'horloge rare suffit largement : les dates ne changent pas, et
        # un compte à rebours en jours n'a besoin d'avancer qu'une fois
        # par jour.
        Input("tick-rare", "n_intervals"),
    )
    def _refresh(_tick):
        today = dt.datetime.now(macrocal.NEW_YORK).date()
        events = macrocal.upcoming(today, limit=ROWS)
        if not events:
            body = html.Div(
                "liste de dates épuisée — compléter btcterm/macrocal.py",
                style={"color": C["red"], "fontFamily": MONO,
                       "fontSize": "11px", "padding": "12px"})
        else:
            body = html.Div([_row(e, today) for e in events]
                            + [_footer(today)])
        return body, _badges(today)
