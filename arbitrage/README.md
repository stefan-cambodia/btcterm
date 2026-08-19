# ⚡ BTC Real-Time Arbitrage Monitor

Surveille les order books de **5 exchanges** en temps réel et détecte les opportunités d'arbitrage BTC/USDT.

## Exchanges supportés

| Exchange | Frais maker | WebSocket |
|----------|------------|-----------|
| Binance  | 0.10%      | ✅ |
| Kraken   | 0.26%      | ✅ |
| Bybit    | 0.10%      | ✅ |
| OKX      | 0.10%      | ✅ |
| Coinbase | 0.60%      | ✅ |

## Installation

```bash
pip install -r requirements.txt
```

## Lancement

```bash
python main.py
```

Quitter avec `Ctrl+C`.

## Architecture

```
main.py
├── OrderBook          – modèle de données par exchange
├── ArbitrageOpportunity – représente une opportunité détectée
├── ExchangeConnector  – base WebSocket avec reconnexion auto
│   ├── BinanceConnector
│   ├── KrakenConnector
│   ├── BybitConnector
│   ├── OKXConnector
│   └── CoinbaseConnector
├── ArbitrageEngine    – scan toutes les paires d'exchanges
└── Dashboard          – rendu Rich (terminal)
```

## Logique d'arbitrage

Pour chaque paire (Exchange A, Exchange B) :

```
profit_brut  = (best_bid_B - best_ask_A) / best_ask_A * 100
profit_net   = profit_brut - fee_A - fee_B
```

Une opportunité est affichée en vert 🔥 si `profit_net > 0.1%`.

## Paramètres configurables (`main.py`)

| Variable | Défaut | Description |
|----------|--------|-------------|
| `MIN_PROFIT_PCT` | `0.1` | Seuil min de profit net (%) |
| `ORDER_BOOK_DEPTH` | `8` | Niveaux affichés par order book |

## ⚠️ Avertissements

- Ce script est à des fins **éducatives et d'analyse**.
- L'arbitrage réel nécessite des API keys, des fonds sur chaque exchange et une exécution en millisecondes.
- Les frais de retrait/transfert entre exchanges réduisent significativement la rentabilité.
- Les opportunités visibles à l'écran peuvent avoir disparu avant l'exécution.
