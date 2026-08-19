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
| **Carnet** | 8 niveaux de chaque côté, spread, âge du flux, choix de la plateforme | 250 ms |
| **Profondeur** | profondeur cumulée des 5 plateformes superposées, recentrées en % du prix médian (onglet du carnet) | 250 ms |
| **Arbitrage** | écarts inter-plateformes nets de frais, triés par rentabilité | 250 ms |
| **Liquidations** | positions fermées de force, toutes paires, totaux de l'heure (onglet de l'arbitrage) | 250 ms |
| **Flux ETF** | entrées/sorties nettes des ETF spot sur 30 jours | 5 min |
| **Perpétuel** | financement, open interest et part des comptes longs (onglet des flux ETF) | 5 min |
| **News** | fil scoré + indice Fear & Greed | 2 s en lecture, collecte toutes les 15 min |
| **Calendrier** | prochaines échéances macro — FOMC, CPI, NFP, PCE — avec compte à rebours (onglet des news) | 5 min |
| **Macro** | cours contre masse monétaire M2 (US), décalage réglable et corrélations | 5 min |
| **Dominance** | parts de capitalisation, cap totale et volume 24 h (onglet de la macro) | 5 min |
| **On-chain** | hashrate et difficulté sur un an, rythme des blocs, mempool (onglet de la macro) | 5 min |

Les **liquidations** sont le pendant du perpétuel : quand une position à levier
ne couvre plus sa marge, la plateforme la ferme au marché, et ces fermetures
arrivent par rafales qui expliquent une partie des mèches du graphique. Le flux
est épisodique — plusieurs minutes de silence ne signalent aucune panne, et le
panneau distingue un flux coupé d'un marché calme.

Le **perpétuel** se lit avec le carnet : le taux de financement est le loyer que
les longs paient aux shorts toutes les huit heures, l'open interest mesure la
taille des positions ouvertes. Un financement élevé sur un open interest qui
gonfle décrit un marché endetté d'un seul côté — la configuration d'où sortent
les liquidations en cascade.

**Onglets** — une cellule peut héberger plusieurs panneaux, choisis par les
onglets posés à la place du titre. Cinq cellules en portent : carnet et
profondeur, flux ETF et perpétuel, news et calendrier, macro et dominance et
on-chain — plus l'arbitrage, qui partage sa place avec les liquidations. Un
panneau caché n'est pas dans la page — il ne coûte rien, et il se remplit dès
qu'on l'affiche.

**Plein écran** — trois façons d'agrandir un panneau :

- le **⛶** en haut à droite du panneau,
- un **double-clic** n'importe où dessus (sauf sur un graphique, où Plotly
  garde le double-clic pour réinitialiser les axes),
- puis `Échap` ou un second clic pour revenir à la grille.

Cliquer le ⛶ d'un autre panneau bascule directement de l'un à l'autre.

Les panneaux s'adaptent à la place disponible :

- le **cours** occupe 69 % de la hauteur du graphique dans la grille, 77 % en
  plein écran, et **100 %** si l'on décoche tout ;
- le **carnet** affiche 8 niveaux de chaque côté dans la grille, 20 en plein
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

**News** — le terminal **remplit** lui-même `~/.btc_news/news.db`, toutes les
quinze minutes, avec les règles de scoring du tracker : plus besoin du timer
systemd pour avoir un fil vivant. La barre de titre du panneau donne l'âge de la
dernière collecte, et le nombre d'articles qu'elle a rapportés.

```bash
python -m terminal.app --no-news                 # laisser la base au tracker
CRYPTOPANIC_API_KEY=… python -m terminal.app     # ajouter CryptoPanic aux RSS
```

**Macro** — le panneau du bas confronte le cours à la masse monétaire M2 des
États-Unis (série H.6 de la Fed, mensuelle). Le sélecteur `+1M` … `+3M` décale
M2 vers l'avant pour éprouver l'idée d'un cours qui suivrait la liquidité avec
un trimestre de retard ; les deux corrélations affichées disent ce qu'il en est.
Celle des **niveaux** est toujours forte et n'apprend rien — deux séries qui
montent depuis dix ans vont ensemble ; celle des **variations sur trois mois**
est la seule qui informe.

**Calendrier** — les prochaines échéances qui font bouger le marché : décisions
du FOMC (avec ou sans projections), inflation CPI et PCE, rapport sur l'emploi
(NFP), chacune avec son compte à rebours et son heure locale. Aucune API
publique satisfaisante n'existe pour ces dates ; elles sont transcrites à la
main dans `btcterm/macrocal.py` depuis les calendriers officiels (la Fed publie
les siennes deux ans à l'avance, l'OMB celles des statistiques fédérales un an).
Le pied du panneau dit jusqu'où court la liste, et prévient quand elle
s'épuise — un calendrier qui se tait parce qu'il est périmé doit se voir.

## Architecture

- **`btcterm/`** — le socle : indicateurs, carnets et connecteurs WebSocket,
  moteur d'arbitrage, collecteurs REST, base de news partagée avec le tracker,
  et le hub qui n'ouvre qu'une connexion par plateforme pour tous les panneaux.
- **`terminal/`** — l'application Dash : grille, thème, figures, panneaux.

Détail complet dans [`ARCHITECTURE.md`](ARCHITECTURE.md), feuille de route en
[§7](ARCHITECTURE.md#7-feuille-de-route-vers-le-terminal).

**Où en est le projet** — le terminal couvre le prix et ses indicateurs, le
carnet, la profondeur comparée, l'arbitrage, les liquidations, les flux ETF, le
marché à terme, les news, le calendrier macro, la dominance, la chaîne et le
contexte macro : la couverture visée par la feuille de route est atteinte. Les
scripts qu'il remplace ont été supprimés ; ceux qui restent (arbitrage en TUI,
export ETF, tracker de news) ne font double emploi avec aucun panneau.

## Outils complémentaires

Ce que le terminal ne couvre pas encore garde sa ligne de commande : le
moniteur d'arbitrage en TUI, l'export des flux ETF et le tracker de news, tous
bâtis sur le même socle. Les quatre scripts que le terminal a remplacés —
`btc-dash.py`, `btc_dashboard2.py`, `btc-liquidity.py`, `btc_orderbook_live.py` —
ont été supprimés, de même qu'`etf.py`, doublon antérieur d'`etf_bitcoin_flows.py`,
et `m2supply.html`, page tronquée que le panneau macro remplace.

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
python tests/test_news_scoring.py        # scoring et collecte des news
python tests/test_liquidations.py        # lecture du flux de liquidations
python tests/test_macrocal.py            # calendrier macro tenu à la main
python tests/test_terminal_wiring.py     # panneaux posés et branchés
python tests/test_fullscreen_toggle.py   # bascule plein écran (nécessite Node)

python -m terminal.app &                 # puis, terminal lancé :
python tests/ui_smoke.py --capture /tmp/captures   # contrôle dans Firefox
```

Le premier vérifie que les indicateurs du socle produisent exactement les mêmes
valeurs que les implémentations des dashboards d'origine, dont il conserve des
copies conformes — c'est ce qui a permis de supprimer ces scripts sans perdre
la garantie. Le deuxième fait de même pour le scoring des news, extrait du
tracker, et vérifie en prime ce que l'extraction rend enfin testable : la
collecte filtre sous le seuil et n'insère pas deux fois le même article.

Le troisième lit un flux de liquidations au format documenté par Binance sans
toucher au réseau — ce flux étant épisodique, le contrôle Firefox trouve presque
toujours le panneau vide, et le sens des événements (une vente forcée ferme une
position longue) mérite mieux qu'une observation chanceuse. Le quatrième garde
le calendrier macro contre ce qui guette une liste de dates tenue à la main : la
faute de frappe silencieuse (aucune publication ne tombe un week-end) et le
décalage d'heure d'été entre New York et l'Europe. Le cinquième vérifie
qu'aucun panneau n'a été écrit puis oublié — ni dans la
grille, ni dans l'enregistrement des callbacks, ni dans la liste des panneaux
qu'une cellule peut afficher, un panneau absent de cette liste n'étant
atteignable par aucun clic. Le sixième exécute la fonction JavaScript du plein
écran sous Node, faute de quoi elle échapperait à toute couverture. Aucun des
six ne touche au réseau.

`ui_smoke.py` est à part : il pilote Firefox pour contrôler ce qui ne se voit
qu'à l'écran — cellules posées, bouton visible et sans recouvrement, bascule
plein écran effective, carnet montrant ses deux côtés, barre de titre du panneau
prix tenant sur une ligne, échelle logarithmique atteignant l'axe, panneau macro
traçant ses deux séries, et changement d'onglet remplaçant un panneau par
l'autre, rempli dès son apparition. Il sait déposer des captures, suppose le
terminal déjà lancé, et s'ignore si Firefox est absent.

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

Le scoring, le schéma et la collecte vivent dans `btcterm/newsdb.py` : ce script
en garde la ligne de commande et l'affichage, le terminal la même base et les
mêmes règles. Le tracker reste utile quand le terminal ne tourne pas — et
`news/systemd_timer.conf` contient (en commentaires, à décommenter et adapter)
les unités systemd `--user` pour un `fetch` automatique toutes les 30 minutes.

---

## Notes

- Le terminal écrit dans `~/.btc_news/news.db` — la base du tracker, mêmes
  règles, mêmes déduplications. `--no-news` l'en dispense.
- Aucun de ces scripts n'écrit d'ordre sur un exchange ; ils sont en lecture
  seule sur des endpoints publics.
- Le terminal se lie à `127.0.0.1:8050` ; `--host` et `--port` permettent d'en
  changer.
- Le dépôt est versionné avec git (branche `main`, pas de remote).
