"""
Calendrier macro : les dates qui font bouger le marché, tenues à la main.

Aucune source publique satisfaisante n'existe pour un calendrier
économique — les API ouvertes sont payantes ou sans licence claire. Mais
les émetteurs eux-mêmes publient leurs dates un à deux ans à l'avance :
ce module est la transcription de ces calendriers officiels, vérifiée à
la source.

| Événement | Source | Publié jusqu'à |
|---|---|---|
| FOMC (décision de taux) | federalreserve.gov/monetarypolicy/fomccalendars.htm | déc. 2027 |
| CPI | bls.gov/schedule/news_release/cpi.htm | déc. 2027 |
| NFP | bls.gov/schedule/news_release/empsit.htm | déc. 2027 |
| PCE | apps.bea.gov/API/signup/release_dates.json (« Personal Income and Outlays ») | déc. 2027 |

Le BLS refuse les clients en ligne de commande (403) : ses pages se lisent
au navigateur. Le BEA, lui, publie un fichier JSON de toutes ses dates —
c'est par lui que l'année 2027 du PCE a été transcrite, le 6 novembre 2026.
Le même fichier montre que les publications du PCE de janvier à avril 2026
ont été avancées après coup (rattrapage du calendrier de 2025) ; passées,
elles sont laissées telles que le calendrier initial les donnait.

Une liste tenue à la main s'épuise, et chaque famille à son rythme :
`last_dates()` dit jusqu'où court chacune, `last_date()` jusqu'où la liste
est complète — la plus proche des quatre —, et le panneau le dit. Il disait
le dernier FOMC, décembre 2027, quand le CPI, le NFP et le PCE s'arrêtaient
un an plus tôt : un calendrier qui se tait parce qu'il est périmé doit se
voir, comme un fil de news figé.

Les heures sont celles de New York, où ces publications sont définies
(8 h 30 pour les statistiques, 14 h pour le FOMC) ; `when_utc` fait la
conversion en tenant compte de l'heure d'été américaine, dont les bornes
ne coïncident pas avec les européennes.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

#: Heures de publication, heure de New York.
TIME_STATS = dt.time(8, 30)   # CPI, NFP, PCE
TIME_FOMC = dt.time(14, 0)    # communiqué du FOMC


@dataclass(frozen=True)
class MacroEvent:
    date: dt.date       # jour de la publication
    time_ny: dt.time    # heure de New York
    kind: str           # "FOMC" | "CPI" | "NFP" | "PCE" | "OPT" | "US"
    label: str
    note: str = ""
    #: L'impact annoncé par Forex Factory (« High », « Medium ») pour les
    #: publications de la semaine (§ week_events) ; vide pour les autres.
    impact: str = ""

    @property
    def when_utc(self) -> dt.datetime:
        return dt.datetime.combine(
            self.date, self.time_ny, tzinfo=NEW_YORK
        ).astimezone(dt.timezone.utc)

    def days_until(self, today: dt.date) -> int:
        return (self.date - today).days


#: Réunions du FOMC : (année, mois, jour du communiqué — le second jour
#: de la réunion), et si elle s'accompagne des projections économiques
#: (SEP, le « dot plot »), qui pèsent autant que la décision elle-même.
_FOMC = (
    (2026, 1, 28, False), (2026, 3, 18, True), (2026, 4, 29, False),
    (2026, 6, 17, True), (2026, 7, 29, False), (2026, 9, 16, True),
    (2026, 10, 28, False), (2026, 12, 9, True),
    (2027, 1, 27, False), (2027, 3, 17, True), (2027, 4, 28, False),
    (2027, 6, 9, True), (2027, 7, 28, False), (2027, 9, 15, True),
    (2027, 10, 27, False), (2027, 12, 8, True),
)

#: Publications statistiques : par année, le jour du mois, indexé par
#: mois de publication. La donnée publiée porte toujours sur le mois
#: précédent — le CPI de janvier 2027 est celui de décembre 2026.
_CPI = {
    2026: (13, 11, 11, 10, 12, 10, 14, 12, 11, 14, 10, 10),
    2027: (13, 11, 10, 13, 12, 10, 14, 11, 14, 14, 10, 10),
}
_NFP = {
    2026: (9, 6, 6, 3, 8, 5, 2, 7, 4, 2, 6, 4),
    2027: (8, 5, 5, 2, 7, 4, 2, 6, 3, 8, 5, 3),
}
_PCE = {
    2026: (29, 26, 27, 30, 28, 25, 30, 26, 30, 29, 25, 23),
    2027: (28, 25, 31, 29, 27, 25, 29, 25, 30, 28, 24, 22),
}

_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre")


def _stats(kind: str, label: str,
           by_year: dict[int, tuple[int, ...]]) -> list[MacroEvent]:
    events = []
    for year, days in by_year.items():
        for month, day in enumerate(days, start=1):
            ref = _MOIS[month - 2]  # la donnée porte sur le mois précédent
            de = "d'" if ref[0] in "aâo" else "de "
            events.append(MacroEvent(dt.date(year, month, day), TIME_STATS,
                                     kind, label, note=f"données {de}{ref}"))
    return events


def _build_events() -> tuple[MacroEvent, ...]:
    events = [
        MacroEvent(dt.date(year, month, day), TIME_FOMC, "FOMC",
                   "Décision de taux de la Fed",
                   note="avec projections (SEP)" if sep else "")
        for year, month, day, sep in _FOMC
    ]
    events += _stats("CPI", "Inflation US (CPI)", _CPI)
    events += _stats("NFP", "Emploi US (NFP)", _NFP)
    events += _stats("PCE", "Inflation PCE", _PCE)
    return tuple(sorted(events, key=lambda e: e.when_utc))


EVENTS: tuple[MacroEvent, ...] = _build_events()

KINDS = ("FOMC", "CPI", "NFP", "PCE")


def upcoming(today: dt.date | None = None,
             limit: int | None = None) -> list[MacroEvent]:
    """Les événements à venir, celui du jour compris.

    Un événement reste listé toute sa journée, même une fois l'heure
    passée : « aujourd'hui » est précisément l'information qu'on vient
    chercher en séance.
    """
    today = today if today is not None else dt.datetime.now(NEW_YORK).date()
    events = [e for e in EVENTS if e.date >= today]
    return events[:limit] if limit else events


def next_of(kind: str, today: dt.date | None = None) -> MacroEvent | None:
    """Le prochain événement d'une famille donnée, ou None si la liste
    est épuisée pour cette famille."""
    return next((e for e in upcoming(today) if e.kind == kind), None)


def last_dates() -> dict[str, dt.date]:
    """Jusqu'où court chaque famille, dans l'ordre de `KINDS`."""
    return {kind: max(e.date for e in EVENTS if e.kind == kind)
            for kind in KINDS}


def last_date() -> dt.date:
    """Jusqu'où la liste est complète — la famille qui s'épuise la
    première. Au-delà, elle est à compléter ici."""
    return min(last_dates().values())


#: Les publications de la semaine retenues : américaines — le dollar mène
#: le bitcoin —, d'impact fort ou moyen. Le faible (une enquête régionale,
#: une adjudication) noierait les dix lignes du panneau.
WEEK_COUNTRY = "USD"
WEEK_IMPACTS = ("High", "Medium")


def week_events(rows, known: tuple[MacroEvent, ...] = EVENTS
                ) -> list[MacroEvent]:
    """Le calendrier de Forex Factory (§ sources.fetch_week_calendar), en
    événements `US` mêlés à la liste tenue à la main.

    Les publications d'un même instant ne font qu'une ligne — le PPI et le
    PPI sous-jacent tombent ensemble —, à l'impact le plus fort, la
    prévision et le précédent de la première d'impact fort. Un instant
    déjà tenu à la main (CPI, NFP, PCE, FOMC) est écarté : la liste à la
    main le dit mieux, en français et datée de la donnée.
    """
    taken = {event.when_utc for event in known}
    by_time: dict[dt.datetime, list[dict]] = {}
    for row in rows or ():
        if (row.get("country") != WEEK_COUNTRY
                or row.get("impact") not in WEEK_IMPACTS):
            continue
        when = row["when"].astimezone(dt.timezone.utc)
        if when in taken:
            continue
        by_time.setdefault(when, []).append(row)
    events = []
    for when, group in sorted(by_time.items()):
        group.sort(key=lambda r: WEEK_IMPACTS.index(r["impact"]))
        lead = group[0]
        note = " · ".join(part for part in (
            f"prév. {lead['forecast']}" if lead["forecast"] else "",
            f"préc. {lead['previous']}" if lead["previous"] else "") if part)
        local = when.astimezone(NEW_YORK)
        events.append(MacroEvent(
            local.date(), local.time().replace(tzinfo=None), "US",
            " · ".join(row["title"] for row in group), note=note,
            impact=lead["impact"]))
    return events

