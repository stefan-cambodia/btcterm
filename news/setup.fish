#!/usr/bin/env fish
# ─────────────────────────────────────────────────────────────────
#  setup.fish  –  Installation du BTC News Tracker sur CachyOS/Fish
# ─────────────────────────────────────────────────────────────────

set SCRIPT_DIR (dirname (realpath (status filename)))
set VENV_DIR   "$SCRIPT_DIR/.venv"
set SCRIPT     "$SCRIPT_DIR/btc_news.py"

echo ""
echo "🪙  BTC News Tracker – Setup"
echo "────────────────────────────"

# 1. Vérifier python3
if not command -q python3
    echo "❌  python3 introuvable. Installe-le avec : sudo pacman -S python"
    exit 1
end
echo "✅  Python : "(python3 --version)

# 2. Créer le venv
if not test -d "$VENV_DIR"
    echo "🔧  Création du venv dans $VENV_DIR …"
    python3 -m venv "$VENV_DIR"
else
    echo "✅  Venv déjà présent : $VENV_DIR"
end

# 3. Activer le venv et installer les dépendances
echo "📦  Installation des dépendances …"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements.txt"
echo "✅  Dépendances installées."

# 4. Rendre le script exécutable
chmod +x "$SCRIPT"

# 5. Créer un wrapper fish dans ~/.config/fish/functions/
set FUNC_DIR ~/.config/fish/functions
mkdir -p "$FUNC_DIR"

set FUNC_FILE "$FUNC_DIR/btcnews.fish"
echo "function btcnews" > "$FUNC_FILE"
echo "    $VENV_DIR/bin/python $SCRIPT \$argv" >> "$FUNC_FILE"
echo "end" >> "$FUNC_FILE"

echo "✅  Fonction fish 'btcnews' créée dans $FUNC_FILE"

# 6. Optionnel : alias court
echo ""
echo "────────────────────────────────────────────────────────"
echo "🎉  Installation terminée !"
echo ""
echo "Recharge ton shell :"
echo "   source ~/.config/fish/config.fish"
echo "   # ou ouvre un nouveau terminal"
echo ""
echo "Utilisation :"
echo "   btcnews fetch              # Récupère les news maintenant"
echo "   btcnews list               # Affiche les dernières news"
echo "   btcnews list --min-score 60 # Seulement les très importantes"
echo "   btcnews unread             # News non lues"
echo "   btcnews search 'etf'       # Recherche"
echo "   btcnews stats              # Stats de la base"
echo "   btcnews watch --interval 30 # Surveillance toutes les 30 min"
echo ""
echo "📂  Base de données : ~/.btc_news/news.db"
echo ""
echo "💡  CryptoPanic (optionnel, plus de sources) :"
echo "   → Crée un compte gratuit sur https://cryptopanic.com"
echo "   → Ajoute ta clé dans btc_news.py : CRYPTOPANIC_API_KEY = 'TACLÉ'"
echo "   → ou passe-la à chaque commande : btcnews fetch --api-key TACLÉ"
echo "────────────────────────────────────────────────────────"
