"""
Le cycle du bitcoin : où en est-on depuis le halving, et depuis le sommet.

Le bitcoin a une horloge que les autres marchés n'ont pas : tous les
210 000 blocs — quatre ans à peu près — l'émission est divisée par deux, et
les trois cycles passés se sont lus contre cette date. Ce module ne prédit
rien ; il situe. Tout y vient des clôtures journalières que le disque tient
déjà depuis août 2017 (§ history), sans source de plus :

- le **halving** : blocs restants et date estimée du prochain, au rythme
  nominal de dix minutes ; la hauteur vient de l'instantané du réseau, ou,
  s'il manque, s'estime depuis le dernier halving ;
- le **repli** depuis le plus haut historique, et depuis quand ;
- le **multiple de Mayer** — le cours rapporté à sa moyenne de 200 jours —,
  avec la part des jours passés où il était plus bas ;
- la **moyenne de 200 semaines**, plancher des trois derniers marchés
  baissiers, et le rapport du **Pi cycle** (moyenne de 111 jours sur deux
  fois celle de 350 : 1 aux sommets de 2017 et 2021) ;
- les **cycles superposés** : le cours en multiple de celui du jour du
  halving, jour pour jour.

L'historique commence le 17 août 2017 : le cycle de 2016 n'y entre qu'à son
quatre-centième jour, et son cours de départ est une référence écrite ici.
Le sommet d'avant cette date (3 000 $ en juin 2017) était déjà dépassé ce
jour-là : le plus haut couru sur la série est bien le plus haut historique.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

__all__ = ["Halving", "HALVINGS", "HALVING_INTERVAL", "BLOCK_MINUTES",
           "MAYER_LOW", "MAYER_HIGH", "WEEKS_200",
           "closes", "next_halving", "mayer", "drawdown", "cycle_paths",
           "metrics", "BLOCKS_PER_DAY", "block_subsidy", "daily_issuance",
           "etf_supply", "btc_amount", "supply_text", "RETARGET_INTERVAL",
           "difficulty_estimate", "hash_ribbons", "MVRV_LOW", "MVRV_HIGH",
           "mvrv_state", "PUELL_LOW", "PUELL_HIGH", "PUELL_WINDOW", "puell",
           "puell_state", "HASHPRICE_WINDOW", "hashprice", "hashprice_state"]

#: Blocs entre deux halvings, et rythme visé par le protocole.
HALVING_INTERVAL = 210_000
BLOCK_MINUTES = 10.0

#: Bornes d'usage du multiple de Mayer : sous 0,8 le cours est loin sous sa
#: moyenne, au-dessus de 2,4 il s'en est détaché comme aux sommets.
MAYER_LOW = 0.8
MAYER_HIGH = 2.4

#: Deux cents semaines, en jours.
WEEKS_200 = 1400


@dataclass(frozen=True)
class Halving:
    """Un halving passé : son bloc, son jour (UTC), le cours de ce jour-là."""
    height: int
    date: datetime
    #: Cours de référence, en dollars, pour les cycles que la série ne
    #: couvre pas depuis leur premier jour ; `None` quand elle le donne.
    price: Optional[float] = None

    @property
    def label(self) -> str:
        return str(self.date.year)


HALVINGS = (
    Halving(210_000, datetime(2012, 11, 28), 12.3),
    Halving(420_000, datetime(2016, 7, 9), 650.0),
    Halving(630_000, datetime(2020, 5, 11)),
    Halving(840_000, datetime(2024, 4, 20)),
)


#: Blocs par jour au rythme visé, dix minutes : l'émission journalière se
#: compte là-dessus. Le rythme réel s'en écarte de quelques pour cent selon
#: l'ajustement de difficulté — moins que l'incertitude des flux convertis.
BLOCKS_PER_DAY = 24 * 60 // int(BLOCK_MINUTES)


def block_subsidy(day: datetime) -> float:
    """La récompense de bloc en vigueur ce jour-là (UTC), en BTC : cinquante
    au lancement, divisée par deux à chaque halving passé."""
    day = pd.Timestamp(day)
    if day.tzinfo is not None:
        day = day.tz_convert(None)
    passed = sum(1 for halving in HALVINGS if halving.date <= day)
    return 50.0 / 2 ** passed


def daily_issuance(day: datetime) -> float:
    """Les bitcoins neufs d'une journée : 450 depuis avril 2024."""
    return block_subsidy(day) * BLOCKS_PER_DAY


def etf_supply(frame: Optional[pd.DataFrame], series: pd.Series,
               sessions: Optional[int] = None) -> Optional[dict]:
    """Les flux nets des ETF d'une fenêtre, en bitcoins, face à l'émission.

    Les flux sont en millions de dollars ; chaque jour se convertit au
    cours de clôture de ce jour-là (`series`, celle de `closes`) — à
    défaut, la dernière clôture connue avant lui. C'est une estimation :
    les émetteurs achètent au fil de la séance, et la clôture UTC tombe
    quatre heures après celle de New York. L'émission, elle, court tous
    les jours du calendrier, week-ends compris, du premier jour de la
    fenêtre au dernier : comparer les deux dit combien de jours de
    production des mineurs les ETF ont absorbés — ou rendus.

    `sessions` borne la fenêtre aux dernières séances, comme `etf_stats`.
    Rend `None` sans flux ou sans cours ; sinon `btc` (net, signé),
    `issuance` (BTC émis sur la période), `ratio` (le premier rapporté au
    second), `calendar_days`, `sessions` et `priced` (séances converties).
    """
    if frame is None or frame.empty or series is None or series.empty:
        return None
    total = "Total" if "Total" in frame.columns else frame.columns[-1]
    view = frame if sessions is None else frame.tail(sessions)
    dates = pd.to_datetime(view["Date"]).dt.normalize()
    prices = series.reindex(series.index.union(dates)).ffill().reindex(dates)
    flows = pd.to_numeric(view[total], errors="coerce").to_numpy()
    priced = [(flow, price) for flow, price in zip(flows, prices.to_numpy())
              if price == price and price > 0 and flow == flow]
    if not priced:
        return None
    btc = float(sum(flow * 1e6 / price for flow, price in priced))
    first, last = dates.iloc[0], dates.iloc[-1]
    calendar = pd.date_range(first, last, freq="D")
    issuance = float(sum(daily_issuance(day) for day in calendar))
    return {"btc": btc, "issuance": issuance,
            "ratio": btc / issuance if issuance else None,
            "calendar_days": len(calendar), "sessions": len(view),
            "priced": len(priced)}


def btc_amount(value: float) -> str:
    """« -15.4 k BTC », « +820 BTC » : la quantité, signée, à l'échelle."""
    if abs(value) >= 10_000:
        return f"{value / 1000:+z,.1f} k BTC"
    return f"{value:+z,.0f} BTC"


def supply_text(supply: Optional[dict]) -> str:
    """« ≈ -15.4 k BTC, 4.9× l'émission » — vide sans conversion possible.

    Une conversion partielle (des séances sans cours) le dit : le chiffre
    ne vaut alors que pour les séances converties.
    """
    if not supply or supply.get("ratio") is None:
        return ""
    text = (f"≈ {btc_amount(supply['btc'])}, "
            f"{abs(supply['ratio']):.1f}× l'émission")
    if supply["priced"] < supply["sessions"]:
        text += f" ({supply['priced']}/{supply['sessions']} séances)"
    return text


def closes(frame: Optional[pd.DataFrame]) -> pd.Series:
    """Les clôtures journalières, une par jour, indexées par leur date."""
    if frame is None or frame.empty:
        return pd.Series(dtype=float)
    series = pd.Series(frame["close"].to_numpy(dtype=float),
                       index=pd.to_datetime(frame["time"]).dt.normalize())
    return series[~series.index.duplicated(keep="last")].sort_index().dropna()


def _naive_utc(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if now.tzinfo is not None:
        return now.astimezone(timezone.utc).replace(tzinfo=None)
    return now


def next_halving(height: Optional[int] = None,
                 now: Optional[datetime] = None) -> dict:
    """Le prochain halving : son bloc, ce qu'il reste, et quand.

    Sans hauteur connue, elle s'estime depuis le dernier halving, à dix
    minutes le bloc — `estimated` le dit. La date se compte au rythme
    nominal : le rythme observé d'un jour ne dit rien des dix-huit mois à
    venir, que l'ajustement de difficulté ramène à dix minutes.
    """
    now = _naive_utc(now)
    last = HALVINGS[-1]
    estimated = not height or height < last.height
    if estimated:
        elapsed = (now - last.date).total_seconds() / 60 / BLOCK_MINUTES
        height = last.height + max(int(elapsed), 0)
    done = height // HALVING_INTERVAL
    target = (done + 1) * HALVING_INTERVAL
    left = target - height
    eta = now + timedelta(minutes=left * BLOCK_MINUTES)
    # Si la hauteur a déjà franchi un halving que la table ignore, le
    # cycle en cours a commencé là — sa date s'estime de la même façon.
    since = last.date if done * HALVING_INTERVAL == last.height else (
        now - timedelta(minutes=(height - done * HALVING_INTERVAL)
                        * BLOCK_MINUTES))
    return {
        "height": int(height), "target": int(target), "blocks_left": int(left),
        "eta": eta, "days_left": left * BLOCK_MINUTES / 1440,
        "days_since": (now - since).total_seconds() / 86_400,
        "progress": 1 - left / HALVING_INTERVAL,
        "estimated": bool(estimated),
    }


def mayer(series: pd.Series) -> pd.Series:
    """Le cours rapporté à sa moyenne de 200 jours, là où elle existe."""
    return (series / series.rolling(200).mean()).dropna()


def drawdown(series: pd.Series) -> pd.Series:
    """Le repli depuis le plus haut couru, en % (zéro au sommet)."""
    return (series / series.cummax() - 1) * 100


def cycle_paths(series: pd.Series) -> list[dict]:
    """Chaque cycle que la série touche, en multiple du cours du halving.

    Rend, du plus ancien au plus récent, `label`, `days` (jours écoulés
    depuis le halving), `multiple` et `current`. Le cours de départ est
    celui de la série ce jour-là, ou la référence du halving si la série
    commence après.
    """
    paths = []
    if series.empty:
        return paths
    for position, halving in enumerate(HALVINGS):
        start = pd.Timestamp(halving.date)
        last = position == len(HALVINGS) - 1
        end = (series.index[-1] + pd.Timedelta(days=1) if last
               else pd.Timestamp(HALVINGS[position + 1].date))
        leg = series[(series.index >= start) & (series.index < end)]
        if leg.empty:
            continue
        base = (float(series[start]) if start in series.index
                else halving.price)
        if not base:
            continue
        paths.append({
            "label": halving.label,
            "days": (leg.index - start).days.to_numpy(),
            "multiple": (leg / base).to_numpy(),
            "base": base,
            "current": last,
        })
    return paths


def _at_day(path: dict, day: int) -> Optional[float]:
    """Le multiple d'un cycle à son `day`-ième jour, s'il l'a vécu."""
    days = path["days"]
    if not len(days) or day < days[0] or day > days[-1]:
        return None
    return float(path["multiple"][int((days <= day).sum()) - 1])


def metrics(series: pd.Series, height: Optional[int] = None,
            now: Optional[datetime] = None) -> Optional[dict]:
    """La situation du jour, ou `None` sans série.

    Chaque mesure qui demande plus de profondeur que la série n'en a
    revient à `None` plutôt qu'approchée.
    """
    if series.empty:
        return None
    now = _naive_utc(now)
    last = float(series.iloc[-1])
    peak_date = series.idxmax()
    peak = float(series[peak_date])

    def average(window: int) -> Optional[float]:
        if len(series) < window:
            return None
        return float(series.iloc[-window:].mean())

    ratio = mayer(series)
    ma200, ma200w = average(200), average(WEEKS_200)
    ma111, ma350 = average(111), average(350)

    paths = cycle_paths(series)
    current = next((p for p in paths if p["current"]), None)
    day = int(current["days"][-1]) if current else None
    same_day = [
        {"label": p["label"], "multiple": _at_day(p, day)}
        for p in paths if not p["current"] and day is not None
    ]
    return {
        "price": last,
        "ath": peak, "ath_date": peak_date.to_pydatetime(),
        "drawdown": (last / peak - 1) * 100,
        "days_since_ath": int((series.index[-1] - peak_date).days),
        "ma200": ma200,
        "mayer": float(ratio.iloc[-1]) if len(ratio) else None,
        # Part des jours passés où le multiple était plus bas : dit si
        # 1,3 est banal ou rare, ce que le chiffre seul ne dit pas.
        "mayer_rank": (float((ratio.iloc[:-1] < ratio.iloc[-1]).mean())
                       if len(ratio) > 1 else None),
        "ma200w": ma200w,
        "ma200w_ratio": last / ma200w if ma200w else None,
        "pi_ratio": ma111 / (2 * ma350) if ma111 and ma350 else None,
        "cycle_day": day,
        "cycle_multiple": float(current["multiple"][-1]) if current else None,
        "same_day": [row for row in same_day if row["multiple"] is not None],
        "halving": next_halving(height, now),
    }


#: Blocs entre deux ajustements de difficulté, et la borne du protocole :
#: un ajustement ne multiplie ni ne divise la difficulté par plus de quatre.
RETARGET_INTERVAL = 2016
RETARGET_LIMIT = 4.0


def difficulty_estimate(height: Optional[int], epoch_start: Optional[float],
                        now: float) -> Optional[dict]:
    """Le prochain ajustement de difficulté, estimé au rythme de la période
    en cours.

    `epoch_start` est l'heure (epoch) du premier bloc de la période — celui
    dont la hauteur est un multiple de 2 016. Si la période tient le même
    rythme jusqu'au bout, la difficulté sera multipliée par dix minutes sur
    le temps moyen d'un bloc : un réseau qui a perdu des mineurs trouve ses
    blocs plus lentement, et la difficulté baisse. Rend `blocks_done`,
    `blocks_left`, `avg_minutes`, `change_pct` (borné par le protocole) et
    `eta` (epoch) ; `None` au premier bloc d'une période ou sans données.
    """
    if not height or epoch_start is None:
        return None
    done = height % RETARGET_INTERVAL
    elapsed = now - epoch_start
    if done < 1 or elapsed <= 0:
        return None
    average = elapsed / done
    factor = BLOCK_MINUTES * 60 / average
    factor = min(max(factor, 1 / RETARGET_LIMIT), RETARGET_LIMIT)
    left = RETARGET_INTERVAL - done
    return {"blocks_done": done, "blocks_left": left,
            "avg_minutes": average / 60, "change_pct": (factor - 1) * 100,
            "eta": now + left * average, "start_height": height - done}


def hash_ribbons(frame: Optional[pd.DataFrame], fast: int = 30,
                 slow: int = 60, recent_days: int = 30) -> Optional[dict]:
    """Les « hash ribbons » : les moyennes de 30 et 60 jours du hashrate.

    Quand la courte passe sous la longue, des mineurs débranchent — leurs
    machines ne paient plus, et ils vendent pour tenir : la
    **capitulation**. Quand elle repasse au-dessus, la **reprise** : le
    moment que l'indicateur (Charles Edwards, 2019) tient pour un bas de
    cycle, plus souvent juste que faux sur les cycles passés, sans rien
    garantir. Rend `frame` (`time`, `value`, `fast`, `slow`), `state`
    (`capitulation`, `reprise` — croisement à la hausse depuis moins de
    `recent_days` jours — ou `sain`) et `since`, la date du dernier
    croisement ; `None` sans assez de jours pour la moyenne longue.
    """
    if frame is None or len(frame) < slow + 1:
        return None
    work = frame[["time", "value"]].copy()
    work["time"] = pd.to_datetime(work["time"])
    work = work.sort_values("time").reset_index(drop=True)
    work["fast"] = work["value"].rolling(fast).mean()
    work["slow"] = work["value"].rolling(slow).mean()
    valid = work.dropna(subset=["fast", "slow"])
    if valid.empty:
        return None
    below = (valid["fast"] < valid["slow"]).to_numpy()
    changes = [i for i in range(1, len(below)) if below[i] != below[i - 1]]
    since = (valid["time"].iloc[changes[-1]] if changes
             else valid["time"].iloc[0])
    last = valid["time"].iloc[-1]
    if below[-1]:
        state = "capitulation"
    elif changes and (last - since).days < recent_days:
        state = "reprise"
    else:
        state = "sain"
    return {"frame": work, "state": state, "since": since}


#: Bornes d'usage du MVRV : sous 1, le marché vaut moins que le prix payé en
#: moyenne pour chaque bitcoin — les creux de 2015, 2018-2019, 2022 ;
#: au-dessus de 3,5, il le vaut plus de trois fois et demie — les sommets
#: de 2013, 2017 et (presque) 2021.
MVRV_LOW = 1.0
MVRV_HIGH = 3.5


def mvrv_state(frame: Optional[pd.DataFrame]) -> Optional[dict]:
    """Le MVRV du jour, le prix réalisé qui s'en déduit, et son rang.

    `frame` : `time`, `mvrv`, `price` (§ sources.fetch_mvrv). Le prix
    réalisé est le prix moyen auquel chaque bitcoin a bougé pour la
    dernière fois — le prix de revient du marché. Rend `mvrv`, `realized`,
    `date` et `rank` (la part des jours passés où il était plus bas).
    """
    if frame is None or frame.empty:
        return None
    last = frame.iloc[-1]
    mvrv = float(last["mvrv"])
    price = float(last["price"]) if pd.notna(last["price"]) else None
    history = frame["mvrv"].iloc[:-1]
    return {"mvrv": mvrv, "date": pd.Timestamp(last["time"]),
            "realized": price / mvrv if price and mvrv else None,
            "rank": float((history < mvrv).mean()) if len(history) else None}


#: Bornes d'usage du multiple de Puell, une convention plus qu'une norme :
#: sous 0,5, les bitcoins émis valent moins de la moitié de leur moyenne
#: annuelle — les mineurs les moins efficaces ne couvrent plus leurs coûts,
#: les creux de 2015, 2018-2019, 2022 ; au-dessus de 4, quatre fois plus —
#: les sommets de 2013 et 2017.
#: Les sommets de 2021 et 2024 sont restés bien en dessous : la borne
#: haute ne sonne plus guère.
PUELL_LOW = 0.5
PUELL_HIGH = 4.0

#: La moyenne de référence, en jours.
PUELL_WINDOW = 365


def puell(frame: Optional[pd.DataFrame]) -> pd.Series:
    """Le multiple de Puell, jour par jour, indexé par la date.

    La valeur en dollars des bitcoins émis ce jour-là (`issuance`, §
    sources.fetch_mvrv) sur sa moyenne des `PUELL_WINDOW` jours : ce que
    gagnent les mineurs, rapporté à ce qu'ils gagnent d'habitude. L'émission
    varie avec le nombre de blocs trouvés dans la journée — 111 un jour,
    135 le lendemain —, la courbe est donc nerveuse ; c'est sa zone qui
    compte, pas son dernier point. Vide sans un an d'émission.
    """
    if frame is None or frame.empty or "issuance" not in frame:
        return pd.Series(dtype=float)
    issued = (frame.set_index("time")["issuance"].astype(float)
              .where(lambda s: s > 0).dropna())
    average = issued.rolling(PUELL_WINDOW, min_periods=PUELL_WINDOW).mean()
    return (issued / average).dropna()


def puell_state(frame: Optional[pd.DataFrame]) -> Optional[dict]:
    """Le multiple de Puell du dernier jour publié, et son rang.

    Rend `puell`, `date`, `rank` (la part des jours passés où il était plus
    bas) et `halving` : la date du dernier halving s'il remonte à moins
    d'un an. La moyenne mêle alors des jours d'avant, à subvention double,
    et le multiple sort bas sans que les mineurs souffrent pour autant —
    l'écart se résorbe à l'anniversaire du halving.
    """
    series = puell(frame)
    if series.empty:
        return None
    value = float(series.iloc[-1])
    date = pd.Timestamp(series.index[-1])
    history = series.iloc[:-1]
    recent = [h.date for h in HALVINGS
              if 0 <= (date - pd.Timestamp(h.date)).days < PUELL_WINDOW]
    return {"puell": value, "date": date,
            "rank": float((history < value).mean()) if len(history) else None,
            "halving": pd.Timestamp(recent[-1]) if recent else None}


#: La fenêtre du hashprice, en jours : une semaine lisse le nombre de blocs
#: trouvés d'un jour à l'autre.
HASHPRICE_WINDOW = 7


def hashprice(frame: Optional[pd.DataFrame],
              window: int = HASHPRICE_WINDOW) -> pd.DataFrame:
    """Le hashprice : ce que rapporte un PH/s de calcul par jour, en dollars.

    (bitcoins émis + frais) × cours, sur le hashrate en PH/s — les colonnes
    `issued_btc`, `fees_btc`, `price`, `hashrate` (TH/s) de Coin Metrics
    (§ sources.fetch_mvrv). Rapport des sommes sur `window` jours plutôt
    que moyenne des rapports : un jour à 111 blocs pèse ce qu'il pèse.
    Rend `time`, `hashprice` et `fee_share` (la part des frais dans le
    revenu, en %) ; vide sans les colonnes.

    C'est la paie du mineur : un hashprice qui touche ses plus bas pousse
    les machines les moins efficaces à s'éteindre — ce que les hash ribbons
    voient ensuite au hashrate. Le 11 novembre 2026 : 31,5 $/PH/s/j, frais
    sous 1 % du revenu.
    """
    columns = ("issued_btc", "fees_btc", "price", "hashrate")
    if frame is None or frame.empty or not set(columns) <= set(frame):
        return pd.DataFrame(columns=["time", "hashprice", "fee_share"])
    work = frame.dropna(subset=list(columns))
    work = work[work["hashrate"] > 0]
    if work.empty:
        return pd.DataFrame(columns=["time", "hashprice", "fee_share"])
    coins = work["issued_btc"] + work["fees_btc"]
    revenue = (coins * work["price"]).rolling(window, min_periods=window).sum()
    power = (work["hashrate"] / 1_000).rolling(window, min_periods=window).sum()
    fees = work["fees_btc"].rolling(window, min_periods=window).sum()
    total = coins.rolling(window, min_periods=window).sum()
    out = pd.DataFrame({"time": work["time"], "hashprice": revenue / power,
                        "fee_share": fees / total * 100})
    return out.dropna().reset_index(drop=True)


def hashprice_state(frame: Optional[pd.DataFrame]) -> Optional[dict]:
    """Le hashprice du dernier jour publié, son écart à trente jours, la
    part des frais, et son plus bas de l'année — la paie du mineur, et où
    elle en est. `None` sans série."""
    series = hashprice(frame)
    if series.empty:
        return None
    last = series.iloc[-1]
    date = pd.Timestamp(last["time"])
    before = series[series["time"] <= date - pd.Timedelta(days=30)]
    year = series[series["time"] > date - pd.Timedelta(days=365)]
    value = float(last["hashprice"])
    previous = float(before["hashprice"].iloc[-1]) if not before.empty else None
    return {"hashprice": value, "date": date,
            "fee_share": float(last["fee_share"]),
            "change_30d": ((value / previous - 1) * 100
                           if previous else None),
            "low_365": float(year["hashprice"].min()),
            "at_low": value <= float(year["hashprice"].min()) * 1.02}

