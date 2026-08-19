# 🪙 BTC Terminal

**Objectif du projet : construire une sorte de terminal Bloomberg orienté
Bitcoin** — un poste de travail unique regroupant, sur des panneaux
synchronisés, tout ce qu'il faut pour lire le marché : prix et indicateurs
techniques, carnets d'ordres et profondeur multi-exchange, opportunités
d'arbitrage, flux des ETF spot, news à impact et sentiment de marché.

## Lancement

```bash
python -m terminal.app
```

→ **http://127.0.0.1:8050**

À distance, par tunnel SSH (le port n'est pas exposé sur le réseau) :

```bash
ssh -L 8050:localhost:8050 <machine>
```

## Les panneaux

| Panneau | Contenu | Rafraîchissement |
|---|---|---|
| **Prix** | chandeliers de 15 m à 1 M, MA 9/26/200, Bollinger, POC + Value Area, signaux, bascule `$`/`€`, échelle log, sous-graphiques optionnels | 2 s |
| **Carnet** | 12 niveaux de chaque côté, spread, âge du flux, choix de la plateforme | 250 ms |
| **Profondeur** | profondeur cumulée des 5 plateformes superposées, recentrées en % du prix médian | 250 ms |
| **Arbitrage** | écarts inter-plateformes nets de frais, triés par rentabilité | 250 ms |
| **Flux ETF** | entrées/sorties nettes des ETF spot sur 30 jours | 5 min |
| **News** | fil scoré + indice Fear & Greed | 5 min |

**Plein écran** — trois façons d'agrandir un panneau :

- le **⛶** en haut à droite du panneau,
- un **double-clic** n'importe où dessus (sauf sur un graphique, où Plotly
  garde le double-clic pour réinitialiser les axes),
- puis `Échap` ou un second clic pour revenir à la grille.

Cliquer le ⛶ d'un autre panneau bascule directement de l'un à l'autre.

Les panneaux s'adaptent à la place disponible :

- le **cours** occupe 69 % de la hauteur du graphique dans la grille, 77 % en
  plein écran, et **100 %** si l'on décoche tout ;
- le **carnet** affiche 6 niveaux de chaque côté dans la grille, 20 en plein
  écran.

**Intervalles** — `15m` `30m` `1h` `4h` `6h` `12h` `1d` `1w` `1M`, à la casse
Binance : `m` pour les minutes, `M` pour le mois. Chacun a sa profondeur
d'historique — de quoi nourrir la MA 200 en intraday, sans tirer trente ans de
bougies mensuelles. La case `LOG` passe l'axe des prix en
logarithmique — indispensable dès qu'on remonte plusieurs années, où une
progression de 4 000 à 80 000 dollars écrase tout le début du graphique.

**Sous-graphiques optionnels** — les cases `RSI` · `CRSI` · `VOL` · `PROFIL` de
la barre de titre du panneau prix décident de ce qui accompagne les chandeliers.
Tout ce qu'on décoche rend sa hauteur au cours. Par défaut : RSI, volume et
profil de volume ; le CRSI est disponible mais masqué.

Le graphique conserve zoom et pan pendant que les données coulent — c'est ce qui
permet d'analyser une zone sans être recadré à chaque tour d'horloge.

**Hors ligne** — si Binance est injoignable au démarrage, le panneau prix sert
une série de démonstration générée localement plutôt qu'un cadre vide, et le
signale par un bandeau orange : les chiffres affichés ne sont alors pas réels.

Le panneau news **lit** la base alimentée par `news/btc_news.py` ; il ne la
remplit pas. Pour l'alimenter : `python news/btc_news.py fetch`, ou le timer
systemd fourni.

## Architecture

- **`btcterm/`** — le socle : indicateurs, carnets et connecteurs WebSocket,
  moteur d'arbitrage, collecteurs, et le hub qui n'ouvre qu'une connexion par
  plateforme pour tous les panneaux.
- **`terminal/`** — l'application Dash : grille, thème, figures, panneaux.

Détail complet dans [`ARCHITECTURE.md`](ARCHITECTURE.md), feuille de route en
[§7](ARCHITECTURE.md#7-feuille-de-route-vers-le-terminal).

## Outils complémentaires

Ce que le terminal ne couvre pas encore garde sa ligne de commande : le
moniteur d'arbitrage en TUI, l'export des flux ETF et le tracker de news, tous
bâtis sur le même socle. Les quatre scripts que le terminal a remplacés —
`btc-dash.py`, `btc_dashboard2.py`, `btc-liquidity.py`, `btc_orderbook_live.py` —
ont été supprimés, de même qu'`etf.py`, doublon antérieur d'`etf_bitcoin_flows.py`.

> Données de marché : APIs publiques (Binance, Kraken, Coinbase, Bybit, OKX) —
> **aucune clé API n'est requise**, aucun ordre n'est jamais passé.

---

## Table des outils

| Outil | Type | Sources | Lancement |
|---|---|---|---|
| `terminal/` | Terminal web (Dash) | REST + WebSockets, 5 plateformes | `python -m terminal.app` → http://127.0.0.1:8050 |
| `arbitrage/main.py` | TUI terminal (Rich) | WebSockets 5 exchanges | `python arbitrage/main.py` |
| `etf_bitcoin_flows.py` | CLI | farside.co.uk (scraping) | `python etf_bitcoin_flows.py --days 90` |
| `news/btc_news.py` | CLI + SQLite | RSS, CryptoPanic, Fear & Greed | `python news/btc_news.py fetch` |
| `m2supply.html` | Page statique Plotly | — | ⚠️ fichier incomplet, voir plus bas |

Voir [`ARCHITECTURE.md`](ARCHITECTURE.md) pour le détail interne de chaque module.

---

## Installation

```fish
# Activer le venv existant (fish)
source venv/bin/activate.fish

# Toutes les dépendances du dépôt
pip install -r requirements.txt
```

Le venv présent à la racine (`venv/`, Python 3.14) contient déjà toutes ces
dépendances.

`requirements.txt` est groupé par usage : pour n'installer qu'une partie, il
suffit de reprendre le bloc concerné. Le socle `btcterm/` ne demande que
`pandas`, `numpy` et `requests`.

Aucune installation de paquet n'est nécessaire : les scripts trouvent
`btcterm/` par eux-mêmes, où que soit le répertoire courant.

`arbitrage/requirements.txt` et `news/requirements.txt` restent disponibles
pour installer un seul sous-projet, et `news/setup.fish` crée un venv dédié
plus une fonction fish `btcnews`.

### Tests

```bash
python tests/test_indicators_parity.py   # indicateurs identiques à l'origine
python tests/test_terminal_wiring.py     # panneaux posés et branchés
python tests/test_fullscreen_toggle.py   # bascule plein écran (nécessite Node)

python -m terminal.app &                 # puis, terminal lancé :
python tests/ui_smoke.py --capture /tmp/captures   # contrôle dans Firefox
```

Le premier vérifie que les indicateurs du socle produisent exactement les mêmes
valeurs que les implémentations des dashboards d'origine, dont il conserve des
copies conformes — c'est ce qui a permis de supprimer ces scripts sans perdre
la garantie. Le deuxième qu'aucun
panneau n'a été écrit puis oublié — ni dans la grille, ni dans l'enregistrement
des callbacks. Le troisième exécute la fonction JavaScript du plein écran sous
Node, faute de quoi elle échapperait à toute couverture. Aucun des trois ne
touche au réseau.

`ui_smoke.py` est à part : il pilote Firefox pour contrôler ce qui ne se voit
qu'à l'écran — panneaux posés, bouton visible et sans recouvrement, bascule
effective, carnet montrant ses deux côtés — et sait déposer des captures. Il
suppose le terminal déjà lancé, et s'ignore si Firefox est absent.

## Les outils en détail

### 1. `arbitrage/main.py` — Moniteur d'arbitrage temps réel

TUI Rich plein écran surveillant **5 exchanges** (Binance, Kraken, Bybit, OKX,
Coinbase) et scannant toutes les paires ordonnées 5 fois par seconde :

```
profit_brut = (best_bid_vente - best_ask_achat) / best_ask_achat * 100
profit_net  = profit_brut - frais_achat - frais_vente
```

Une opportunité est retenue au-delà de `MIN_PROFIT_PCT = 0.1 %`, et les
carnets de plus de **5 s** sont ignorés. Le carnet Bybit était figé sur son
premier snapshot tout en paraissant frais ; corrigé en phase 1. Voir `arbitrage/README.md` pour la
grille de frais et les avertissements — c'est un outil d'observation, pas un
bot d'exécution.

### 2. `etf_bitcoin_flows.py` — Flux des ETF Bitcoin spot

Récupère le tableau public de `farside.co.uk/btc/` (flux quotidiens IBIT,
FBTC, GBTC, ARKB, BITB, HODL…) et affiche les N derniers jours en millions
de dollars, plus le flux net cumulé et le décompte des jours entrants/sortants.

```bash
python etf_bitcoin_flows.py                 # 90 derniers jours
python etf_bitcoin_flows.py --days 0        # tout l'historique
python etf_bitcoin_flows.py --csv flows.csv # export CSV complet
```

Ce script avait un doublon, `etf.py`, mouture antérieure qui ne gérait pas les
en-têtes multi-niveaux du site, n'élaguait pas les colonnes vides, passait le
HTML directement à `pd.read_html()` (déprécié) et affichait via
`DataFrame.to_string` au lieu de `tabulate`. Il a été supprimé.

### 3. `news/btc_news.py` — BTC News Tracker

CLI d'agrégation de news à impact sur le cours, stockées dans SQLite
(`~/.btc_news/news.db`). Sources : 6 flux RSS (CoinDesk, CoinTelegraph,
Decrypt, Bitcoin Magazine, The Block, CryptoSlate), l'API CryptoPanic
(optionnelle, clé gratuite) et le Fear & Greed Index.

Chaque article reçoit un **score 0-100** par pondération de mots-clés
(régulation, ETF, Fed, halving, hack, ATH…) ; sous `MIN_SCORE = 30`, il est
écarté. Un sentiment `bullish` / `bearish` / `neutral` est déduit du
vocabulaire (ou des votes CryptoPanic quand ils existent).

```bash
btcnews fetch -v                 # récupérer
btcnews list --min-score 60      # seulement les news importantes
btcnews list --sentiment bearish
btcnews unread                   # non lues, puis marquées lues
btcnews search "etf"
btcnews stats
btcnews watch --interval 30      # boucle de surveillance
```

`news/systemd_timer.conf` contient (en commentaires, à décommenter et adapter)
les unités systemd `--user` pour un `fetch` automatique toutes les 30 minutes.

### 4. `m2supply.html` — ⚠️ fichier incomplet

Page Plotly censée superposer le cours du BTC et la masse monétaire M2
normalisés. **En l'état le fichier est tronqué** : il ne contient que la fin du
script (fin de `loadData`, layout, `Plotly.newPlot`, `setInterval` de 5 min).
Il manque le `<head>`, le chargement de la bibliothèque Plotly, le `<div
id="chart">` et tout le début de `loadData` — donc la récupération des données
BTC et M2. Ouvert tel quel dans un navigateur, il ne produit rien.

---

## Notes

- `order/` est un répertoire vide.
- Aucun de ces scripts n'écrit d'ordre sur un exchange ; ils sont en lecture
  seule sur des endpoints publics.
- Le terminal se lie à `127.0.0.1:8050` ; `--host` et `--port` permettent d'en
  changer.
- Le dépôt est versionné avec git (branche `main`, pas de remote).
