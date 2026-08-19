#!/usr/bin/env python3
"""
Vérifie le calendrier macro tenu à la main (`btcterm.macrocal`).

Une liste de dates transcrite à la main a deux façons de se tromper :
la faute de frappe (un 30 février lèverait dès l'import, mais un mardi
écrit à la place d'un mercredi passerait sans bruit) et la conversion
d'heure — l'heure d'été américaine ne commence ni ne finit en même temps
que l'européenne, et un décalage d'une heure sur un CPI ferait rater la
publication.

Lancement :
    python tests/test_macrocal.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datetime as dt  # noqa: E402

from btcterm import macrocal  # noqa: E402


def test_familles_et_volumes():
    """Huit FOMC par an ; douze publications 2026 pour chaque statistique."""
    par_famille = {}
    for event in macrocal.EVENTS:
        assert event.kind in macrocal.KINDS, f"famille inconnue : {event.kind}"
        par_famille.setdefault((event.kind, event.date.year), []).append(event)

    for year in (2026, 2027):
        assert len(par_famille[("FOMC", year)]) == 8, \
            f"le FOMC tient huit réunions par an, pas " \
            f"{len(par_famille[('FOMC', year)])} en {year}"
    for kind in ("CPI", "NFP", "PCE"):
        assert len(par_famille[(kind, 2026)]) == 12, \
            f"{kind} : douze publications attendues en 2026"
    print(f"  ✓ {len(macrocal.EVENTS)} événements, volumes conformes")


def test_jours_de_semaine():
    """Aucune publication un week-end : c'est le filet contre la faute de
    frappe qui déplace une date d'un jour ou d'un mois."""
    for event in macrocal.EVENTS:
        assert event.date.weekday() < 5, \
            f"{event.kind} du {event.date} tombe un week-end"
    print("  ✓ aucune date en week-end")


def test_sep_aux_reunions_trimestrielles():
    """Les projections (SEP) accompagnent les réunions de mars, juin,
    septembre et décembre — le calendrier de la Fed le précise."""
    for event in macrocal.EVENTS:
        if event.kind != "FOMC":
            continue
        attendu = event.date.month in (3, 6, 9, 12)
        assert ("SEP" in event.note) == attendu, \
            f"FOMC du {event.date} : note SEP incohérente"
    print("  ✓ SEP sur les seules réunions trimestrielles")


def test_conversion_heure_new_york():
    """8 h 30 à New York, c'est 13 h 30 UTC en hiver et 12 h 30 UTC en
    été — l'heure d'été américaine commence le 8 mars 2026, avant
    l'européenne."""
    janvier = next(e for e in macrocal.EVENTS
                   if e.kind == "CPI" and e.date == dt.date(2026, 1, 13))
    mars = next(e for e in macrocal.EVENTS
                if e.kind == "CPI" and e.date == dt.date(2026, 3, 11))
    assert janvier.when_utc.hour == 13 and janvier.when_utc.minute == 30
    assert mars.when_utc.hour == 12 and mars.when_utc.minute == 30
    print("  ✓ conversion EST/EDT → UTC")


def test_upcoming_filtre_et_ordonne():
    """`upcoming` garde l'événement du jour, écarte le passé, et rend la
    liste en ordre chronologique."""
    jour = dt.date(2026, 9, 16)  # jour d'un FOMC
    events = macrocal.upcoming(jour)
    assert events[0].date == jour and events[0].kind == "FOMC", \
        "l'événement du jour doit rester listé"
    assert all(e.date >= jour for e in events)
    assert events == sorted(events, key=lambda e: e.when_utc)
    assert len(macrocal.upcoming(jour, limit=5)) == 5
    print(f"  ✓ upcoming : {len(events)} événements au {jour}, ordonnés")


def test_next_of():
    jour = dt.date(2026, 8, 19)
    fomc = macrocal.next_of("FOMC", jour)
    cpi = macrocal.next_of("CPI", jour)
    assert fomc.date == dt.date(2026, 9, 16)
    assert cpi.date == dt.date(2026, 9, 11)
    assert macrocal.next_of("CPI", dt.date(2028, 1, 1)) is None, \
        "liste épuisée : None attendu, pas une exception"
    print("  ✓ next_of : FOMC 16/09, CPI 11/09 vus du 19/08/2026")


def test_horizon():
    """La liste court jusqu'au dernier FOMC publié par la Fed."""
    assert macrocal.last_date() == dt.date(2027, 12, 8)
    print("  ✓ horizon : 8 décembre 2027")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCalendrier macro — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le calendrier tenu à la main est cohérent.\n")
