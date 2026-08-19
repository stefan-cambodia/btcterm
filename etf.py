#!/usr/bin/env python3
"""
Suivi des flux entrants/sortants des ETF Bitcoin spot (US).

Source des données : farside.co.uk/btc/ (page publique qui agrège les flux
quotidiens de tous les ETF Bitcoin spot américains : IBIT, FBTC, GBTC, ARKB,
BITB, HODL, BRRR, EZBC, BTCO, etc.)

Usage :
    python etf_bitcoin_flows.py                 # affiche les flux récents
    python etf_bitcoin_flows.py --csv flows.csv # exporte aussi en CSV
    python etf_bitcoin_flows.py --days 30        # limite aux N derniers jours

Dépendances :
    pip install requests pandas lxml beautifulsoup4
"""

import argparse
import sys
import requests
import pandas as pd

URL = "https://farside.co.uk/btc/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def fetch_flows() -> pd.DataFrame:
    """Récupère et nettoie le tableau des flux ETF Bitcoin depuis farside.co.uk"""
    resp = requests.get(URL, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    tables = pd.read_html(resp.text)
    if not tables:
        raise RuntimeError("Aucun tableau trouvé sur la page.")

    # Le tableau principal des flux est généralement le plus grand
    df = max(tables, key=lambda t: t.shape[0]).copy()

    # Nettoyage : la première colonne est la date, les suivantes les ETF,
    # la dernière colonne "Total" donne le flux net quotidien (en millions $)
    df.columns = [str(c).strip() for c in df.columns]
    first_col = df.columns[0]
    df = df.rename(columns={first_col: "Date"})

    # Suppression des lignes d'en-tête répétées / lignes vides
    df = df[df["Date"].astype(str).str.match(r"^\d{1,2} \w{3} \d{4}$", na=False)]

    df["Date"] = pd.to_datetime(df["Date"], format="%d %b %Y", errors="coerce")
    df = df.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)

    # Conversion des colonnes numériques (flux en millions de $)
    for col in df.columns[1:]:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("(", "-", regex=False)
            .str.replace(")", "", regex=False)
            .str.replace("US$m", "", regex=False)
            .str.strip()
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def summarize(df: pd.DataFrame, days: int | None) -> pd.DataFrame:
    if days:
        df = df.tail(days)
    return df


def main():
    parser = argparse.ArgumentParser(description="Flux ETF Bitcoin spot (US)")
    parser.add_argument("--csv", help="Chemin du fichier CSV de sortie", default=None)
    parser.add_argument("--days", type=int, default=15, help="Nombre de jours à afficher")
    args = parser.parse_args()

    print("Récupération des données depuis farside.co.uk ...")
    try:
        df = fetch_flows()
    except Exception as e:
        print(f"Erreur lors de la récupération des données : {e}", file=sys.stderr)
        sys.exit(1)

    view = summarize(df, args.days)

    if "Total" in view.columns:
        total_col = "Total"
    else:
        # certaines versions du site nomment la colonne différemment
        total_col = view.columns[-1]

    print(f"\n=== Flux ETF Bitcoin spot - {len(view)} derniers jours (en millions $) ===\n")
    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", None)
    print(view.to_string(index=False, formatters={"Date": lambda d: d.strftime("%Y-%m-%d")}))

    net_total = view[total_col].sum()
    inflow_days = (view[total_col] > 0).sum()
    outflow_days = (view[total_col] < 0).sum()

    print(f"\nFlux net cumulé sur la période : {net_total:,.1f} M$")
    print(f"Jours avec flux entrant net : {inflow_days}")
    print(f"Jours avec flux sortant net : {outflow_days}")

    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"\nDonnées complètes exportées vers : {args.csv}")


if __name__ == "__main__":
    main()
