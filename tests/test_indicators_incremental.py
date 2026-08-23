#!/usr/bin/env python3
"""
Parité du dernier point : calcul borné contre recalcul complet.

Le canal push ne transmet que la dernière bougie et les derniers points
d'indicateurs, calculés sur les `TAIL_BARS` dernières lignes
(terminal/lwc.py) plutôt que sur la série entière. Ce test borne l'écart
entre ce calcul et le recalcul complet — dans l'esprit de
test_indicators_parity.py : le client ne doit pas voir un indicateur
sauter quand la valeur poussée remplace la valeur chargée.

Deux régimes de précision, par nature de fenêtre :

- fenêtres finies (MA, Bollinger, MA de volume, rang centile du CRSI) :
  250 lignes couvrent la plus longue (MA 200) — l'écart n'est que du
  bruit de sommation flottante ;
- lissages exponentiels (RSI, ATR, composantes du CRSI) : la mémoire est
  infinie en théorie, mais le poids du passé tronqué décroît en (1-α)^n
  — à 250 lignes il pèse ~1e-8 de la valeur.

Lancement :
    python tests/test_indicators_incremental.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btcterm.sources import generate_demo_ohlcv  # noqa: E402
from terminal.charts import prepare_price_frame  # noqa: E402
from terminal.lwc import TAIL_BARS, tail_points  # noqa: E402

#: Colonnes à fenêtre finie : l'égalité est attendue au bruit flottant
#: près. Et colonnes à lissage exponentiel : tolérance de troncature.
FENETRES = ["ma9", "ma26", "ma200", "bb_mid", "bb_upper", "bb_lower",
            "vol_ma20"]
LISSAGES = ["rsi", "crsi", "atr"]


def serie_de_travail(limit: int = 365):
    df = generate_demo_ohlcv(limit, interval="1h", index=False)
    df["time"] = df["time"].astype("datetime64[ns]")
    return df


def ecart_relatif(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-12)


def test_parite_du_dernier_point():
    df = serie_de_travail()
    complet = prepare_price_frame(df).iloc[-1]
    borne = prepare_price_frame(
        df.iloc[-TAIL_BARS:].reset_index(drop=True)).iloc[-1]

    for col in FENETRES:
        ecart = ecart_relatif(complet[col], borne[col])
        assert ecart < 1e-9, f"{col} : écart {ecart:.2e}"
    for col in LISSAGES:
        ecart = ecart_relatif(complet[col], borne[col])
        assert ecart < 1e-6, f"{col} : écart {ecart:.2e}"
    print(f"  ✓ fenêtres finies < 1e-9, lissages exponentiels < 1e-6 "
          f"(sur {TAIL_BARS} lignes)")


def test_le_paquet_pousse_dit_la_meme_chose():
    """Ce que `tail_points` met dans le paquet est bien le dernier point
    du recalcul complet, aux tolérances ci-dessus."""
    df = serie_de_travail()
    complet = prepare_price_frame(df).iloc[-1]
    paquet = tail_points(df, "1h")

    assert paquet["interval"] == "1h"
    attendu = int(df["time"].iloc[-1].value // 1_000_000_000)
    assert paquet["bar"]["time"] == attendu
    assert ecart_relatif(paquet["bar"]["close"], complet["close"]) < 1e-9

    for nom in ("ma9", "ma26", "ma200"):
        assert ecart_relatif(paquet["overlays"][nom]["value"],
                             complet[nom]) < 1e-9, nom
    assert ecart_relatif(paquet["panes"]["rsi"]["value"],
                         complet["rsi"]) < 1e-6
    assert ecart_relatif(paquet["volume_ma"]["value"],
                         complet["vol_ma20"]) < 1e-9
    assert paquet["demo"] is True, "la série de démo doit se déclarer"
    print("  ✓ bar, moyennes, RSI et MA de volume du paquet = recalcul complet")


def test_serie_courte_sans_naufrage():
    """Moins de lignes que la fenêtre : les indicateurs indéfinis sont
    simplement absents du paquet, jamais des NaN."""
    paquet = tail_points(serie_de_travail(60), "1h")
    assert "ma200" not in paquet["overlays"], "la MA 200 n'existe pas encore"
    assert "ma26" in paquet["overlays"]
    assert paquet["volume_ma"] is not None
    for zone in (paquet["overlays"], paquet["panes"]):
        for nom, point in zone.items():
            assert not math.isnan(point["value"]), nom
    print("  ✓ série courte : l'indéfini est absent, pas NaN")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"\nParité du calcul borné — {len(tests)} vérifications\n" + "─" * 60)
    for test in tests:
        print(f"\n{test.__name__}")
        test()
    print("\n" + "─" * 60)
    print("Le point poussé vaut le point recalculé.\n")
