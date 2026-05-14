#!/bin/bash
set -e

echo ""
echo "🎙  Installation de Voxaho..."
echo "================================"

# Utiliser python3.12 (Homebrew) si disponible, sinon python3
if command -v python3.12 &>/dev/null; then
    PYTHON=python3.12
elif command -v /opt/homebrew/bin/python3.12 &>/dev/null; then
    PYTHON=/opt/homebrew/bin/python3.12
else
    PYTHON=python3
fi

# Vérifier Python 3.10+
PYTHON_VERSION=$($PYTHON -c 'import sys; print(sys.version_info.major * 100 + sys.version_info.minor)')
if [ "$PYTHON_VERSION" -lt 310 ]; then
    echo "❌ Python 3.10+ requis. Version actuelle : $($PYTHON --version)"
    exit 1
fi
echo "✓ $($PYTHON --version)"

# Créer l'environnement virtuel
echo ""
echo "📦 Création de l'environnement virtuel..."
$PYTHON -m venv venv
source venv/bin/activate

# Installer les dépendances (sans --quiet pour voir les erreurs)
echo ""
echo "📥 Installation des dépendances..."
pip install --upgrade pip
if ! pip install -r requirements.txt; then
    echo ""
    echo "❌ Échec de l'installation des dépendances."
    echo "   Vérifiez votre connexion et réessayez."
    exit 1
fi
echo "✓ Dépendances installées."

# Pré-télécharger le modèle Whisper (dans le venv, donc python = python3.12)
echo ""
echo "📥 Téléchargement du modèle Whisper 'small' (environ 244 Mo)..."
echo "   (ceci peut prendre quelques minutes selon votre connexion)"
python -c "
from faster_whisper import WhisperModel
print('   Téléchargement en cours...')
WhisperModel('small', device='cpu', compute_type='int8')
print('   ✓ Modèle prêt.')
"

echo ""
echo "✅ Installation terminée !"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Prochaines étapes :"
echo ""
echo "  1. Autoriser l'Accessibilité :"
echo "     Réglages Système → Confidentialité → Accessibilité"
echo "     → Ajouter Terminal"
echo ""
echo "  2. Désactiver Fn = Émojis :"
echo "     Réglages Système → Clavier"
echo "     → 'Appuyer sur Fn pour' → 'Ne rien faire'"
echo ""
echo "  3. Lancer Voxaho :"
echo "     ./run.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
