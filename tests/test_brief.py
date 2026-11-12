#!/usr/bin/env python3
"""
Vérifications de la synthèse des dernières 24 h (btcterm/brief.py).

Ce qui doit tenir :

- un écart « sur 24 h » se calcule contre l'instantané le plus proche de
  la veille, et dit sa durée réelle quand la machine dormait à ce moment ;
  sous `MIN_SPAN_H`, il n'est pas donné ;
- la couverture du journal se dit dès qu'il manque plus de dix minutes —
  trous entre instantanés, avant le premier, après le dernier ;
- une section qui échoue ne coûte que sa ligne ;
- la synthèse du matin part une fois par jour, après son heure, jamais
  sans sujet ntfy — et pas une seconde fois après un redémarrage ;
- le panneau la rend, couverture en tête.

Aucun réseau n'est touché : le hub est factice, le journal temporaire.

Lancement :
    python tests/test_brief.py
"""

import datetime as dt
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from btcterm import brief  # noqa: E402
from btcterm.journal import Journal  # noqa: E402
from btcterm.liquidations import Liquidation  # noqa: E402
from btcterm.macrocal import MacroEvent  # noqa: E402
from btcterm.tape import Trade  # noqa: E402

H = 3600.0
NOW = 1_794_000_000.0


def snapshot(ts, **fields):
    row = {name: None for name in ("oi_usd", "dvol", "rr25", "cb_premium",
                                   "btc_dominance", "funding_rate")}
    row.update(ts=ts, **fields)
    return row


def test_l_ecart_se_prend_sur_la_veille():
    rows = [snapshot(NOW - 24.2 * H, oi_usd=100.0),
            snapshot(NOW - 20 * H, oi_usd=105.0),
            snapshot(NOW - 0.1 * H, oi_usd=110.0)]
    current, reference, span = brief.change(rows, "oi_usd", NOW)
    assert (current, reference) == (110.0, 100.0)
    assert 23.9 < span < 24.2, span

    #: La machine dormait la veille au soir : l'écart vaut 15 h, et le dit.
    dormi = [snapshot(NOW - 15 * H, oi_usd=104.0),
             snapshot(NOW, oi_usd=110.0)]
    assert brief.change(dormi, "oi_usd", NOW) == (110.0, 104.0, 15.0)
    assert "sur 15 h" in brief.perp_item(None, brief.change(dormi, "oi_usd",
                                                            NOW)).text

    #: Sous MIN_SPAN_H, pas d'écart : il ne dirait pas la journée.
    court = [snapshot(NOW - 3 * H, oi_usd=104.0), snapshot(NOW, oi_usd=110.0)]
    assert brief.change(court, "oi_usd", NOW)[1] is None
    assert "%" not in brief.perp_item(None, brief.change(court, "oi_usd",
                                                         NOW)).text
    assert brief.change([snapshot(NOW)], "oi_usd", NOW) is None
    print("  ✓ référence la plus proche de la veille, durée réelle dite")


def test_la_couverture_se_dit():
    start, end = NOW - 24 * H, NOW
    pleine = [start + i * 300 for i in range(1, 288)]
    assert brief.coverage(pleine, start, end) is None

    #: Endormie de 0 h à 7 h du matin, et le service lancé à la 2e heure.
    stamps = [t for t in pleine if t > start + 2 * H
              and not (start + 10 * H < t < start + 17 * H)]
    texte = brief.coverage(stamps, start, end)
    assert texte.startswith("journal : 14 h 55"), texte
    assert "2 interruptions" in texte, texte
    assert "vide" in brief.coverage([], start, end)
    print("  ✓ heures couvertes et interruptions, bords compris")


def test_les_sections_lisent_leurs_donnees():
    cours = brief.price_item({"lastPrice": "76030", "priceChangePercent":
                              "-1.5", "highPrice": "77000", "lowPrice": "75000"})
    assert cours.tone == "down" and "76,030 $" in cours.text, cours
    assert brief.price_item({}).tone == "muted"

    # Le bilan vient du journal, calculé par la base ; le contrat de
    # Binance sur l'indice de dominance n'est pas du bitcoin.
    import tempfile
    from btcterm.journal import Journal
    from btcterm.liquidations import Liquidation
    with tempfile.TemporaryDirectory() as dossier:
        journal = Journal(Path(dossier) / "journal.db")
        for symbole, cote, prix in (("BTCUSDT", "long", 3e6),
                                    ("ETHUSDT", "long", 1e6),
                                    ("BTCDOMUSDT", "long", 2e5),
                                    ("BTCUSDT", "short", 5e5)):
            journal.record_liquidation(Liquidation(
                time=NOW, symbol=symbole, side=cote, price=prix,
                quantity=1.0, exchange="Binance"))
        bilan = journal.liquidation_summary(NOW - 1, NOW + 1)
        journal.close()
    ligne = brief.liquidations_item(bilan)
    assert ligne.text == "longs 4.2 M$ · shorts 500 k$ · dont BTC 3.5 M$", ligne
    assert "4 événements" in ligne.detail and "BTCUSDT long" in ligne.detail
    assert brief.liquidations_item(None).tone == "muted"
    assert ligne.tone == "down", "des longs liquidés : la journée a baissé"

    ordres = [{"ts": NOW, "exchange": "Coinbase", "side": "sell",
               "notional": 2e6},
              {"ts": NOW, "exchange": "Binance", "side": "buy",
               "notional": 5e5}]
    ligne = brief.trades_item(ordres)
    assert "solde -1.5 M$" in ligne.text and ligne.tone == "down", ligne
    assert "vente Coinbase 2.0 M$" in ligne.detail

    #: Le jour en cours de publication (NaN) ne compte pas comme un zéro.
    flux = pd.DataFrame({"Date": pd.to_datetime(["2026-11-05", "2026-11-06",
                                                 "2026-11-07"]),
                         "Total": [-430.2, 120.0, float("nan")]})
    ligne = brief.etf_item(flux)
    assert ligne.text == "+120 M$ le 06/11 · 5 séances -310 M$", ligne
    assert brief.etf_item(pd.DataFrame()).tone == "muted"
    # Avec les clôtures : les cinq séances en bitcoins, face à l'émission.
    clotures = pd.Series([100_000.0], index=pd.to_datetime(["2026-11-01"]))
    ligne = brief.etf_item(flux, clotures)
    assert ligne.text == ("+120 M$ le 06/11 · 5 séances -310 M$ "
                          "≈ -3,102 BTC, 3.4× l'émission"), ligne.text

    vol = brief.volatility_item((38.8, 39.5, 86_400), (-3.5,),
                                {"total": -112.4e6}, 75_536.0)
    assert vol.text.endswith("· GEX -112 M$/1 %, flip 75,536"), vol.text
    assert "GEX" not in brief.volatility_item((38.8, None, 0), None).text

    cycle_etat = {"cycle_day": 936, "cycle_multiple": 1.13, "drawdown": -41.0,
                  "days_since_ath": 402, "mayer": 1.0,
                  "halving": {"days_left": 517.0, "estimated": False}}
    ligne_cycle = brief.cycle_item(cycle_etat, {"mvrv": 1.37})
    assert "MVRV 1.37" in ligne_cycle.text and ligne_cycle.tone == ""
    assert brief.cycle_item(cycle_etat, {"mvrv": 0.9}).tone == "warn"
    assert "MVRV" not in brief.cycle_item(cycle_etat).text

    fg = brief.sentiment_item([{"value": 28, "label": "Fear"},
                               {"value": 25, "label": "Extreme Fear"}])
    assert fg.text == "Fear & Greed 25 (Extreme Fear), 28 la veille", fg

    etat = {"cycle_day": 935, "cycle_multiple": 1.13, "drawdown": -41.0,
            "days_since_ath": 401, "mayer": 1.0,
            "halving": {"days_left": 518.3, "estimated": False}}
    ligne = brief.cycle_item(etat)
    assert ligne.text == ("J+935 du halving (×1.13) · -41.0 % du sommet, "
                          "depuis 401 j · Mayer 1.00 · halving dans 518 j"), ligne
    assert ligne.tone == ""
    #: Aux bornes du multiple de Mayer, la ligne avertit ; au sommet, elle
    #: le dit ; une hauteur estimée porte son tilde ; une série trop courte
    #: pour la moyenne n'a pas de Mayer.
    haut = brief.cycle_item({**etat, "mayer": 2.5, "drawdown": 0.0,
                             "halving": {"days_left": 518.3,
                                         "estimated": True}})
    assert haut.tone == "warn" and "au sommet" in haut.text
    assert haut.text.endswith("halving dans ~518 j")
    assert brief.cycle_item({**etat, "mayer": 0.7}).tone == "warn"
    assert "Mayer" not in brief.cycle_item({**etat, "mayer": None}).text
    assert brief.cycle_item(None) is None
    print("  ✓ cours, liquidations, gros ordres, ETF, sentiment, cycle")


def test_l_agenda_annonce_la_prochaine_echeance():
    def event(kind, hours):
        moment = dt.datetime.fromtimestamp(NOW + hours * H, dt.timezone.utc)
        return MacroEvent(moment.astimezone(brief.macrocal.NEW_YORK).date(),
                          moment.astimezone(brief.macrocal.NEW_YORK).time(),
                          kind, f"{kind} de test")

    proches = brief.agenda_item(NOW, [event("FOMC", -2), event("CPI", 5),
                                      event("NFP", 30), event("PCE", 200)])
    assert proches.text.startswith("CPI ") and "NFP" in proches.text
    assert "PCE" not in proches.text and "FOMC" not in proches.text
    assert proches.tone == "warn", "une échéance dans la journée se signale"

    loin = brief.agenda_item(NOW, [event("PCE", 200)])
    assert loin.text.startswith("PCE ") and "dans 8 j" in loin.text, loin
    assert loin.tone == ""
    assert brief.agenda_item(NOW, [event("CPI", -3)]).tone == "warn"
    print("  ✓ les échéances des 48 h, la prochaine au moins")


class HubFactice:
    """Ce que `build` lit d'un hub — et un flux ETF en panne."""

    SNAPSHOT_GAP = 900

    def __init__(self, journal):
        self.journal = journal

    def ticker(self):
        return {"lastPrice": "76000", "priceChangePercent": "0.5",
                "highPrice": "76500", "lowPrice": "75000"}

    def perp_snapshot(self):
        return {}

    def etf_flows(self):
        raise RuntimeError("farside injoignable")

    def fear_greed_history(self):
        return [{"value": 40, "label": "Fear"}]

    def chain_stats(self):
        raise RuntimeError("blockchain.info injoignable")

    def cycle_closes(self):
        jours = pd.date_range("2024-04-01", periods=300, freq="D")
        return pd.Series([60_000.0 + 50 * i for i in range(300)], index=jours)


def journal_garni(path: Path, now: float) -> Journal:
    journal = Journal(path)
    for i in range(1, 288):
        ts = now - 24 * H + i * 300
        if now - 9 * H < ts < now - 2 * H:
            continue                     # sept heures de sommeil
        journal.record_market_snapshot(
            ts, oi_usd=7e9 * (1 + i / 2880), funding_rate=1e-4,
            dvol=40 + i / 100, rr25=-3.0, cb_premium=-0.1,
            btc_dominance=59.0)
    journal.record_liquidation(Liquidation(now - H, "BTCUSDT", "long",
                                           76000.0, 20.0, "OKX"))
    journal.record_trade(Trade(now - 2 * H, "Coinbase", "buy", 76000.0, 5.0))
    return journal


def test_la_synthese_se_construit_sans_une_section():
    with tempfile.TemporaryDirectory() as tmp:
        hub = HubFactice(journal_garni(Path(tmp) / "journal.db", NOW))
        synthese = brief.build(hub, NOW, news=lambda since, n: [
            {"title": "Un titre", "source": "Test", "score": 90,
             "sentiment": "bullish"}])

    lignes = {item.label: item for item in synthese.items}
    assert lignes["ETF"].text == "indisponible", "la panne ne coûte que sa ligne"
    assert lignes["Cours"].text.startswith("76,000 $")
    #: Le financement manque en direct : le journal donne le sien.
    assert "+0.0100 % /8 h" in lignes["Perpétuel"].text, lignes["Perpétuel"]
    assert "sur 24 h" in lignes["Perpétuel"].text
    assert lignes["Liquidations"].text.startswith("longs 1.5 M$")
    assert lignes["Gros ordres"].text.startswith("achats 380 k$")
    assert lignes["News"].tone == "up"
    #: La hauteur de la chaîne manque : le halving est estimé, et le dit.
    assert lignes["Cycle"].text.startswith("J+280 du halving"), lignes["Cycle"]
    assert "au sommet" in lignes["Cycle"].text
    assert "halving dans ~" in lignes["Cycle"].text
    assert synthese.coverage.startswith("journal : 17 h"), synthese.coverage

    texte = synthese.text()
    assert texte.splitlines()[1].startswith("⚠ journal")
    assert "ETF : indisponible" in texte and "Agenda : " in texte
    print(f"  ✓ {len(synthese.items)} lignes, la panne ETF isolée, "
          f"couverture dite")


def test_parse_time():
    assert brief.parse_time("07:00") == dt.time(7, 0)
    assert brief.parse_time(" 7:30 ") == dt.time(7, 30)
    assert brief.parse_time("7") == dt.time(7)
    for illisible in (None, "", "matin", "25:00", "7:75"):
        assert brief.parse_time(illisible) is None, illisible
    print("  ✓ heures lues, illisibles écartées")


class NotifierFactice:
    def __init__(self):
        self.envois = []
        self.parti = threading.Event()

    def send(self, alert):
        self.envois.append(alert)
        self.parti.set()
        return True

    def describe(self):
        return "ntfy.test"


def test_la_synthese_du_matin_part_une_fois():
    from btcterm.hub import MarketHub

    vrai = brief.build
    brief.build = lambda hub, now=None: brief.Brief(at=now, items=[
        brief.Item("Cours", "76,000 $")])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            hub = MarketHub(collect_news=False, keep_journal=False)
            hub._brief_sent = brief.SentLog(Path(tmp) / "brief.json")
            hub.brief_at = dt.time(7, 0)

            def a(jour, heure):
                return dt.datetime(2026, 11, jour, heure).timestamp()

            #: Sans sujet ntfy, rien ne part.
            assert not hub._maybe_brief(a(7, 8))
            notifier = NotifierFactice()
            hub.alerts.notifier = notifier

            assert not hub._maybe_brief(a(7, 6)), "avant l'heure"
            assert hub._maybe_brief(a(7, 8))
            assert notifier.parti.wait(2)
            assert notifier.envois[0].kind == "brief"
            assert "Cours : 76,000 $" in notifier.envois[0].message
            assert not hub._maybe_brief(a(7, 9)), "une fois par jour"

            #: Un service relancé le même jour ne la renvoie pas.
            relance = MarketHub(collect_news=False, keep_journal=False)
            relance._brief_sent = brief.SentLog(Path(tmp) / "brief.json")
            relance.brief_at = dt.time(7, 0)
            relance.alerts.notifier = notifier
            assert not relance._maybe_brief(a(7, 10))
            #: Le lendemain, elle repart — même au réveil, à 9 h.
            assert relance._maybe_brief(a(8, 9))
    finally:
        brief.build = vrai
    print("  ✓ après l'heure, une fois par jour, pas deux après un "
          "redémarrage, jamais sans ntfy")


def test_le_panneau_rend_la_synthese():
    from terminal.panels import brief as panneau

    with tempfile.TemporaryDirectory() as tmp:
        hub = HubFactice(journal_garni(Path(tmp) / "journal.db", NOW))
        hub.brief_at = dt.time(7, 0)

        class Alertes:
            notifier = None
        hub.alerts = Alertes()
        vue, badges = panneau.render(hub, NOW)

    texte = str(vue)
    assert "journal : 17 h" in texte, "la couverture en tête"
    assert texte.count("'Tr'") >= 8 or texte.count("Tr(") >= 8, texte[:300]
    titre = str(badges)
    assert "07:00" in titre and "pas configuré" in titre, titre
    print("  ✓ lignes, couverture en tête, heure d'envoi au titre")


def test_la_liquidite_contre_sa_moyenne():
    """La synthèse dit le carnet contre lui-même, et se tait sans une
    heure de relevés."""
    from btcterm import brief

    def lignes(n, dernier=1.0):
        out = []
        for k in range(n):
            f = dernier if k == n - 1 else 1.0
            out.append({"ts": 300.0 * k, "exchange": "Binance",
                        "bid_btc": 120.0 * f, "ask_btc": 80.0 * f})
            if k != 3:              # OKX manque un relevé : il est écarté
                out.append({"ts": 300.0 * k, "exchange": "OKX",
                            "bid_btc": 60.0 * f, "ask_btc": 40.0 * f})
            if k < 2:               # trop rare pour compter
                out.append({"ts": 300.0 * k, "exchange": "Coinbase",
                            "bid_btc": 1.0, "ask_btc": 1.0})
        return out

    assert brief.liquidity_item([]) is None
    assert brief.liquidity_item(lignes(brief.LIQUIDITY_MIN - 1)) is None
    plein = brief.liquidity_item(lignes(24))
    assert plein.label == "Liquidité" and plein.tone == ""
    assert plein.text == "300 BTC à ±0.1 % (+0 % contre la moyenne) · achats 60 %", \
        plein.text
    assert "Binance, OKX" in plein.detail and "sans Coinbase" in plein.detail
    creux = brief.liquidity_item(lignes(24, dernier=0.5))
    assert creux.tone == "warn" and "150 BTC" in creux.text
    assert "(-49 %" in creux.text, creux.text
    print("  ✓ liquidité : dernier relevé contre la moyenne, creux signalé")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nSynthèse — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("La synthèse dit la journée, et ce qu'elle en sait.\n")
