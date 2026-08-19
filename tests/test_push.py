#!/usr/bin/env python3
"""
Le pousseur WebSocket, contrôlé sans réseau ni navigateur.

`ui_smoke.py` vérifie le canal de bout en bout dans un vrai Firefox ;
ce test couvre ce qui n'a pas besoin d'un navigateur — et qui doit donc
pouvoir échouer sans en lancer un :

- `_merge` : l'état annoncé par le navigateur est une entrée non fiable,
  et un message malformé ne doit jamais fermer le canal ;
- `_frame` : le rendu poussé suit l'état (plateforme, panneau agrandi)
  et vise exactement les cibles que les callbacks pilotent ;
- la sérialisation : les trames différentielles reposent sur la
  comparaison de chaînes — un rendu qui changerait d'une sérialisation
  à l'autre sans raison renverrait tout à chaque cadence.

Le hub n'est jamais démarré : les carnets se remplissent par
`OrderBook.replace`, le fil de liquidations par `_handle`, comme dans
test_liquidations.py. Aucun réseau n'est touché.

Lancement :
    python tests/test_push.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plotly.utils import PlotlyJSONEncoder  # noqa: E402

from btcterm.hub import MarketHub  # noqa: E402
from terminal.panels.liquidations import ROWS_MAX  # noqa: E402
from terminal.panels.orderbook import DEPTH, DEPTH_MAX  # noqa: E402
from terminal.push import DEFAULT_STATE, _frame, _merge  # noqa: E402

CIBLES = {"book-table", "depth-chart", "arb-table", "arb-count",
          "liq-table", "liq-badges"}


def hub_garni(levels: int = DEPTH_MAX + 2) -> MarketHub:
    """Un hub jamais démarré, aux carnets remplis à la main.

    Binance et Kraken reçoivent des niveaux discernables (60 000 contre
    50 000) : les tests peuvent lire dans la trame quelle plateforme a
    été rendue.
    """
    hub = MarketHub(collect_news=False)
    for name, base in (("Binance", 60_000.0), ("Kraken", 50_000.0)):
        hub.books[name].replace(
            bids={base - i: 1.0 + i for i in range(1, levels + 1)},
            asks={base + i: 1.0 + i for i in range(1, levels + 1)},
        )
    return hub


def serialise(frame: dict) -> dict[str, str]:
    return {cible: json.dumps(props, cls=PlotlyJSONEncoder)
            for cible, props in frame.items()}


def lignes(blob: str) -> int:
    """Nombre de lignes de table dans une cible sérialisée."""
    return blob.count('"Tr"')


def test_merge_ne_fait_pas_confiance():
    etat = dict(DEFAULT_STATE)

    #: Les messages légitimes sont appliqués, clé par clé.
    assert _merge(etat, '{"exchange": "Kraken"}')["exchange"] == "Kraken"
    assert _merge(etat, '{"expanded": "book"}')["expanded"] == "book"
    assert _merge(etat, '{"expanded": null}')["expanded"] is None

    #: Tout le reste laisse l'état intact au lieu de casser la boucle :
    #: JSON invalide, JSON qui n'est pas un objet, clés inconnues,
    #: valeurs du mauvais type.
    for hostile in ("n'importe quoi {", "[1, 2]", '"Kraken"', "null",
                    '{"autre": 1}', '{"exchange": 3}', '{"expanded": []}',
                    b"\xff\xfe"):
        assert _merge(etat, hostile) == etat, hostile

    #: L'état d'origine n'est jamais modifié en place — le pousseur
    #: compare l'ancien et le nouveau pour vider son cache d'envoi.
    _merge(etat, '{"exchange": "Kraken", "expanded": "book"}')
    assert etat == DEFAULT_STATE
    print("  ✓ messages légitimes appliqués, hostiles ignorés, état copié")


def test_frame_vise_les_cibles_des_callbacks():
    frame = _frame(hub_garni(), dict(DEFAULT_STATE))
    assert set(frame) == CIBLES, set(frame) ^ CIBLES
    for cible, props in frame.items():
        prop = "figure" if cible == "depth-chart" else "children"
        assert set(props) == {prop}, f"{cible} : {set(props)}"
    print(f"  ✓ {len(CIBLES)} cibles, children partout sauf la figure")


def test_frame_suit_l_etat():
    hub = hub_garni()

    #: La plateforme annoncée choisit le carnet rendu.
    binance = serialise(_frame(hub, {"exchange": "Binance", "expanded": None}))
    kraken = serialise(_frame(hub, {"exchange": "Kraken", "expanded": None}))
    assert "60,001" in binance["book-table"], "carnet Binance attendu"
    assert "50,001" in kraken["book-table"], "carnet Kraken attendu"
    assert "60,001" not in kraken["book-table"]

    #: L'agrandissement du carnet passe de 8 à 20 niveaux par côté
    #: (2 × niveaux + la ligne de spread) — et ne touche que lui.
    plein = serialise(_frame(hub, {"exchange": "Binance", "expanded": "book"}))
    assert lignes(binance["book-table"]) == 2 * DEPTH + 1
    assert lignes(plein["book-table"]) == 2 * DEPTH_MAX + 1
    assert plein["arb-table"] == binance["arb-table"]

    #: Même logique pour les liquidations, avec un fil injecté plus
    #: fourni que la vue repliée.
    from test_liquidations import message
    for i in range(ROWS_MAX + 4):
        hub.liquidations._handle(message(price=str(64_000 + i)))
    replie = serialise(_frame(hub, {"exchange": "Binance", "expanded": None}))
    agrandi = serialise(_frame(hub, {"exchange": "Binance", "expanded": "liq"}))
    assert lignes(agrandi["liq-table"]) == ROWS_MAX
    assert lignes(replie["liq-table"]) < lignes(agrandi["liq-table"])
    print("  ✓ plateforme suivie, agrandissements carnet et liquidations")


def test_carnets_vides_rendus_dits():
    """Un hub sans données doit le dire, pas produire une trame vide."""
    frame = serialise(_frame(MarketHub(collect_news=False),
                             dict(DEFAULT_STATE)))
    assert set(json.loads("{" + ",".join(
        f'"{c}":{b}' for c, b in frame.items()) + "}")) == CIBLES
    assert "connexion en cours" in frame["book-table"]
    assert "en attente" in frame["arb-table"]
    print("  ✓ six cibles même à vide, chacune disant son état")


def test_serialisation_stable():
    """Deux rendus du même état doivent se sérialiser à l'identique.

    C'est l'hypothèse des trames différentielles : le pousseur compare
    des chaînes, et une cible dont la sérialisation fluctuerait sans
    changement de données repartirait à chaque cadence. Seul le carnet
    y échappe — il affiche l'âge de son flux, qui avance réellement.
    """
    hub = hub_garni()
    etat = dict(DEFAULT_STATE)
    un, deux = serialise(_frame(hub, etat)), serialise(_frame(hub, etat))
    for cible in CIBLES - {"book-table"}:
        assert un[cible] == deux[cible], f"{cible} fluctue sans raison"
    print("  ✓ cibles stables à données constantes — le différentiel a un sens")


def test_l_age_du_carnet_avance():
    """Le pendant du test précédent : l'âge affiché doit vivre.

    Si le carnet se sérialisait à l'identique alors que le temps passe,
    c'est que l'âge du flux — le témoin de fraîcheur du panneau — serait
    figé au moment du snapshot.
    """
    hub = hub_garni()
    etat = dict(DEFAULT_STATE)
    un = serialise(_frame(hub, etat))["book-table"]
    time.sleep(0.05)
    deux = serialise(_frame(hub, etat))["book-table"]
    assert un != deux, "l'âge du flux n'avance pas"
    print("  ✓ l'âge du flux avance d'un rendu à l'autre")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nPousseur WebSocket — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le pousseur rend ce que les callbacks rendraient.\n")
