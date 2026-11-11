#!/usr/bin/env python3
"""
Vérifie le câblage du terminal : tout panneau déclaré doit être posé dans
la grille **et** avoir ses callbacks enregistrés.

C'est la classe d'erreur qui a laissé le panneau ETF muet à sa création :
il était écrit, mais ni placé dans la grille ni enregistré, et rien ne le
signalait — l'application démarrait normalement, le panneau restait
simplement absent.

Aucun réseau n'est touché : le hub est construit sans être démarré.

Lancement :
    python tests/test_terminal_wiring.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import importlib  # noqa: E402
import pkgutil  # noqa: E402

from btcterm.hub import MarketHub  # noqa: E402
from terminal.app import create_app  # noqa: E402
from terminal.grid import CELLS, DEFAULT_PLACEMENT, body  # noqa: E402
from terminal.header import (change_text, position_text,  # noqa: E402
                             price_text, price_title, sources_text)
from terminal.panels import PANELS  # noqa: E402


def discover_panels():
    """Modules de panneaux présents sur le disque.

    La découverte ne passe surtout pas par `PANELS` : un test qui
    parcourrait `PANELS` ne verrait pas un panneau qu'on aurait oublié
    d'y inscrire — c'est précisément le défaut à détecter.
    """
    import terminal.panels as package

    return [
        importlib.import_module(f"terminal.panels.{info.name}")
        for info in pkgutil.iter_modules(package.__path__)
        if not info.name.startswith("_")
    ]


DISCOVERED = discover_panels()


def collect_ids(node, found=None):
    """Identifiants de tous les composants d'un arbre de layout Dash."""
    found = found if found is not None else set()
    if isinstance(node, (list, tuple)):
        for child in node:
            collect_ids(child, found)
    elif hasattr(node, "children") or hasattr(node, "id"):
        component_id = getattr(node, "id", None)
        if isinstance(component_id, str):
            found.add(component_id)
        collect_ids(getattr(node, "children", None), found)
    return found


def panel_ids(panel) -> set[str]:
    """Identifiants déclarés par un panneau, layouts secondaires compris."""
    ids = collect_ids(panel.layout())
    for name in dir(panel):
        if name.endswith("_layout"):
            ids |= collect_ids(getattr(panel, name)())
    return ids


HUB = MarketHub()
APP = create_app(HUB)

#: Identifiants présents dans la page, onglets compris. Une cellule ne
#: rend que son panneau actif — c'est ce qui rend les onglets gratuits —
#: mais un panneau derrière un onglet reste posé dans la grille, et le
#: test doit le voir comme tel.
APP_IDS = collect_ids(APP.layout)
for _area, _panels in CELLS.items():
    for _panel_id, _label, _layout in _panels:
        APP_IDS |= collect_ids(body(_area, _panel_id, DEFAULT_PLACEMENT))

CALLBACK_KEYS = " ".join(APP.callback_map)


def test_tous_les_panneaux_sont_declares():
    """Tout module de panneau sur le disque doit figurer dans `PANELS`."""
    assert DISCOVERED, "aucun module de panneau découvert"
    oublies = [p.__name__ for p in DISCOVERED if p not in PANELS]
    assert not oublies, f"panneaux écrits mais absents de PANELS : {oublies}"
    print(f"  ✓ {len(DISCOVERED)} panneaux découverts, tous déclarés")


def test_panneaux_places_dans_la_grille():
    """Tout identifiant déclaré par un panneau doit exister dans la page."""
    for panel in DISCOVERED:
        name = panel.__name__.rsplit(".", 1)[-1]
        manquants = panel_ids(panel) - APP_IDS
        assert not manquants, f"panneau {name} : {sorted(manquants)} absents de la grille"
        print(f"  ✓ {name:10s} posé ({len(panel_ids(panel))} composants)")


def test_panneaux_enregistres():
    """Tout panneau doit piloter au moins une sortie de callback."""
    for panel in DISCOVERED:
        name = panel.__name__.rsplit(".", 1)[-1]
        pilotes = [i for i in panel_ids(panel) if f"{i}." in CALLBACK_KEYS]
        assert pilotes, f"panneau {name} : aucun callback enregistré"
        print(f"  ✓ {name:10s} branché ({len(pilotes)} sorties)")


def test_tous_les_layouts_sont_atteignables():
    """Tout layout écrit doit être posé dans une cellule.

    Depuis les onglets, un panneau peut exister, être enregistré, et
    n'être atteignable par aucun clic — l'équivalent moderne du panneau
    oublié. `CELLS` est la seule liste qui décide de ce qu'on peut
    afficher : c'est donc elle qu'on compare aux layouts écrits.
    """
    poses = {fonction for panels in CELLS.values() for _, _, fonction in panels}
    for panel in DISCOVERED:
        name = panel.__name__.rsplit(".", 1)[-1]
        ecrits = {getattr(panel, attribut) for attribut in dir(panel)
                  if attribut == "layout" or attribut.endswith("_layout")}
        orphelins = ecrits - poses
        assert not orphelins, f"panneau {name} : layout écrit mais absent de CELLS"
        print(f"  ✓ {name:10s} atteignable ({len(ecrits)} layout(s))")


def test_horloges_declarees():
    """Les trois régimes de rafraîchissement doivent être présents."""
    for tick in ("tick-fast", "tick-slow", "tick-rare"):
        assert tick in APP_IDS, f"horloge {tick} absente"
    print("  ✓ tick-fast / tick-slow / tick-rare")


def test_pousseur_branche():
    """La route /push et ses relais d'état doivent être en place.

    Sans la route, push.js échoue en silence et le terminal reste en
    interrogation — rien ne le signalerait au démarrage, c'est le même
    genre de mutisme que le panneau oublié. Le rendu poussé, lui, est
    couvert hors ligne par test_push.py.
    """
    rules = {rule.rule for rule in APP.server.url_map.iter_rules()}
    assert "/push" in rules, "route /push absente du serveur Flask"
    assert "expanded." in CALLBACK_KEYS, "Store expanded sans callback"
    for sink in ("push-sink-expanded", "push-sink-exchange",
                 "push-sink-currency"):
        assert sink in APP_IDS, f"{sink} absent de la page"
        assert f"{sink}." in CALLBACK_KEYS, f"{sink} sans relais clientside"
    print("  ✓ route /push posée, relais d'état branchés")


def test_la_page_connait_son_serveur():
    """La page emporte le jeton du serveur qui l'a servie, et /api/boot
    dit celui du serveur qui répond : c'est leur différence qui fait
    recharger un onglet d'avant le redémarrage (terminal/push.py)."""
    client = APP.server.test_client()
    boot = client.get("/api/boot").get_json()["boot"]
    assert len(boot) == 32, boot
    page = client.get("/").get_data(as_text=True)
    assert f'name="btcterm-boot" content="{boot}"' in page, \
        "la page ne porte pas le jeton de son serveur"
    print("  ✓ /api/boot et la balise meta portent le même jeton")


def test_panneau_prix_lwc_cable():
    """Le rendu Lightweight Charts du panneau prix est posé, branché et
    servi : div, Stores, relais clientside et routes d'API.

    Même classe d'erreur que le panneau oublié : un div monté sans ses
    callbacks, ou des routes absentes, laisserait un panneau prix
    éternellement vide sans rien signaler au démarrage.
    """
    assert "price-lwc" in APP_IDS, "div du rendu LWC absent"
    assert "price-chart" not in APP_IDS, "la figure Plotly du prix a disparu"
    assert "lwc-config" in APP_IDS, "Store de configuration absent"
    for sink in ("lwc-sink", "lwc-poll-sink", "lwc-alert-sink"):
        assert sink in APP_IDS, f"{sink} absent de la page"
        assert f"{sink}." in CALLBACK_KEYS, f"{sink} sans callback clientside"

    rules = {rule.rule for rule in APP.server.url_map.iter_rules()}
    for route in ("/api/klines", "/api/profile", "/push"):
        assert route in rules, f"route {route} absente"
    print("  ✓ panneau prix LWC : div posé, trois relais branchés, routes servies")


def test_entete_branche():
    for component_id in ("hdr-price", "hdr-change", "hdr-spread", "hdr-status"):
        assert component_id in APP_IDS, f"{component_id} absent de l'en-tête"
        assert f"{component_id}." in CALLBACK_KEYS, f"{component_id} sans callback"
    print("  ✓ bandeau supérieur")


def test_entete_suit_la_devise():
    """Le cours du bandeau suit la bascule $/€ du panneau prix.

    Il restait en dollars quand le graphique passait en euros : le
    bandeau ne connaissait pas le sélecteur. Il passe par le Store
    global `currency`, toujours dans la page, parce que le sélecteur,
    lui, disparaît avec le panneau prix quand celui-ci est caché.
    """
    assert "currency" in APP_IDS, "Store currency absent de la page"
    assert "currency." in CALLBACK_KEYS, "Store currency sans relais"
    lecteurs = [cb for cb in APP.callback_map.values()
                if any(i["id"] == "currency" for i in cb["inputs"])]
    assert lecteurs, "le bandeau ne lit pas la devise"

    assert price_text(100_000.0, "USD", 0.9) == "100,000.00 $"
    assert price_text(100_000.0, None, 0.9) == "100,000.00 $"
    assert price_text(100_000.0, "EUR", 0.9) == "90,000.00 €"
    assert price_text(None, "EUR", 0.9) == "—"
    # Un taux de secours se voit dans le cours et se dit dans l'infobulle ;
    # il ne touche pas au cours en dollars.
    assert price_text(100_000.0, "EUR", 0.9, stale=True) == "⚠ 90,000.00 €"
    assert price_text(100_000.0, "USD", 1.0, stale=True) == "100,000.00 $"
    assert "1 $ = 0.9000 €" in price_title("EUR", 0.9)
    assert "secours" in price_title("EUR", 0.9, stale=True)
    assert "€" not in price_title("USD", 1.0)
    print("  ✓ le cours du bandeau suit la devise du panneau prix")


def test_les_panneaux_de_marche_suivent_la_devise():
    """Tout ce qui décrit le marché du moment lit la devise en Input.

    Le relais du Store se lit dans le navigateur, le rendu dans `render` :
    ce test tient le fil entre les deux, que rien d'autre ne contrôle
    — un Input oublié ne casse rien, la bascule ne se voit juste plus.
    """
    cibles = ("arb-table", "liq-table", "perp-badges", "dominance-badges",
              "lwc-perp-sink")
    for cible in cibles:
        lecteurs = [cb for cle, cb in APP.callback_map.items()
                    if f"{cible}." in cle
                    and any(i["id"] == "currency" for i in cb["inputs"])]
        assert lecteurs, f"{cible} ne suit pas la devise"
    print(f"  ✓ {len(cibles)} sorties branchées sur la devise")


def test_les_badges_lents_convertissent():
    from terminal.fx import DOLLARS, Display
    from terminal.panels import dominance, perp
    import pandas as pd

    euros = Display(0.5, "€", "⚠ ", "converti à 1 $ = 0.5000 €")
    agregats = {"shares": {"BTC": 59.0}, "total_cap_usd": 2e12,
                "total_volume_usd": 80e9, "cap_change_24h": 1.0}
    assert "2.00 T$" in str(dominance._badges(agregats, DOLLARS))
    converti = str(dominance._badges(agregats, euros))
    assert "1.00 T€" in converti and "40 Md€" in converti
    assert "⚠" in converti and "0.5000" in converti
    # La variation sur 24 h : celle du journal quand il la mesure, celle
    # de la source en gris sinon — elle s'est montrée fausse de deux points.
    source = str(dominance._badges(agregats, DOLLARS))
    assert "+1.00 %" in source and "univers non garanti" in source
    journal = str(dominance._badges(agregats, DOLLARS, change=(-1.69, 24.2)))
    assert "-1.69 %" in journal and "+1.00 %" not in journal
    assert "sur 24 h, mesurée au journal" in journal
    assert "sur 22 h" in str(dominance._badges(agregats, DOLLARS,
                                               change=(-1.69, 22.4)))

    oi = pd.DataFrame({"oi_usd": [8e9]})
    instant = {"funding_rate": 0.0001}
    assert "OI 8.00 Md$" in str(perp._badges(instant, oi))
    converti = str(perp._badges(instant, oi, fx=euros))
    assert "OI 4.00 Md€" in converti and "⚠" in converti
    print("  ✓ dominance et perpétuel convertis, taux de secours signalé")


def test_carnet_suit_la_devise():
    """Le carnet lit la devise en Input, comme le bandeau : le sélecteur
    quitte la page avec le panneau prix, et la bascule doit se voir tout
    de suite, y compris quand le canal push a coupé l'horloge."""
    carnet = APP.callback_map["book-data.data"]
    assert {"id": "currency", "property": "data"} in carnet["inputs"]
    print("  ✓ le carnet suit la devise du panneau prix")


def test_la_position_au_bandeau():
    """Le bandeau lit le Store global : le panneau peut être replié."""
    assert "position" in APP_IDS and "hdr-position" in APP_IDS
    assert "position." in CALLBACK_KEYS, "Store position sans relais"
    assert position_text(None, 100.0)[0] == ""
    assert position_text({"side": "long"}, 100.0)[0] == ""

    texte, style = position_text({"side": "long", "quantity": 2, "entry": 90.0},
                                 100.0)
    assert texte == "long +20 $ (+11.11 %)" and style["color"] == "#00d4aa"
    texte, style = position_text({"side": "short", "quantity": 2, "entry": 90.0},
                                 100.0)
    assert texte.startswith("short -20 $") and style["color"] != "#00d4aa"
    print("  ✓ position suivie au bandeau, dans le bon sens")


def test_les_sources_en_panne_au_bandeau():
    assert "hdr-sources" in APP_IDS
    assert "hdr-sources.children" in CALLBACK_KEYS
    assert sources_text({}, 1000.0) == ("", "")

    texte, infobulle = sources_text({"perp": 400.0, "klines": 880.0}, 1000.0)
    assert texte == "⚠ 2 sources"
    assert "perpétuel depuis 10 min" in infobulle
    assert "chandeliers Binance depuis 2 min" in infobulle

    #: Les deux lectures de la chaîne ne font qu'une source.
    assert sources_text({"chain": 700.0, "chain_stats": 400.0},
                        1000.0) == ("⚠ 1 source", "on-chain depuis 10 min"
                                    " — cliquer pour le détail")
    print("  ✓ sources en panne nommées au bandeau, la plus ancienne date")


def test_variation_inconnue_dite_telle():
    """Binance muet : le ticker est vide, la variation n'est pas +0,00 %."""
    texte, style = change_text({})
    assert texte == "— % 24 h" and "+" not in texte, texte
    texte, style = change_text({"priceChangePercent": "-1.5"})
    assert texte == "-1.50 % 24 h"
    texte, _ = change_text({"priceChangePercent": "0.25"})
    assert texte == "+0.25 % 24 h"
    print("  ✓ variation 24 h inconnue affichée comme telle")



def test_le_bandeau_compte_tous_les_flux():
    """Le bandeau disait « 5/5 flux » pour les seuls carnets ; il compte
    aussi les fils de liquidations et le ruban, et nomme ceux qui manquent."""
    from terminal.header import links_text

    texte, infobulle = links_text([("carnet Binance", True),
                                   ("liquidations Binance", False),
                                   ("ruban Coinbase", True)])
    assert texte == "2/3 flux"
    assert infobulle.startswith("sans liquidations Binance")
    assert links_text([("carnet Kraken", True)])[1].startswith("tous les liens")

    from btcterm.hub import MarketHub
    hub = MarketHub(collect_news=False, keep_journal=False)
    noms = [nom for nom, _ in hub.realtime_links()]
    assert len([n for n in noms if n.startswith("carnet")]) == 5
    print("  ✓ tous les liens temps réel comptés, les absents nommés")

def test_les_frais_de_transaction_se_lisent():
    """Les frais on-chain : trois horizons tirés de la table de la source,
    et un badge qui ne promet rien sans eux."""
    import pandas as pd

    from btcterm import sources
    from terminal.panels import onchain

    table = {"1": 1.003, "2": 1.003, "6": 0.351, "25": 0.123, "144": 0.112,
             "1008": 0.1}
    frais = sources.fee_estimates(table)
    assert frais == {"next": 1.003, "hour": 0.351, "day": 0.112}
    # Une table sans l'horizon exact rend le plus proche.
    assert sources.fee_estimates({"2": 5.0, "8": 3.0, "100": 1.0}) == \
        {"next": 5.0, "hour": 3.0, "day": 1.0}
    try:
        sources.fee_estimates({})
    except RuntimeError:
        pass
    else:
        raise AssertionError("une table vide doit lever")

    stats = {"hash_rate_ghs": 9.59e11, "difficulty": 1.484e14,
             "minutes_between_blocks": 10.6}
    mempool = pd.DataFrame({"value": [29e6]})
    sans = str(onchain._badges(stats, mempool))
    assert "frais" not in sans and "959 EH/s" in sans
    calme = str(onchain._badges(stats, mempool, frais))
    assert "1.0 sat/vB" in calme and "0.35 sat/vB dans l'heure" in calme
    ruee = onchain._badges(stats, mempool, {"next": 140.0, "hour": 90.0,
                                            "day": 12.0})
    assert "140 sat/vB" in str(ruee) and onchain.C["red"] in str(ruee)
    assert [onchain._frais(v) for v in (0.112, 1.003, 9.96, 23.4)] == \
        ["0.11", "1.0", "10.0", "23"]
    print("  ✓ frais on-chain : trois horizons, badge coloré, absence tue")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nCâblage du terminal — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Tous les panneaux sont posés et branchés.\n")
