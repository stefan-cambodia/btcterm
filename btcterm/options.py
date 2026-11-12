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
    "MIN_DAYS", "call_delta", "gamma", "gamma_exposure", "gamma_profile",
    "gamma_flip", "chart_levels", "expiry_summary", "smile", "front_expiry", "futures_curve", "max_pain",
    "option_chain", "parse_instrument", "put_call_ratio", "strike_table",
    "summary", "term_structure", "expected_move", "VOL_RANK_LOW",
    "VOL_RANK_HIGH", "vol_rank",
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


def gamma(forward, strike, years, iv):
    """Gamma en Black-76, taux nul : φ(d1) / (F σ √T) — le même pour un
    call et un put. Vectorisé : scalaires ou tableaux numpy."""
    forward, strike, years, iv = (np.asarray(x, dtype=float)
                                  for x in (forward, strike, years, iv))
    root = iv * np.sqrt(years)
    d1 = (np.log(forward / strike) + 0.5 * iv * iv * years) / root
    return np.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi) / (forward * root)


#: Le GEX « naïf » suppose les teneurs de marché acheteurs des calls (les
#: clients en vendent, pour du rendement) et vendeurs des puts (les clients
#: en achètent, pour se couvrir) : +1 pour un call, −1 pour un put. C'est
#: une convention, pas une donnée — la chaîne ne dit pas qui tient quoi.
GEX_SIGNS = {"C": 1.0, "P": -1.0}

#: Une option à moins d'une heure de son échéance n'a plus qu'un gamma
#: concentré au strike, qui écraserait le reste : elle n'est pas comptée.
GEX_MIN_YEARS = 1.0 / (365.0 * 24.0)


def _gex_rows(chain: pd.DataFrame, now: pd.Timestamp):
    """Les options vivantes, en tableaux : strike, signe, OI, années,
    volatilité, forward — et le prix de l'indice."""
    rows = chain[(chain["oi"] > 0) & (chain["iv"] > 0)]
    years = ((rows["expiry"] - now).dt.total_seconds()
             / (365.0 * 86_400)).to_numpy()
    keep = years >= GEX_MIN_YEARS
    rows = rows[keep]
    if rows.empty:
        return None
    return {"strike": rows["strike"].to_numpy(dtype=float),
            "sign": rows["kind"].map(GEX_SIGNS).to_numpy(dtype=float),
            "oi": rows["oi"].to_numpy(dtype=float),
            "years": years[keep],
            "iv": rows["iv"].to_numpy(dtype=float),
            "forward": rows["forward"].to_numpy(dtype=float),
            "spot": float(rows["index"].median())}


def _gex(rows: dict, shift: float = 1.0):
    """Le GEX de chaque option, en dollars par 1 % de mouvement, pour un
    prix décalé de `shift` (les forwards suivent)."""
    spot = rows["spot"] * shift
    g = gamma(rows["forward"] * shift, rows["strike"], rows["years"], rows["iv"])
    return rows["sign"] * g * rows["oi"] * spot * spot * 0.01


def gamma_exposure(chain: pd.DataFrame, now: Optional[pd.Timestamp] = None,
                   span: float = 0.25) -> Optional[dict]:
    """L'exposition gamma des teneurs de marché, par strike.

    Pour chaque option, gamma × open interest (en BTC) × prix² × 1 % : les
    dollars de bitcoin que les teneurs achètent ou vendent pour rester
    couverts quand le prix bouge de 1 %. Positive (convention `GEX_SIGNS`),
    ils vendent la hausse et achètent la baisse — le marché se tasse ;
    négative, ils suivent le mouvement — il s'étire. Toutes échéances
    vivantes additionnées. Rend `None` sans option ; sinon `table`
    (strike, calls, puts, net — les strikes à `span` du prix), `total` (le
    GEX net au prix du jour) et `spot`.
    """
    if chain is None or chain.empty:
        return None
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    rows = _gex_rows(chain, now)
    if rows is None:
        return None
    gex = _gex(rows)
    frame = pd.DataFrame({"strike": rows["strike"], "gex": gex,
                          "kind": np.where(rows["sign"] > 0, "C", "P")})
    table = (frame.pivot_table(index="strike", columns="kind", values="gex",
                               aggfunc="sum", fill_value=0.0)
             .reindex(columns=["C", "P"], fill_value=0.0)
             .rename(columns={"C": "calls", "P": "puts"}).reset_index())
    table["net"] = table["calls"] + table["puts"]
    spot = rows["spot"]
    table = table[(table["strike"] >= spot * (1 - span))
                  & (table["strike"] <= spot * (1 + span))]
    return {"table": table.reset_index(drop=True), "total": float(gex.sum()),
            "spot": spot}


def gamma_profile(chain: pd.DataFrame, now: Optional[pd.Timestamp] = None,
                  span: float = 0.2, steps: int = 81) -> Optional[pd.DataFrame]:
    """Le GEX net total pour des prix hypothétiques autour du prix du jour :
    `price`, `gex`. C'est lui qui dit où les teneurs passent d'un régime à
    l'autre (`gamma_flip`)."""
    if chain is None or chain.empty:
        return None
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    rows = _gex_rows(chain, now)
    if rows is None:
        return None
    shifts = np.linspace(1 - span, 1 + span, steps)
    return pd.DataFrame({"price": rows["spot"] * shifts,
                         "gex": [float(_gex(rows, s).sum()) for s in shifts]})


def gamma_flip(profile: Optional[pd.DataFrame],
               spot: Optional[float] = None) -> Optional[float]:
    """Le prix où le GEX net change de signe — le plus proche du prix du
    jour s'il y en a plusieurs —, interpolé entre deux points du profil."""
    if profile is None or len(profile) < 2:
        return None
    prices = profile["price"].to_numpy()
    values = profile["gex"].to_numpy()
    crossings = []
    for i in range(len(values) - 1):
        a, b = values[i], values[i + 1]
        if a == 0:
            crossings.append(float(prices[i]))
        elif a * b < 0:
            crossings.append(float(prices[i] + (prices[i + 1] - prices[i])
                                   * a / (a - b)))
    if not crossings:
        return None
    if spot is None:
        spot = float(prices[len(prices) // 2])
    return min(crossings, key=lambda price: abs(price - spot))


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


def smile(chain: pd.DataFrame, expiry: Optional[pd.Timestamp]) -> pd.DataFrame:
    """Le sourire de volatilité d'une échéance : `strike`, `iv` (en %), la
    volatilité de l'option hors de la monnaie — le put sous le forward, le
    call au-dessus, celles qui s'échangent et dont la marque est la mieux
    tenue (la règle de `_smile`) —, et `side` (`P` ou `C`). Vide sans
    échéance ou sans cotation."""
    columns = ["strike", "iv", "side"]
    if expiry is None or chain is None or chain.empty:
        return pd.DataFrame(columns=columns)
    group = chain[chain["expiry"] == expiry]
    if group.empty:
        return pd.DataFrame(columns=columns)
    forward = float(group["forward"].median())
    otm = group[((group["kind"] == "P") & (group["strike"] < forward))
                | ((group["kind"] == "C") & (group["strike"] >= forward))]
    return (otm.assign(iv=otm["iv"] * 100, side=otm["kind"])[columns]
            .sort_values("strike").reset_index(drop=True))


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

    **Un future coté au prix de l'indice ne cote pas.** Le 10 novembre
    2026, à la première lecture après une panne de Deribit (503), tous
    les futures portaient un prix marqué égal à l'indice, au centime : une
    base de zéro s'est écrite au journal entre deux relevés à 5,7 %/an,
    avec un skew de −6 quand il valait −3,5 avant et après. Ces lignes
    sont écartées, et leur nombre rendu dans `attrs["pinned"]` : une
    courbe vide pour cette raison dit que la source n'a pas encore repris
    ses cotations (§ hub.record_market_snapshot).
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    records = []
    pinned = 0
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
        if float(mark) == float(index):
            pinned += 1
            continue
        basis = float(mark) / float(index) - 1.0
        records.append({"expiry": parsed[0], "days": days, "mark": float(mark),
                        "index": float(index), "basis": basis,
                        "annualized": basis * 365.0 / days})
    columns = ["expiry", "days", "mark", "index", "basis", "annualized"]
    curve = (pd.DataFrame.from_records(records, columns=columns)
             .sort_values("expiry").reset_index(drop=True))
    curve.attrs["pinned"] = pinned
    return curve


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


def expected_move(chain: Optional[pd.DataFrame],
                  now: Optional[pd.Timestamp] = None,
                  expiry: Optional[pd.Timestamp] = None) -> Optional[dict]:
    """Le mouvement que le prix des options suppose d'ici une échéance.

    La fourchette à un écart-type : forward × σ × √T, σ la volatilité à
    la monnaie de l'échéance (§ _smile), T le temps qui reste, en années.
    Le prix a, selon les options, environ deux chances sur trois d'y finir
    — une lecture du risque payé, pas une prévision : le marché la dépasse
    plus souvent qu'une loi normale ne le voudrait. L'échéance par défaut
    est celle du max pain (`front_expiry`). Rend `expiry`, `days`,
    `forward`, `atm`, `move` (en dollars), `pct`, `low`, `high` ; `None`
    sans chaîne, sans échéance, ou quand les strikes cotés n'encadrent
    pas la monnaie.
    """
    if chain is None or chain.empty:
        return None
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    expiry = front_expiry(chain, now) if expiry is None else expiry
    if expiry is None:
        return None
    group = chain[chain["expiry"] == expiry]
    years = _years(expiry, now)
    if group.empty or years <= 0:
        return None
    smile = _smile(group, now)
    atm, forward = smile["atm"], smile["forward"]
    if not (atm > 0 and forward > 0):
        return None
    move = float(forward * atm * np.sqrt(years))
    return {"expiry": expiry, "days": years * 365.0, "forward": float(forward),
            "atm": float(atm), "move": move, "pct": move / forward * 100,
            "low": forward - move, "high": forward + move}


#: Rang au-dessous duquel les options sont dites bon marché pour l'année, et
#: au-dessus duquel elles sont chères, en % — les bornes d'usage des
#: vendeurs et des acheteurs de volatilité.
VOL_RANK_LOW = 20.0
VOL_RANK_HIGH = 80.0

#: Bougies journalières exigées pour parler de rang : un mois.
VOL_RANK_MIN_DAYS = 30


def vol_rank(dvol: Optional[pd.DataFrame]) -> Optional[dict]:
    """Où se tient la volatilité implicite du jour dans son année.

    `dvol` : les bougies journalières du DVOL (§ sources.fetch_dvol, un an).
    Le **rang** situe la clôture du jour entre le plus bas et le plus haut
    de la période — 0 au plus bas, 100 au plus haut — ; le **centile** dit
    la part des jours passés plus bas. Les deux divergent quand l'année a
    connu un pic isolé : un DVOL de 40 après un pic à 80 a un rang bas,
    mais un centile qui peut rester moyen. Rend `value`, `rank`,
    `percentile`, `low`, `high`, `days` ; `None` sous un mois de bougies.
    """
    if dvol is None or dvol.empty or "close" not in dvol:
        return None
    closes = dvol["close"].astype(float).dropna()
    if len(closes) < VOL_RANK_MIN_DAYS:
        return None
    value = float(closes.iloc[-1])
    low, high = float(closes.min()), float(closes.max())
    history = closes.iloc[:-1]
    return {"value": value, "low": low, "high": high, "days": len(closes),
            "rank": (value - low) / (high - low) * 100 if high > low else 50.0,
            "percentile": float((history < value).mean() * 100)}


def chart_levels(chain: Optional[pd.DataFrame],
                 now: Optional[pd.Timestamp] = None) -> list[dict]:
    """Les niveaux que les options posent sur le graphique du prix, à côté
    des niveaux clés (§ analysis.key_levels, même forme) : le gamma flip
    (`γ flip`), le max pain de l'échéance par défaut (`max pain 27/11`) et
    les deux bornes du mouvement attendu d'ici là (`+1σ 27/11`, `−1σ
    27/11`, § expected_move), `span` « options ». Le cours qui s'en approche se voit sans changer
    d'onglet. Rien sans chaîne, et un niveau qu'on ne sait pas calculer
    manque plutôt que d'être faux.
    """
    if chain is None or chain.empty:
        return []
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    levels = []
    gex = gamma_exposure(chain, now)
    if gex is not None:
        flip = gamma_flip(gamma_profile(chain, now), gex["spot"])
        if flip is not None:
            levels.append({"label": "γ flip", "price": round(float(flip), 2),
                           "span": "options", "kind": "flip"})
    front = front_expiry(chain, now)
    if front is not None:
        pain = max_pain(strike_table(chain, front))
        if pain is not None:
            levels.append({"label": f"max pain {front:%d/%m}",
                           "price": float(pain), "span": "options",
                           "kind": "pain"})
        move = expected_move(chain, now, front)
        if move is not None:
            for sign, bound in (("+", "high"), ("−", "low")):
                levels.append({"label": f"{sign}1σ {front:%d/%m}",
                               "price": round(float(move[bound]), 2),
                               "span": "options", "kind": "move"})
    return levels


#: Part de l'open interest total au-delà de laquelle une échéance est un
#: événement : le 12 novembre 2026, l'hebdomadaire du vendredi (9,5 %), la
#: mensuelle (23 %) et les trimestrielles (41, 14 et 5 %) ; les quotidiennes
#: pèsent quelques centaines de bitcoins, moins d'un pour cent.
EXPIRY_EVENT_SHARE = 0.05


def expiry_summary(chain: Optional[pd.DataFrame],
                   now: Optional[pd.Timestamp] = None,
                   share: float = EXPIRY_EVENT_SHARE) -> list[dict]:
    """Les échéances qui pèsent, à venir : `expiry`, `oi` (BTC),
    `notional` (dollars, à l'indice), `share`, `pain` (le max pain) et
    `pc` (put/call en open interest). Pour le calendrier : des milliards de
    dollars d'options qui expirent à 8 h UTC sont un rendez-vous, et le max
    pain attire parfois le cours dans les heures qui précèdent."""
    if chain is None or chain.empty:
        return []
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    live = chain[chain["expiry"] > now]
    total = float(live["oi"].sum())
    if not total:
        return []
    spot = float(live["index"].median())
    events = []
    for expiry, group in live.groupby("expiry"):
        oi = float(group["oi"].sum())
        if oi / total < share:
            continue
        calls = float(group.loc[group["kind"] == "C", "oi"].sum())
        puts = float(group.loc[group["kind"] == "P", "oi"].sum())
        events.append({"expiry": expiry, "oi": oi, "notional": oi * spot,
                       "share": oi / total,
                       "pain": max_pain(strike_table(chain, expiry)),
                       "pc": puts / calls if calls else None})
    return events
