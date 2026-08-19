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
| CPI, NFP | calendrier OMB 2026 des indicateurs fédéraux (bls.gov/schedule) | déc. 2026 |
| PCE | même calendrier OMB (bea.gov/news/schedule) | déc. 2026 |

Une liste tenue à la main s'épuise : `last_date()` dit jusqu'où elle
court, et le panneau l'affiche — un calendrier qui se tait parce qu'il
est périmé doit se voir, comme un fil de news figé.

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
    kind: str           # "FOMC" | "CPI" | "NFP" | "PCE"
    label: str
    note: str = ""

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

#: Publications statistiques 2026 : jour du mois, indexé par mois. La
#: donnée publiée porte toujours sur le mois précédent.
_CPI_2026 = (13, 11, 11, 10, 12, 10, 14, 12, 11, 14, 10, 10)
_NFP_2026 = (9, 6, 6, 3, 8, 5, 2, 7, 4, 2, 6, 4)
_PCE_2026 = (29, 26, 27, 30, 28, 25, 30, 26, 30, 29, 25, 23)

_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre")


def _stats(kind: str, label: str, days: tuple[int, ...]) -> list[MacroEvent]:
    events = []
    for month, day in enumerate(days, start=1):
        ref = _MOIS[month - 2]  # la donnée porte sur le mois précédent
        de = "d'" if ref[0] in "aâo" else "de "
        events.append(MacroEvent(dt.date(2026, month, day), TIME_STATS,
                                 kind, label, note=f"données {de}{ref}"))
    return events


def _build_events() -> tuple[MacroEvent, ...]:
    events = [
        MacroEvent(dt.date(year, month, day), TIME_FOMC, "FOMC",
                   "Décision de taux de la Fed",
                   note="avec projections (SEP)" if sep else "")
        for year, month, day, sep in _FOMC
    ]
    events += _stats("CPI", "Inflation US (CPI)", _CPI_2026)
    events += _stats("NFP", "Emploi US (NFP)", _NFP_2026)
    events += _stats("PCE", "Inflation PCE", _PCE_2026)
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


def last_date() -> dt.date:
    """Jusqu'où court la liste — au-delà, elle est à compléter ici."""
    return EVENTS[-1].date
