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
    """Huit FOMC par an ; douze publications par an pour chaque statistique."""
    par_famille = {}
    for event in macrocal.EVENTS:
        assert event.kind in macrocal.KINDS, f"famille inconnue : {event.kind}"
        par_famille.setdefault((event.kind, event.date.year), []).append(event)

    for year in (2026, 2027):
        assert len(par_famille[("FOMC", year)]) == 8, \
            f"le FOMC tient huit réunions par an, pas " \
            f"{len(par_famille[('FOMC', year)])} en {year}"
    for kind in ("CPI", "NFP", "PCE"):
        for year in (2026, 2027):
            assert len(par_famille[(kind, year)]) == 12, \
                f"{kind} : douze publications attendues en {year}"
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
    """La liste est complète jusqu'à la famille qui s'épuise la première.

    `last_date` rendait le dernier FOMC, décembre 2027, quand le CPI, le
    NFP et le PCE s'arrêtaient en décembre 2026 : le panneau ne pouvait pas
    prévenir de l'épuisement qui venait.
    """
    fins = macrocal.last_dates()
    assert fins == {"FOMC": dt.date(2027, 12, 8), "CPI": dt.date(2027, 12, 10),
                    "NFP": dt.date(2027, 12, 3), "PCE": dt.date(2027, 12, 22)}, fins
    assert macrocal.last_date() == dt.date(2027, 12, 3)
    print("  ✓ horizon : complet jusqu'au 3 décembre 2027 (NFP)")


def test_le_mois_de_reference_passe_l_annee():
    """Le CPI de janvier 2027 porte sur décembre 2026 ; les NFP tombent un
    vendredi — le filet contre une date recopiée de travers."""
    janvier = macrocal.next_of("CPI", dt.date(2027, 1, 1))
    assert janvier.date == dt.date(2027, 1, 13)
    assert janvier.note == "données de décembre"
    assert all(e.date.weekday() == 4 for e in macrocal.EVENTS
               if e.kind == "NFP" and e.date.year == 2027)
    print("  ✓ CPI de janvier 2027 sur décembre ; NFP 2027 le vendredi")


def _texte(composant) -> str:
    if composant is None:
        return ""
    if isinstance(composant, (str, int, float)):
        return str(composant)
    if isinstance(composant, (list, tuple)):
        return "".join(_texte(c) for c in composant)
    return _texte(getattr(composant, "children", None))


def test_panneau_dans_le_fuseau_de_la_machine():
    """Le panneau écrit le jour, l'heure et le compte à rebours dans un
    seul fuseau, celui de la machine.

    Constaté le 11 novembre 2026 depuis Phnom Penh (UTC+7) : le FOMC du
    9 décembre s'écrivait « mer. 9 déc. 02:00 », jour de New York et
    heure locale, et le CPI de la veille au soir restait « auj. ».
    """
    from zoneinfo import ZoneInfo

    from terminal.panels import calendar as panneau

    phnom_penh = ZoneInfo("Asia/Phnom_Penh")
    # Le lendemain matin du CPI du 10 novembre (13:30 UTC, 20:30 locales).
    matin = dt.datetime(2026, 11, 11, 10, 17, tzinfo=phnom_penh)
    corps, titre = panneau.render(matin)
    lignes = [_texte(ligne) for ligne in corps.children[:-1]]
    assert "CPI" not in lignes[0] and "PCE" in lignes[0], lignes[0]
    assert not any("passé" in l or "auj." in l for l in lignes), lignes[:2]
    assert _texte(titre) == "FOMC J-29 · CPI J-29", _texte(titre)
    fomc = next(l for l in lignes if "FOMC" in l)
    assert fomc.startswith("jeu. 10 déc.  02:00"), fomc
    assert "J-29" in fomc

    # Le soir même, une heure après la publication : encore listée,
    # mais dite passée, et le titre regarde déjà la suivante.
    soir = dt.datetime(2026, 11, 10, 21, 30, tzinfo=phnom_penh)
    corps, titre = panneau.render(soir)
    premiere = _texte(corps.children[0])
    assert premiere.startswith("mar. 10 nov.  20:30") and "passé" in premiere
    assert corps.children[0].style.get("opacity"), "une ligne passée s'efface"
    assert "CPI J-30" in _texte(titre), _texte(titre)

    # Une heure avant : aujourd'hui, en rouge.
    avant = dt.datetime(2026, 11, 10, 19, 30, tzinfo=phnom_penh)
    corps, titre = panneau.render(avant)
    assert "auj." in _texte(corps.children[0])
    assert "CPI auj." in _texte(titre)
    # Et la veille au soir : demain, même à moins de vingt-quatre heures.
    veille = dt.datetime(2026, 11, 9, 23, 0, tzinfo=phnom_penh)
    assert "demain" in _texte(panneau.render(veille)[0].children[0])

    # À New York, rien ne change : jour et heure de la source.
    new_york = dt.datetime(2026, 11, 10, 7, 0, tzinfo=macrocal.NEW_YORK)
    corps, _ = panneau.render(new_york)
    lignes = [_texte(ligne) for ligne in corps.children[:-1]]
    assert lignes[0].startswith("mar. 10 nov.  08:30") and "auj." in lignes[0]
    assert next(l for l in lignes if "FOMC" in l).startswith(
        "mer.  9 déc.  14:00")

    # Une liste épuisée se dit toujours.
    fin = dt.datetime(2028, 6, 1, 12, 0, tzinfo=phnom_penh)
    assert "épuisée" in _texte(panneau.render(fin)[0])
    print("  ✓ jour, heure et compte à rebours dans le fuseau de la machine")



def test_les_echeances_d_options_au_calendrier():
    """Les échéances qui pèsent, lues dans la chaîne Deribit, se mêlent aux
    dates tenues à la main, à 8 h UTC — 15 h à Phnom Penh —, avec leur
    montant, leur max pain et leur put/call ; les petites restent dehors."""
    import pandas as pd
    from btcterm import options as opt
    from terminal.panels import calendar as panneau

    def ligne(nom, oi, sorte="C"):
        return {"instrument_name": f"BTC-{nom}-75000-{sorte}", "mark_iv": 50.0,
                "open_interest": oi, "underlying_price": 75_000.0,
                "estimated_delivery_price": 75_000.0, "volume_usd": 1.0}

    chaine = opt.option_chain([ligne("27NOV26", 6_000), ligne("27NOV26", 3_000, "P"),
                               ligne("14NOV26", 100), ligne("25DEC26", 20_000)])
    maintenant = pd.Timestamp("2026-11-12 09:00", tz="UTC")
    resume = opt.expiry_summary(chaine, maintenant)
    assert [f"{e['expiry']:%d/%m}" for e in resume] == ["27/11", "25/12"], \
        "la quotidienne du 14, moins de 5 %, reste dehors"
    novembre = resume[0]
    assert novembre["oi"] == 9_000 and novembre["notional"] == 9_000 * 75_000
    assert novembre["pc"] == 0.5 and novembre["pain"] == 75_000

    evenements = panneau.option_events(resume)
    assert evenements[0].kind == "OPT"
    assert evenements[0].when_utc == dt.datetime(2026, 11, 27, 8,
                                                 tzinfo=dt.timezone.utc)
    assert "0.7 Md$" in evenements[0].note and "P/C 0.50" in evenements[0].note

    phnom_penh = dt.datetime(2026, 11, 12, 16, tzinfo=dt.timezone(dt.timedelta(hours=7)))
    liste = panneau.listed(phnom_penh, limit=40, extra=tuple(evenements))
    quand = [e.when_utc for e in liste]
    assert quand == sorted(quand), "les échéances prennent leur rang"
    assert sum(e.kind == "OPT" for e in liste) == 2
    corps, _ = panneau.render(phnom_penh, extra=tuple(evenements))
    texte = str(corps)
    assert "Échéance Deribit" in texte and "15:00" in texte
    assert opt.expiry_summary(chaine.iloc[0:0]) == []
    print("  ✓ échéances lourdes au calendrier, à 8 h UTC, montant, max pain, P/C")


def test_le_calendrier_n_attend_pas_deribit():
    """Le calendrier lit la chaîne en cache, jamais le réseau : à froid,
    il s'affichait deux secondes et demie plus tard. Vide à froid, la
    chaîne du cache ensuite, même vieillie."""
    from unittest import mock
    from btcterm import options as opt
    from btcterm.hub import MarketHub

    hub = MarketHub(collect_news=False, keep_journal=False)
    with mock.patch("btcterm.sources.fetch_deribit_summary",
                    side_effect=AssertionError("réseau touché")):
        assert hub.option_chain_cached().empty
        chaine = opt.option_chain([{
            "instrument_name": "BTC-27NOV26-75000-C", "mark_iv": 50.0,
            "open_interest": 10.0, "underlying_price": 75_000.0,
            "estimated_delivery_price": 75_000.0, "volume_usd": 1.0}])
        hub._cache.get("options", 60, lambda: chaine)
        assert len(hub.option_chain_cached()) == 1
    print("  ✓ la chaîne du cache, ou rien : jamais d'attente de Deribit")

if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCalendrier macro — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le calendrier tenu à la main est cohérent.\n")
