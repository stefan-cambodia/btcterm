"""
Panneau calendrier macro : les prochaines dates qui font bouger le marché.

Les dates viennent du socle (`btcterm.macrocal`) : une liste tenue à la
main, transcrite des calendriers officiels — la Fed pour le FOMC, l'OMB
pour les statistiques fédérales. La barre de titre porte le compte à
rebours des deux rendez-vous les plus lourds, FOMC et CPI ; le pied du
panneau dit jusqu'où court la liste, une liste épuisée devant se voir.

Les heures affichées sont celles de la machine : les publications sont
définies en heure de New York, et c'est le socle qui fait la conversion.
**Les jours aussi.** Le panneau écrivait le jour de New York à côté de
l'heure locale : vu de Phnom Penh, le FOMC du mercredi 9 décembre 2026,
14 h à New York, s'affichait « mer. 9 déc. 02:00 » — un jour trop tôt,
il tombe le jeudi 10 à 2 h. Et le CPI de la veille au soir restait
« auj. », en rouge, toute la matinée du lendemain, New York n'ayant pas
changé de date. Tout se compte désormais dans le fuseau de la machine :
le jour écrit, le compte à rebours, et « aujourd'hui » ; une publication
dont l'heure est passée reste listée jusqu'à minuit, mais se dit passée,
et le titre passe à la suivante.
"""

from __future__ import annotations

import datetime as dt

from dash import Input, Output, html

from btcterm import macrocal
from btcterm import options as opt

from ..theme import C, MONO, PANEL_STYLE, TITLE_STYLE

#: Nombre d'événements affichés.
ROWS = 14

KIND_COLOR = {
    "FOMC": C["yellow"],
    "CPI": C["orange"],
    "NFP": C["cyan"],
    "PCE": C["purple"],
    "OPT": C["green"],
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


def _countdown(when: dt.datetime, now: dt.datetime) -> tuple[str, str]:
    """Texte et couleur du compte à rebours : l'imminent doit ressortir.

    `when` et `now` sont dans le même fuseau, celui de la machine : les
    jours se comptent d'une date locale à l'autre, pas en tranches de
    vingt-quatre heures — une publication de demain matin est « demain »
    dès ce soir.
    """
    if when <= now:
        return "passé", C["muted"]
    days = (when.date() - now.date()).days
    if days <= 0:
        return "auj.", C["red"]
    if days == 1:
        return "demain", C["orange"]
    return f"J-{days}", C["text"] if days <= 7 else C["muted"]


def _local(event: macrocal.MacroEvent, now: dt.datetime) -> dt.datetime:
    """L'heure de la publication dans le fuseau de `now`."""
    return event.when_utc.astimezone(now.tzinfo)


def option_events(summaries: list[dict]) -> list[macrocal.MacroEvent]:
    """Les échéances d'options qui pèsent (§ options.expiry_summary), en
    événements du calendrier : Deribit expire à 8 h UTC, l'heure de New
    York s'en déduit comme pour le reste. Ce ne sont pas des dates tenues à
    la main : elles viennent de la chaîne, et le pied du panneau ne les
    compte pas dans son horizon."""
    events = []
    for item in summaries:
        when = item["expiry"].to_pydatetime().astimezone(macrocal.NEW_YORK)
        note = f"{item['notional'] / 1e9:.1f} Md$"
        if item.get("pain"):
            note += f" · max pain {item['pain']:,.0f}"
        if item.get("pc") is not None:
            note += f" · P/C {item['pc']:.2f}"
        events.append(macrocal.MacroEvent(
            when.date(), when.time().replace(tzinfo=None), "OPT",
            "Échéance Deribit", note=note))
    return events


def listed(now: dt.datetime, limit: int = ROWS,
           extra: tuple = ()) -> list[macrocal.MacroEvent]:
    """Les événements du jour local et des suivants, dans l'ordre —
    `extra` y mêle ceux qui ne sont pas tenus à la main (les échéances
    d'options)."""
    events = sorted([*macrocal.EVENTS, *extra], key=lambda e: e.when_utc)
    events = [e for e in events if _local(e, now).date() >= now.date()]
    return events[:limit]


def _row(event: macrocal.MacroEvent, now: dt.datetime):
    when = _local(event, now)
    date_txt = (f"{_JOURS[when.weekday()]} {when.day:2d} "
                f"{_MOIS[when.month - 1]:5s} {when:%H:%M}")
    countdown, color = _countdown(when, now)
    passe = when <= now
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
        # Une publication passée reste lisible, mais ne retient plus l'œil.
        **({"opacity": "0.55"} if passe else {}),
    }, title=(f"{event.date:%d/%m/%Y} à {event.time_ny:%H:%M}, heure de New "
              "York" + (" — 8 h UTC, l'expiration de Deribit ; montant, max "
                        "pain et put/call de l'échéance, lus dans la chaîne"
                        if event.kind == "OPT" else "")))


def _badges(now: dt.datetime):
    """FOMC et CPI en compte à rebours : les deux rendez-vous qui pèsent —
    le prochain de chaque famille, celui dont l'heure n'est pas passée."""
    children = []
    for kind in ("FOMC", "CPI"):
        event = next((e for e in macrocal.EVENTS
                      if e.kind == kind and e.when_utc > now), None)
        if event is None:
            continue
        countdown, color = _countdown(_local(event, now), now)
        if children:
            children.append(html.Span(" · ", style={"color": C["muted"]}))
        children += [
            html.Span(f"{kind} ", style={"color": KIND_COLOR[kind]}),
            html.Span(countdown, style={"color": color}),
        ]
    return html.Span(children, style={"fontFamily": MONO})


def _footer(today: dt.date):
    """Jusqu'où court la liste tenue à la main — et quand la compléter.

    Chaque famille s'épuise à son rythme : le pied nomme celles qui
    s'arrêtent dans le mois. Il disait le dernier FOMC quand le CPI, le
    NFP et le PCE s'arrêtaient un an plus tôt.
    """
    ends = macrocal.last_dates()
    soon = [kind for kind, end in ends.items() if (end - today).days < 30]
    if soon:
        return html.Div(
            f"dates bientôt épuisées : {', '.join(soon)} — compléter "
            "btcterm/macrocal.py",
            style={"color": C["red"], "fontFamily": MONO, "fontSize": "10px",
                   "padding": "6px 4px"})
    horizon = macrocal.last_date()
    first = next(kind for kind, end in ends.items() if end == horizon)
    return html.Div(
        f"dates officielles transcrites à la main · complètes jusqu'au "
        f"{horizon.day} {_MOIS[horizon.month - 1]} {horizon.year} ({first})",
        style={"color": C["muted"], "fontFamily": MONO, "fontSize": "10px",
               "padding": "6px 4px"},
        title=" · ".join(f"{kind} jusqu'au {end:%d/%m/%Y}"
                         for kind, end in ends.items()))


def render(now: dt.datetime | None = None, extra: tuple = ()):
    """La liste et le titre, vus de `now` — une date avec son fuseau, celui
    de la machine par défaut —, `extra` y mêlant les échéances d'options.
    Pure : c'est ce que les tests appellent."""
    now = now if now is not None else dt.datetime.now().astimezone()
    events = listed(now, extra=extra)
    if not events:
        body = html.Div(
            "liste de dates épuisée — compléter btcterm/macrocal.py",
            style={"color": C["red"], "fontFamily": MONO,
                   "fontSize": "11px", "padding": "12px"})
    else:
        body = html.Div([_row(e, now) for e in events]
                        + [_footer(now.date())])
    return body, _badges(now)


def register(app, hub):
    @app.callback(
        Output("cal-list", "children"),
        Output("cal-next", "children"),
        # L'horloge rare suffit : les dates ne changent pas, et cinq
        # minutes de retard à dire une publication passée ne trompent
        # personne.
        Input("tick-rare", "n_intervals"),
    )
    def _refresh(_tick):
        # Les échéances d'options de la chaîne déjà en cache — jamais
        # d'attente de Deribit : à froid, le calendrier s'affichait deux
        # secondes et demie plus tard ; une chaîne absente ou illisible
        # laisse le calendrier tenu à la main.
        try:
            extra = tuple(option_events(opt.expiry_summary(
                hub.option_chain_cached())))
        except Exception:
            extra = ()
        return render(extra=extra)
