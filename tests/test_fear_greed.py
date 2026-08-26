#!/usr/bin/env python3
"""
Vérifie l'indice Fear & Greed : lecture de la source, dérivation par le
hub, et cohérence des couleurs entre le badge et la courbe.

Trois pièges valent d'être tenus en respect :

- alternative.me répond **du plus récent au plus ancien** ; tracé tel
  quel, l'historique se lirait à l'envers, et une capitulation
  ressemblerait à une euphorie ;
- le hub ne fait qu'un appel pour les deux usages — le chiffre du badge
  est le dernier point de la courbe. Si la dérivation se trompait de
  bout, le panneau afficherait un chiffre vieux de trois mois ;
- le badge et les bandes du graphique doivent colorer une même valeur de
  la même façon, sans quoi le panneau se contredit à l'écran.

Aucun réseau n'est touché : `requests.get` est remplacé.

Lancement :
    python tests/test_fear_greed.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm import sources  # noqa: E402
from btcterm.hub import MarketHub  # noqa: E402
from terminal.charts import (FEAR_GREED_ZONES,  # noqa: E402
                             build_fear_greed_chart, fear_greed_color)

#: Réponse d'alternative.me, dans son ordre à lui : le plus récent
#: d'abord, valeurs en chaînes de caractères.
REPONSE = {"data": [
    {"value": "65", "value_classification": "Greed", "timestamp": "1756166400"},
    {"value": "22", "value_classification": "Extreme Fear",
     "timestamp": "1756080000"},
    {"value": "18", "value_classification": "Extreme Fear",
     "timestamp": "1755993600"},
]}


class FausseReponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


_VRAI_GET = sources.requests.get


def _stub(payload=REPONSE, erreur=None):
    """Remplace `requests.get` et retient les paramètres reçus."""
    appels = []

    def get(url, params=None, timeout=None):
        appels.append((url, params))
        if erreur is not None:
            raise erreur
        return FausseReponse(payload)

    sources.requests.get = get
    return appels


def test_historique_remis_dans_le_sens_de_lecture():
    appels = _stub()
    try:
        points = sources.fetch_fear_greed_history(90)
    finally:
        sources.requests.get = _VRAI_GET

    assert appels[0][1] == {"limit": 90}, "la profondeur demandée doit passer"
    assert [p["value"] for p in points] == [18, 22, 65], \
        "l'historique doit courir du plus ancien au plus récent"
    assert points[-1]["label"] == "Greed"
    assert points[0]["time"] < points[-1]["time"]
    print("  ✓ 3 points relus dans l'ordre chronologique")


def test_le_badge_est_le_dernier_point_de_la_courbe():
    hub = MarketHub(collect_news=False, keep_journal=False)
    _stub()
    try:
        assert hub.fear_greed() == {"value": 65, "label": "Greed"}
        assert len(hub.fear_greed_history()) == 3
    finally:
        sources.requests.get = _VRAI_GET
    print("  ✓ le chiffre du badge et la fin de la courbe s'accordent")


def test_source_injoignable_ne_casse_rien():
    """Sans cache, une liste vide plutôt qu'une exception."""
    hub = MarketHub(collect_news=False, keep_journal=False)
    _stub(erreur=RuntimeError("réseau coupé"))
    try:
        assert hub.fear_greed_history() == []
        assert hub.fear_greed() is None
    finally:
        sources.requests.get = _VRAI_GET
    print("  ✓ source muette : liste vide, aucun panneau en erreur")


def test_la_serie_precedente_survit_a_une_panne():
    """Une courbe d'un quart d'heure vaut mieux qu'un cadre vide.

    Le cache du hub est vieilli à la main : c'est le seul moyen de faire
    rappeler la source sans attendre les quinze minutes de TTL.
    """
    hub = MarketHub(collect_news=False, keep_journal=False)
    _stub()
    try:
        assert len(hub.fear_greed_history()) == 3
    finally:
        sources.requests.get = _VRAI_GET

    horodatage, valeur = hub._cache._entries["fear_greed_history"]
    hub._cache._entries["fear_greed_history"] = (
        horodatage - MarketHub.TTL_FEAR_GREED - 1, valeur)

    _stub(erreur=RuntimeError("réseau coupé"))
    try:
        assert hub.fear_greed() == {"value": 65, "label": "Greed"}, \
            "la série précédente doit être resservie, pas effacée"
    finally:
        sources.requests.get = _VRAI_GET
    print("  ✓ panne après coup : la dernière courbe connue reste affichée")


def test_couleurs_communes_au_badge_et_aux_bandes():
    """Les zones pavent 0–100 sans trou, et suivent la règle du badge."""
    bornes = [(low, high) for low, high, _, _, _ in FEAR_GREED_ZONES]
    assert bornes[0][0] == 0 and bornes[-1][1] > 100
    for (_, fin), (debut, _) in zip(bornes, bornes[1:]):
        assert fin == debut, "les bandes doivent se toucher sans se chevaucher"

    from terminal.theme import C
    for valeur in (0, 24, 44):
        assert fear_greed_color(valeur) == C["red"]
    for valeur in (45, 54):
        assert fear_greed_color(valeur) == C["yellow"]
    for valeur in (55, 75, 100):
        assert fear_greed_color(valeur) == C["green"]
    print("  ✓ rouge sous 45, vert au-dessus de 55, dans les deux rendus")


def test_figure_sans_donnees():
    """Une source muette donne un cadre expliqué, pas une exception."""
    figure = build_fear_greed_chart([])
    assert figure.layout.annotations[0].text.startswith("indice Fear")
    assert not figure.data
    print("  ✓ historique vide : le graphique le dit")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nIndice Fear & Greed — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("L'indice est lu, dérivé et coloré de façon cohérente.\n")
