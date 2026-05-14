#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "❌ Environnement virtuel non trouvé."
    echo "   Lancez d'abord : ./install.sh"
    exit 1
fi

source venv/bin/activate
python main.py
