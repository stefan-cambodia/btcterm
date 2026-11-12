#!/usr/bin/env python3
"""
Le marché des options, sans réseau : ce que `btcterm.options` calcule des
lignes brutes de Deribit, et ce que le panneau en montre.

Vérifié ici :

- les noms d'instruments se lisent — options, futures, strikes décimaux —,
  et le perpétuel n'a pas d'échéance ;
- le delta de Black-76 a ses bornes et sa symétrie ;
- le sourire d'une échéance se lit au bon endroit : volatilité au forward,
  ailes à 25 delta, sur un sourire fabriqué dont on connaît la réponse ;
- le max pain est celui qu'on calcule à la main ;
- la base des futures s'annualise, sans le perpétuel ni les échéances de
  quelques heures ;
- l'échéance par défaut est la plus proche qui pèse, pas celle du jour ;
- le panneau dit l'absence de Deribit, et ses figures tiennent à vide ;
- un collecteur qui n'a rien obtenu lève, au lieu d'un vide qu'on mettrait
  en cache.

Lancement :
    python tests/test_options.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm import options as opt, sources  # noqa: E402

NOW = pd.Timestamp("2026-11-06 06:00", tz="UTC")


def ligne(nom, iv=40.0, oi=100.0, forward=75_000.0, index=74_900.0,
          volume=1_000.0):
    return {"instrument_name": nom, "mark_iv": iv, "open_interest": oi,
            "underlying_price": forward, "estimated_delivery_price": index,
            "volume_usd": volume}


def test_les_noms_se_lisent():
    echeance, strike, sorte = opt.parse_instrument("BTC-27NOV26-68000-P")
    assert echeance == pd.Timestamp("2026-11-27 08:00", tz="UTC")
    assert strike == 68_000.0 and sorte == "P"
    assert opt.parse_instrument("BTC-6NOV26-75500-C")[0].day == 6
    assert opt.parse_instrument("BTC-27NOV26-0d5-C")[1] == 0.5
    assert opt.parse_instrument("BTC-25DEC26") == (
        pd.Timestamp("2026-12-25 08:00", tz="UTC"), None, "F")
    for nom in ("BTC-PERPETUAL", "BTC-27XXX26-1000-C", "", None):
        assert opt.parse_instrument(nom) is None, nom
    print("  ✓ options, futures et strikes décimaux ; le perpétuel écarté")


def test_le_delta_a_ses_bornes():
    an = 1.0
    atm = opt.call_delta(75_000, 75_000, an, 0.4)
    # Au forward, N(σ√T/2) : un peu plus d'un demi.
    assert math.isclose(atm, 0.5 * (1 + math.erf(0.2 / math.sqrt(2))), rel_tol=1e-12)
    assert opt.call_delta(75_000, 10_000, an, 0.4) > 0.999
    assert opt.call_delta(75_000, 500_000, an, 0.4) < 0.001
    # Échue : la valeur intrinsèque seule.
    assert opt.call_delta(75_000, 70_000, 0, 0.4) == 1.0
    assert opt.call_delta(75_000, 80_000, -0.1, 0.4) == 0.0
    print("  ✓ delta de Black-76 : demi au forward, bornes, échéance passée")


def _sourire(echeance="13NOV26", forward=75_000.0):
    """Un sourire connu : volatilité = 40 % − 20 % × log(K/F), puts plus chers."""
    lignes = []
    for strike in range(55_000, 100_001, 1_000):
        iv = 0.40 - 0.20 * math.log(strike / forward)
        for sorte in ("C", "P"):
            lignes.append(ligne(f"BTC-{echeance}-{strike}-{sorte}", iv=iv * 100,
                                forward=forward, oi=10.0))
    return lignes


def test_le_sourire_se_lit_au_bon_endroit():
    chaine = opt.option_chain(_sourire())
    courbe = opt.term_structure(chaine, NOW)
    assert len(courbe) == 1
    rang = courbe.iloc[0]
    # Au forward, la formule donne 40 % ; l'interpolation entre deux
    # strikes encadrants en est à une fraction de point.
    assert abs(rang["atm"] - 0.40) < 1e-3, rang["atm"]
    # Ailes : le put 25 delta (strike bas) plus cher que le call 25 delta.
    assert rang["put25"] > rang["atm"] > rang["call25"]
    assert rang["rr25"] < 0
    # Vérification indépendante de l'aile du call : le strike où le delta
    # du call vaut 0,25 avec sa propre volatilité.
    annees = (pd.Timestamp("2026-11-13 08:00", tz="UTC") - NOW).total_seconds() / (365 * 86_400)
    bas, haut = 75_000.0, 120_000.0
    for _ in range(60):
        k = (bas + haut) / 2
        iv = 0.40 - 0.20 * math.log(k / 75_000)
        if opt.call_delta(75_000, k, annees, iv) > 0.25:
            bas = k
        else:
            haut = k
    attendu = 0.40 - 0.20 * math.log(k / 75_000)
    assert abs(rang["call25"] - attendu) < 2e-3, (rang["call25"], attendu)
    assert rang["oi_calls"] == rang["oi_puts"] == 460.0
    print("  ✓ sourire : volatilité au forward, ailes à 25 delta, skew négatif")


def test_les_echeances_trop_proches_sortent_des_courbes():
    chaine = opt.option_chain(_sourire("6NOV26") + _sourire("13NOV26"))
    assert chaine["expiry"].nunique() == 2
    courbe = opt.term_structure(chaine, NOW)
    assert list(courbe["expiry"].dt.day) == [13], "l'échéance du jour est restée"
    print("  ✓ l'échéance de quelques heures reste hors des courbes")


def test_le_max_pain_est_celui_qu_on_calcule():
    table = pd.DataFrame({"strike": [70_000.0, 75_000.0, 80_000.0],
                          "call_oi": [0.0, 10.0, 100.0],
                          "put_oi": [100.0, 10.0, 0.0]})
    # À 70 000 : calls 0 ; puts 10×5 000 = 50 000.
    # À 75 000 : calls 0 ; puts 0 … sauf 100×0 → 0 ; total 0.
    # À 80 000 : calls 10×5 000 = 50 000 ; puts 0.
    assert opt.max_pain(table) == 75_000.0
    assert opt.max_pain(table.iloc[0:0]) is None
    vide = table.assign(call_oi=0.0, put_oi=0.0)
    assert opt.max_pain(vide) is None
    print("  ✓ max pain calculé à la main ; rien sans open interest")


def test_la_table_des_strikes():
    chaine = opt.option_chain([
        ligne("BTC-13NOV26-70000-P", oi=5), ligne("BTC-13NOV26-80000-C", oi=7),
        ligne("BTC-27NOV26-80000-C", oi=3), ligne("BTC-27NOV26-80000-P", oi=2),
    ])
    une = opt.strike_table(chaine, pd.Timestamp("2026-11-13 08:00", tz="UTC"))
    assert une.to_dict("list") == {"strike": [70_000.0, 80_000.0],
                                   "call_oi": [0.0, 7.0], "put_oi": [5.0, 0.0]}
    toutes = opt.strike_table(chaine)
    assert toutes.set_index("strike").loc[80_000.0].to_dict() == {
        "call_oi": 10.0, "put_oi": 2.0}
    assert opt.put_call_ratio(chaine) == 7 / 10
    print("  ✓ open interest par strike, une échéance ou toutes")


def test_la_base_des_futures():
    lignes = [
        {"instrument_name": "BTC-PERPETUAL", "mark_price": 75_100,
         "estimated_delivery_price": 75_000},
        {"instrument_name": "BTC-6NOV26", "mark_price": 75_010,
         "estimated_delivery_price": 75_000},
        {"instrument_name": "BTC-26MAR27", "mark_price": 77_000,
         "estimated_delivery_price": 75_000},
        {"instrument_name": "BTC-25DEC26", "mark_price": 75_600,
         "estimated_delivery_price": 75_000},
    ]
    courbe = opt.futures_curve(lignes, NOW)
    assert list(courbe["expiry"].dt.month) == [12, 3], courbe
    mars = courbe.iloc[1]
    assert math.isclose(mars["basis"], 2_000 / 75_000)
    assert math.isclose(mars["annualized"], mars["basis"] * 365 / mars["days"])
    print("  ✓ base annualisée, sans le perpétuel ni l'échéance du jour")


def test_l_echeance_par_defaut_est_celle_qui_pese():
    chaine = opt.option_chain(
        [ligne("BTC-6NOV26-75000-C", oi=900)]      # le jour même
        + [ligne("BTC-9NOV26-75000-C", oi=10)]     # quotidienne, minuscule
        + [ligne("BTC-13NOV26-75000-C", oi=200)]   # hebdomadaire
        + [ligne("BTC-25DEC26-75000-C", oi=800)])  # trimestrielle
    assert opt.front_expiry(chaine, NOW).day == 13
    assert opt.front_expiry(opt.option_chain([]), NOW) is None
    print("  ✓ la plus proche qui pèse, jamais celle du jour")


def test_le_resume():
    chaine = opt.option_chain(_sourire("13NOV26") + _sourire("25DEC26"))
    futures = opt.futures_curve([
        {"instrument_name": "BTC-25DEC26", "mark_price": 75_600,
         "estimated_delivery_price": 75_000},
        {"instrument_name": "BTC-29JAN27", "mark_price": 76_300,
         "estimated_delivery_price": 75_000},
        {"instrument_name": "BTC-26MAR27", "mark_price": 77_000,
         "estimated_delivery_price": 75_000}], NOW)
    resume = opt.summary(chaine, futures, NOW)
    assert resume["expiry30"].month == 12, "l'échéance à un mois est celle de décembre"
    assert abs(resume["atm30"] - 0.40) < 1e-3
    assert resume["rr25_30"] < 0
    assert resume["front"].day == 13 and resume["max_pain"] is not None
    # 84 jours contre 49 et 140 : janvier est l'échéance à trois mois.
    assert resume["basis90"][0].month == 1, resume["basis90"]
    vide = opt.summary(opt.option_chain([]), opt.futures_curve([]), NOW)
    assert vide["atm30"] is None and vide["pc_oi"] is None and vide["oi_btc"] == 0
    # La chaîne d'options en panne n'efface pas la base des futures, qui
    # n'en dépend pas.
    sans_chaine = opt.summary(opt.option_chain([]), futures, NOW)
    assert sans_chaine["basis90"] == resume["basis90"]
    print("  ✓ résumé : un mois, trois mois, max pain ; vide sans Deribit")


def test_le_panneau_tient_sans_deribit():
    from terminal import charts
    from terminal.panels import options as panneau

    vide = opt.option_chain([])
    assert panneau.expiry_choices(vide, NOW) == []
    resume = opt.summary(vide, opt.futures_curve([]), NOW)
    texte = panneau.badges(resume, None, None, verbose=True)
    assert "Deribit indisponible" in str(texte)
    for figure in (charts.build_vol_chart(pd.DataFrame(columns=["time", "close"]),
                                          pd.DataFrame(columns=["time", "value"])),
                   charts.build_term_chart(opt.term_structure(vide, NOW),
                                           opt.futures_curve([])),
                   charts.build_strikes_chart(opt.strike_table(vide), None, None)):
        assert figure.layout.annotations, "une figure vide doit le dire"

    chaine = opt.option_chain(_sourire("13NOV26") + _sourire("9NOV26"))
    choix = panneau.expiry_choices(chaine, NOW)
    assert [c["value"] for c in choix] == ["2026-11-09", "2026-11-13"]
    print("  ✓ panneau : absence dite, figures vides annotées, échéances au choix")


def test_forward_et_max_pain_ne_se_chevauchent_pas():
    """Deux verticales proches : centrées, leurs étiquettes se lisaient
    « forward 73,789max pain 76,000 » dans la grille. Chacune s'écarte du
    côté opposé à l'autre ; seule, elle reste centrée."""
    from terminal import charts
    from terminal.panels import options as panneau

    table = pd.DataFrame({"strike": [70_000.0, 74_000.0, 76_000.0, 80_000.0],
                          "call_oi": [1.0, 2.0, 3.0, 4.0],
                          "put_oi": [4.0, 3.0, 2.0, 1.0]})

    def cotes(forward, pain):
        figure = charts.build_strikes_chart(table, forward, pain)
        return {a.text.rsplit(" ", 1)[0]: a.xanchor
                for a in figure.layout.annotations}

    assert cotes(73_789, 76_000) == {"forward": "right", "max pain": "left"}
    assert cotes(76_000, 73_789) == {"forward": "left", "max pain": "right"}
    assert cotes(75_000, 75_000) == {"forward": "right", "max pain": "left"}
    assert cotes(75_000, None) == {"forward": "center"}

    # Le sélecteur d'échéances a quitté la barre de titre, qu'il faisait
    # déborder de 141 px dans la vue STRIKES : il est sous elle, dans le
    # corps du panneau.
    corps = panneau.layout().children
    assert [getattr(e, "id", None) for e in corps][1:3] == \
        ["options-expiry", "options-chart"]
    assert "options-expiry" not in str(corps[0])
    print("  ✓ étiquettes écartées l'une de l'autre ; échéances sous la barre")


def test_le_vide_vaut_panne():
    class Reponse:
        def __init__(self, payload):
            self.payload = payload
        def raise_for_status(self):
            pass
        def json(self):
            return self.payload

    original = sources._http.get
    try:
        for payload in ({"result": []}, {"error": {"code": 10}},
                        {"result": {"data": [], "continuation": None}}):
            sources._http.get = lambda *a, **k: Reponse(payload)
            for fetch in (lambda: sources.fetch_deribit_summary("option"),
                          lambda: sources.fetch_dvol(30)):
                try:
                    fetch()
                except Exception:
                    pass
                else:
                    raise AssertionError(f"un résultat vide doit lever : {payload}")
    finally:
        sources._http.get = original
    print("  ✓ une chaîne ou un DVOL vides lèvent")



def test_le_portage_dans_la_meme_unite():
    """Financement des perpétuels et base des futures, tout en %/an : ce que
    rapporte une position longue couverte, d'une plateforme à l'autre."""
    from terminal.panels.perp import carry

    futures = opt.futures_curve([{"instrument_name": "BTC-29JAN27",
                                  "mark_price": 76_300,
                                  "estimated_delivery_price": 75_000}])
    lignes = {"Binance": {"funding_rate": 0.0002},
              "Bybit": {"funding_rate": -0.00004},
              "OKX": {"funding_rate": None}}
    texte = " ".join(str(span.children) for span in carry(lignes, futures))
    assert "BIN" in texte and "+21.9 %" in texte, texte      # 0,02 % × 3 × 365
    assert "BYB" in texte and "-4.4 %" in texte, texte
    assert "OKX" not in texte, "une plateforme sans taux n'a pas de ligne"
    assert "future 29/01/27" in texte
    # Sans futures, la ligne s'arrête au dernier perpétuel ; sans rien, vide.
    seul = " ".join(str(s.children) for s in carry(lignes, opt.futures_curve([])))
    assert "future" not in seul and not seul.rstrip().endswith("·")
    assert carry({}, opt.futures_curve([])) == []
    print("  ✓ portage : financement annualisé et base, une plateforme muette absente")

def test_un_future_au_prix_de_l_indice_ne_cote_pas():
    """Au sortir d'une panne, Deribit marque ses futures au prix de
    l'indice : ce n'est pas une base nulle, c'est une absence de cotation."""
    import pandas as pd

    from btcterm import options as opt

    maintenant = pd.Timestamp("2026-11-10 09:43", tz="UTC")
    def future(nom, marque, indice=74_000.0):
        return {"instrument_name": nom, "mark_price": marque,
                "estimated_delivery_price": indice}

    saine = opt.futures_curve([future("BTC-29JAN27", 74_900.0),
                               future("BTC-26MAR27", 75_600.0)], now=maintenant)
    assert len(saine) == 2 and saine.attrs["pinned"] == 0
    assert (saine["annualized"] > 0.04).all()
    figee = opt.futures_curve([future("BTC-29JAN27", 74_000.0),
                               future("BTC-26MAR27", 74_000.0)], now=maintenant)
    assert figee.empty and figee.attrs["pinned"] == 2
    mixte = opt.futures_curve([future("BTC-29JAN27", 74_000.0),
                               future("BTC-26MAR27", 75_600.0)], now=maintenant)
    assert len(mixte) == 1 and mixte.attrs["pinned"] == 1
    assert opt.summary(opt.option_chain([]), figee)["basis90"] is None
    print("  ✓ futures figés à l'indice écartés, et comptés")


def test_l_historique_des_options():
    """Ce que seul le journal garde se trace : volatilité et skew dans la
    grille ; en plein écran, la base et le put/call en plus."""
    import pandas as pd

    from terminal.charts import build_options_history

    temps = pd.date_range("2026-11-10", periods=6, freq="5min")
    instants = pd.DataFrame({
        "time": temps, "dvol": [40.0, 40.2, None, 40.5, 40.4, 40.1],
        "atm30_iv": [38.0, 38.1, None, 38.4, 38.2, 38.0],
        "rr25": [-3.0, -3.1, None, -3.4, -3.3, -3.2],
        "basis90": [5.5, 5.6, None, 5.7, 5.6, 5.5],
        "pc_oi": [0.70, 0.71, None, 0.74, 0.73, 0.72],
        "gap": [False, False, True, False, False, False]})

    grille = build_options_history(instants)
    assert [t.name for t in grille.data] == ["DVOL", "à la monnaie, 1 mois",
                                              "skew 25Δ"]
    assert grille.layout.yaxis.domain == (0.36, 1.0)

    plein = build_options_history(instants, maximized=True)
    assert [t.name for t in plein.data][-2:] == ["base 3 mois", "put/call (OI)"]
    assert plein.layout.yaxis3.domain == (0.0, 0.24)
    assert plein.layout.yaxis.domain[0] > plein.layout.yaxis2.domain[1] \
        > plein.layout.yaxis3.domain[1], "trois étages qui ne se recouvrent pas"
    assert 5.3 < plein.layout.yaxis3.range[0] < 5.5 \
        and 5.7 < plein.layout.yaxis3.range[1] < 5.9, plein.layout.yaxis3.range
    # Une base lue à zéro, une fois : l'étage ne s'écrase pas dessus.
    longue = pd.DataFrame({
        "time": pd.date_range("2026-11-10", periods=200, freq="5min"),
        "dvol": 40.0, "atm30_iv": 38.0, "rr25": -3.0, "pc_oi": 0.7,
        "basis90": [0.0 if k == 100 else 5.5 + (k % 10) / 50 for k in range(200)],
        "gap": False})
    assert build_options_history(longue, maximized=True).layout.yaxis3.range[0] > 5
    base = plein.data[3]
    assert base.yaxis == "y3" and pd.isna(base.y[2]), "rompue sur l'interruption"

    # Un journal d'avant ces colonnes : deux étages, même en plein écran.
    ancien = build_options_history(instants.drop(columns=["basis90", "pc_oi"]),
                                   maximized=True)
    assert len(ancien.data) == 3 and ancien.layout.yaxis.domain == (0.36, 1.0)
    # Une seule des deux : elle seule s'ajoute.
    une = build_options_history(instants.assign(pc_oi=None), maximized=True)
    assert [t.name for t in une.data][-1] == "base 3 mois" and len(une.data) == 4
    # Moins de deux relevés : l'historique se dit en constitution.
    vide = build_options_history(instants.iloc[:1], maximized=True)
    assert not vide.data and "constitution" in vide.layout.annotations[0].text
    print("  ✓ historique : deux étages dans la grille, trois en plein écran")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nOptions — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Ce que les options disent se lit, et se tait sans source.\n")
