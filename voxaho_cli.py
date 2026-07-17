#!/usr/bin/env python3
"""
Point d'entrée racine de la CLI Voxaho.

Shim minimal qui délègue à core.cli.main(). S'utilise ainsi :
    python voxaho_cli.py transcribe note.wav
    python voxaho_cli.py history --json
    python voxaho_cli.py stats
    python voxaho_cli.py version

Note : un point d'entrée console `voxaho` (entry_points/console_scripts) pourra
être ajouté plus tard à setup.py pour exposer directement la commande `voxaho`
après installation — hors périmètre de ce module.
"""

import sys

from core.cli import main

if __name__ == "__main__":
    sys.exit(main())
