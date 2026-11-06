"""
Le marché des options bitcoin : ce que les prix d'options disent du futur.

Le carnet, le perpétuel et les liquidations disent ce que le marché *fait* ;
les options disent ce qu'il *craint* et ce qu'il *paie pour s'en couvrir*.
Quatre lectures, toutes tirées de la même photographie de Deribit — la
plateforme qui tient l'essentiel de l'open interest des options bitcoin :

- la **volatilité implicite à la monnaie** de chaque échéance, et leur
  courbe : une courbe inversée (le court terme plus cher que le long) dit un
  marché sous tension, une courbe montante un marché calme ;
- le **skew 25 delta** (*risk reversal*) : l'écart de volatilité entre un
  call et un put également éloignés de la monnaie. Négatif, les puts sont
  plus chers — on paie pour se couvrir à la baisse ;
- l'**open interest par strike** d'une échéance, et son **max pain** — le
  prix où les acheteurs d'options perdraient le plus à l'échéance. C'est une
  description du positionnement, pas une prévision ;
- la **base des futures à échéance**, annualisée : ce que coûte de porter
  une position longue jusqu'à l'échéance, le pendant à terme du financement
  du perpétuel.

Tout est calculé ici, sans réseau, depuis les lignes brutes de
`get_book_summary_by_currency` : le delta de chaque option vient de
Black-76 (options sur le future de l'échéance, taux nul, la convention de
Deribit), la volatilité étant la `mark_iv` de la plateforme.
"""

from __future__ import annotations

import math
import re
from typing import Iterable, Optional

import numpy as np
import pandas as pd

__all__ = [
    "MIN_DAYS", "call_delta", "front_expiry", "futures_curve", "max_pain",
    "option_chain", "parse_instrument", "put_call_ratio", "strike_table",
    "summary", "term_structure",
]

#: Deribit fait expirer options et futures à 08:00 UTC.
EXPIRY_HOUR_UTC = 8

#: En deçà de deux jours d'échéance, le temps restant est si court qu'un
#: écart de quelques dollars fait un taux annualisé absurde, et que la
#: volatilité implicite ne mesure plus que le bruit de la journée : ces
#: échéances restent dans l'open interest, pas dans les courbes.
MIN_DAYS = 2.0

_MONTHS = {name: number for number, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}

_NAME = re.compile(r"^[A-Z]+-(\d{1,2})([A-Z]{3})(\d{2})(?:-(\d+(?:d\d+)?)-([CP]))?$")


def parse_instrument(name: str) -> Optional[tuple[pd.Timestamp, Optional[float], str]]:
    """`BTC-27NOV26-68000-P` → (échéance, strike, `"P"`).

    Un future à échéance rend `(échéance, None, "F")` ; le perpétuel, qui
    n'a pas d'échéance, et tout nom illisible rendent `None`.
    """
    match = _NAME.match(name or "")
    if not match:
        return None
    day, month, year, strike, kind = match.groups()
    if month not in _MONTHS:
        return None
    expiry = pd.Timestamp(year=2000 + int(year), month=_MONTHS[month],
                          day=int(day), hour=EXPIRY_HOUR_UTC, tz="UTC")
    if strike is None:
        return expiry, None, "F"
    return expiry, float(strike.replace("d", ".")), kind


def _years(expiry: pd.Timestamp, now: pd.Timestamp) -> float:
    return (expiry - now).total_seconds() / (365.0 * 86_400)


def call_delta(forward: float, strike: float, years: float, iv: float) -> float:
    """Delta d'un call en Black-76, taux nul : N(d1).

    Le delta d'un put est celui du call moins un. À échéance passée ou sans
    volatilité, l'option ne vaut plus que sa valeur intrinsèque.
    """
    if years <= 0 or iv <= 0:
        return 1.0 if forward > strike else 0.0
    d1 = (math.log(forward / strike) + 0.5 * iv * iv * years) / (iv * math.sqrt(years))
    return 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))


def option_chain(rows: Iterable[dict]) -> pd.DataFrame:
    """La chaîne d'options, une ligne par instrument coté.

    Colonnes : `expiry`, `strike`, `kind` (`C`/`P`), `iv` (fraction),
    `oi` (en BTC, un contrat valant un bitcoin), `volume_usd`, `forward`
    (prix du future de l'échéance) et `index`. Une option sans volatilité
    de marque — jamais cotée — est écartée.
    """
    records = []
    for row in rows:
        parsed = parse_instrument(row.get("instrument_name", ""))
        if parsed is None or parsed[2] == "F":
            continue
        iv = row.get("mark_iv")
        forward = row.get("underlying_price")
        if not iv or not forward:
            continue
        expiry, strike, kind = parsed
        records.append({
            "expiry": expiry, "strike": strike, "kind": kind,
            "iv": float(iv) / 100.0,
            "oi": float(row.get("open_interest") or 0.0),
            "volume_usd": float(row.get("volume_usd") or 0.0),
            "forward": float(forward),
            "index": float(row.get("estimated_delivery_price") or forward),
        })
    columns = ["expiry", "strike", "kind", "iv", "oi", "volume_usd",
               "forward", "index"]
    return (pd.DataFrame.from_records(records, columns=columns)
            .sort_values(["expiry", "strike", "kind"]).reset_index(drop=True))


def _smile(group: pd.DataFrame, now: pd.Timestamp) -> dict:
    """Volatilité à la monnaie et aux ailes 25 delta d'une échéance.

    Les options hors de la monnaie seules font le sourire — puts sous le
    forward, calls au-dessus : ce sont elles qui s'échangent, et leur
    volatilité est la mieux tenue. La volatilité à la monnaie s'interpole
    en log-strike au forward ; les ailes s'interpolent en delta du call —
    0,25 pour le call 25 delta, 0,75 pour le put 25 delta (delta de put
    −0,25). Une aile que les strikes cotés n'atteignent pas reste vide.
    """
    forward = float(group["forward"].median())
    years = _years(group["expiry"].iloc[0], now)
    otm = group[((group["kind"] == "P") & (group["strike"] < forward))
                | ((group["kind"] == "C") & (group["strike"] >= forward))]
    otm = otm.sort_values("strike")
    result = {"forward": forward, "atm": np.nan, "call25": np.nan,
              "put25": np.nan}
    if len(otm) < 2:
        return result

    log_k = np.log(otm["strike"].to_numpy() / forward)
    ivs = otm["iv"].to_numpy()
    if log_k[0] <= 0 <= log_k[-1]:
        result["atm"] = float(np.interp(0.0, log_k, ivs))

    deltas = np.array([call_delta(forward, k, years, v)
                       for k, v in zip(otm["strike"], otm["iv"])])
    # Le delta du call décroît quand le strike monte : `np.interp` veut des
    # abscisses croissantes.
    order = np.argsort(deltas)
    deltas, ivs = deltas[order], ivs[order]
    for key, target in (("call25", 0.25), ("put25", 0.75)):
        if deltas[0] <= target <= deltas[-1]:
            result[key] = float(np.interp(target, deltas, ivs))
    return result


def term_structure(chain: pd.DataFrame, now: Optional[pd.Timestamp] = None,
                   min_days: float = MIN_DAYS) -> pd.DataFrame:
    """La courbe des échéances : une ligne par échéance d'au moins `min_days`.

    Colonnes : `expiry`, `days`, `forward`, `atm`, `call25`, `put25` (en
    fraction), `rr25` (call 25 delta moins put 25 delta, en points de
    volatilité : négatif, les puts sont plus chers), `fly25` (moyenne des
    ailes moins la monnaie : la convexité du sourire), `oi_calls`,
    `oi_puts`.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    rows = []
    for expiry, group in chain.groupby("expiry"):
        days = (expiry - now).total_seconds() / 86_400
        if days < min_days:
            continue
        smile = _smile(group, now)
        rows.append({
            "expiry": expiry, "days": days, **smile,
            "rr25": (smile["call25"] - smile["put25"]) * 100,
            "fly25": ((smile["call25"] + smile["put25"]) / 2 - smile["atm"]) * 100,
            "oi_calls": float(group.loc[group["kind"] == "C", "oi"].sum()),
            "oi_puts": float(group.loc[group["kind"] == "P", "oi"].sum()),
        })
    columns = ["expiry", "days", "forward", "atm", "call25", "put25", "rr25",
               "fly25", "oi_calls", "oi_puts"]
    return pd.DataFrame.from_records(rows, columns=columns)


def strike_table(chain: pd.DataFrame,
                 expiry: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """Open interest des calls et des puts par strike, en BTC.

    Une échéance donnée, ou toutes réunies si `expiry` est `None`.
    """
    view = chain if expiry is None else chain[chain["expiry"] == expiry]
    table = (view.pivot_table(index="strike", columns="kind", values="oi",
                              aggfunc="sum", fill_value=0.0)
             .reindex(columns=["C", "P"], fill_value=0.0))
    table.columns = ["call_oi", "put_oi"]
    return table.reset_index()


def max_pain(table: pd.DataFrame) -> Optional[float]:
    """Le strike où la valeur des options à l'échéance serait la plus faible.

    Pour chaque strike candidat S : la somme, sur les calls, de
    OI × max(0, S − K), et sur les puts, de OI × max(0, K − S). C'est le prix
    où les acheteurs d'options perdraient le plus — une lecture du
    positionnement. L'idée que le prix y serait attiré n'a rien d'établi.
    """
    if table.empty or (table["call_oi"].sum() + table["put_oi"].sum()) <= 0:
        return None
    strikes = table["strike"].to_numpy()
    calls = table["call_oi"].to_numpy()
    puts = table["put_oi"].to_numpy()
    pain = [float((calls * np.maximum(0.0, s - strikes)).sum()
                  + (puts * np.maximum(0.0, strikes - s)).sum())
            for s in strikes]
    return float(strikes[int(np.argmin(pain))])


def put_call_ratio(chain: pd.DataFrame, column: str = "oi") -> Optional[float]:
    """Puts sur calls, en open interest (défaut) ou en volume (`volume_usd`)."""
    calls = chain.loc[chain["kind"] == "C", column].sum()
    puts = chain.loc[chain["kind"] == "P", column].sum()
    return float(puts / calls) if calls > 0 else None


def futures_curve(rows: Iterable[dict], now: Optional[pd.Timestamp] = None,
                  min_days: float = MIN_DAYS) -> pd.DataFrame:
    """La base des futures à échéance contre l'indice, une ligne par échéance.

    Colonnes : `expiry`, `days`, `mark`, `index`, `basis` (fraction) et
    `annualized` (fraction par an, base × 365 / jours). Le perpétuel n'a
    pas d'échéance : sa base, c'est le financement, que le panneau
    perpétuel montre déjà.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    records = []
    for row in rows:
        parsed = parse_instrument(row.get("instrument_name", ""))
        if parsed is None or parsed[2] != "F":
            continue
        mark = row.get("mark_price")
        index = row.get("estimated_delivery_price")
        if not mark or not index:
            continue
        days = (parsed[0] - now).total_seconds() / 86_400
        if days < min_days:
            continue
        basis = float(mark) / float(index) - 1.0
        records.append({"expiry": parsed[0], "days": days, "mark": float(mark),
                        "index": float(index), "basis": basis,
                        "annualized": basis * 365.0 / days})
    columns = ["expiry", "days", "mark", "index", "basis", "annualized"]
    return (pd.DataFrame.from_records(records, columns=columns)
            .sort_values("expiry").reset_index(drop=True))


def front_expiry(chain: pd.DataFrame, now: Optional[pd.Timestamp] = None,
                 share: float = 0.05) -> Optional[pd.Timestamp]:
    """L'échéance qu'on regarde par défaut : la plus proche qui compte.

    Deribit liste une échéance par jour sur la semaine qui vient, chacune
    d'un open interest minuscule : la plus proche d'au moins `share` de
    l'open interest total, à au moins une journée — celle du jour, qui
    expire dans quelques heures, n'a plus rien à dire de son positionnement.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    if chain.empty:
        return None
    by_expiry = chain.groupby("expiry")["oi"].sum()
    by_expiry = by_expiry[by_expiry.index > now + pd.Timedelta(days=1)]
    if by_expiry.empty:
        return None
    total = by_expiry.sum()
    for expiry, oi in by_expiry.items():
        if total and oi / total >= share:
            return expiry
    return by_expiry.index[0]


def summary(chain: pd.DataFrame, futures: pd.DataFrame,
            now: Optional[pd.Timestamp] = None) -> dict:
    """Les chiffres du bandeau du panneau, en une passe.

    `atm` et `rr25` sont ceux de l'échéance la plus proche d'au moins
    trente jours — la référence des volatilités « à un mois » ; `front`
    est l'échéance par défaut de la vue des strikes, avec son max pain ;
    `basis90` la base annualisée de l'échéance la plus proche de trois mois,
    la référence d'usage du portage.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    out = {"atm30": None, "rr25_30": None, "expiry30": None,
           "pc_oi": put_call_ratio(chain) if not chain.empty else None,
           "pc_volume": (put_call_ratio(chain, "volume_usd")
                         if not chain.empty else None),
           "oi_btc": float(chain["oi"].sum()) if not chain.empty else 0.0,
           "front": None, "max_pain": None, "basis90": None}
    # La base des futures ne dépend pas de la chaîne d'options : une panne
    # de celle-ci ne doit pas l'effacer.
    if not futures.empty:
        row = futures.loc[(futures["days"] - 90).abs().idxmin()]
        out["basis90"] = (row["expiry"], float(row["annualized"]))
    if chain.empty:
        return out
    curve = term_structure(chain, now)
    month = curve[curve["days"] >= 30].dropna(subset=["atm"])
    if not month.empty:
        row = month.iloc[0]
        out.update(atm30=float(row["atm"]), expiry30=row["expiry"],
                   rr25_30=None if pd.isna(row["rr25"]) else float(row["rr25"]))
    front = front_expiry(chain, now)
    if front is not None:
        out["front"] = front
        out["max_pain"] = max_pain(strike_table(chain, front))
    return out
