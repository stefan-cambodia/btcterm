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
    pip install requests pandas lxml beautifulsoup4 tabulate
"""

import argparse
import sys
import pandas as pd
from tabulate import tabulate

from btcterm.sources import fetch_etf_flows


def summarize(df: pd.DataFrame, days: int | None) -> pd.DataFrame:
    if days and days > 0:
        df = df.tail(days)
    return df


def main():
    parser = argparse.ArgumentParser(description="Flux ETF Bitcoin spot (US)")
    parser.add_argument("--csv", help="Chemin du fichier CSV de sortie", default=None)
    parser.add_argument("--days", type=int, default=90, help="Nombre de jours à afficher (0 = tout l'historique)")
    args = parser.parse_args()

    print("Récupération des données depuis farside.co.uk ...")
    try:
        df = fetch_etf_flows()
    except Exception as e:
        print(f"Erreur lors de la récupération des données : {e}", file=sys.stderr)
        sys.exit(1)

    view = summarize(df, args.days)

    if "Total" in view.columns:
        total_col = "Total"
    else:
        # certaines versions du site nomment la colonne différemment
        total_col = view.columns[-1]

    # On retire les colonnes ETF totalement à zéro sur la période affichée
    # (ETF pas encore lancés ou sans activité) pour alléger le tableau
    etf_cols = [c for c in view.columns if c not in ("Date", total_col)]
    active_cols = [c for c in etf_cols if view[c].abs().sum() > 0]
    display_cols = ["Date"] + active_cols + [total_col]

    table = view[display_cols].copy()
    table["Date"] = table["Date"].dt.strftime("%Y-%m-%d")
    for col in active_cols + [total_col]:
        table[col] = table[col].round(1)

    print(f"\n=== Flux ETF Bitcoin spot - {len(view)} derniers jours (en millions $) ===\n")
    print(
        tabulate(
            table,
            headers="keys",
            tablefmt="simple",
            showindex=False,
            numalign="right",
            floatfmt=".1f",
        )
    )

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
