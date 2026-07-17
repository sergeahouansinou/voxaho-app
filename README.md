# 🎙 Voxaho — Dictée vocale 100 % locale

**Voxaho** est une application de dictée vocale pour **macOS** et **Windows** qui transforme votre voix en texte et l'insère instantanément dans n'importe quelle application — email, document, code, messagerie — là où se trouve votre curseur.

> Maintenez une touche. Parlez. Relâchez. Le texte apparaît.

Contrairement aux solutions concurrentes basées sur le cloud, **Voxaho fonctionne entièrement en local** : votre voix ne quitte **jamais** votre machine. Aucun serveur, aucune connexion requise pour dicter, aucune donnée collectée. Confidentialité absolue — y compris pour les professions sensibles (juridique, médical, entreprise).

🌐 Site officiel : [voxaho.com](https://voxaho.com)

---

## ✨ Fonctionnalités

- **Dictée universelle** : fonctionne dans toutes vos applications (Mail, Word, Slack, VS Code, navigateur…)
- **Push-to-talk** : maintenez `Fn` (macOS) ou `Ctrl droit` (Windows, configurable) pendant que vous parlez
- **Transcription locale** propulsée par [Whisper](https://github.com/openai/whisper) via `faster-whisper` — 4 modèles au choix (tiny → large-v3) selon votre machine
- **6 langues de dictée** : français, anglais, espagnol, allemand, italien + détection automatique
- **Nettoyage automatique** : suppression des hésitations (« euh », « um »…), ponctuation, majuscules, filtre anti-hallucinations
- **Préservation du presse-papiers** : votre copier-coller en cours n'est jamais perdu
- **Barre flottante discrète** : indicateur d'état animé, personnalisable (position, couleur, taille)
- **Assistant de configuration** : onboarding guidé — permissions, choix du modèle, test micro, tutoriel
- **Démarrage automatique** à l'ouverture de session (optionnel)
- **Hors-ligne** : après le téléchargement initial du modèle, aucune connexion n'est nécessaire

## 💼 Modèle de licence

- **Essai gratuit de 14 jours**, sans carte bancaire
- **Licence à vie** — achat unique sur [voxaho.com](https://voxaho.com), activable/désactivable par machine

---

## 🗂 Architecture

```
localflow/
├── main.py               # Point d'entrée, config, gate licence/trial
├── core/
│   ├── recorder.py       # Capture micro (sounddevice, 16 kHz mono)
│   ├── transcriber.py    # Transcription faster-whisper + reformatage
│   ├── injector.py       # Injection du texte (presse-papiers + collage)
│   ├── hotkey.py         # Touche globale (CGEventTap macOS / pynput Windows)
│   ├── license.py        # Licence Lemon Squeezy + trial 14 j signé HMAC
│   ├── autostart.py      # Lancement à la session (LaunchAgent / registre)
│   └── relaunch.py       # Redémarrage après octroi de permissions
├── ui/
│   ├── floating_bar.py   # Barre flottante animée (états visuels)
│   ├── settings_window.py# Préférences (6 onglets)
│   └── setup_wizard.py   # Assistant premier lancement (7 étapes)
└── tests/                # Suite pytest (82 tests)
```

**Pipeline de dictée** : touche pressée → enregistrement → touche relâchée → transcription Whisper → nettoyage → injection dans l'app active.

## 🛠 Développement

### Prérequis
- Python 3.12+
- macOS 13+ ou Windows 10+

### Installation

```bash
# macOS / Linux
./install.sh

# Windows
install.bat
```

### Lancer en mode dev

```bash
./run.sh        # macOS
run.bat         # Windows
```

### Tests

```bash
./venv/bin/python -m pytest tests/ -v
```

### Build de distribution

```bash
./build_dmg.sh          # macOS → Voxaho.app + DMG (py2app)
build_windows.bat       # Windows → installeur (Inno Setup, build_installer.iss)
```

### Permissions requises
- **macOS** : Microphone + Accessibilité (interception de la touche Fn et collage)
- **Windows** : Microphone (aucun droit administrateur)

---

## 👤 Auteur

Voxaho est créé et développé par **Serge AHOUANSINOU**.

- 🌐 Portfolio : [sergeahouansinou.vercel.app](https://sergeahouansinou.vercel.app)
- 🎙 Produit : [voxaho.com](https://voxaho.com)

## 📄 Licence

© Serge AHOUANSINOU — Tous droits réservés.

Voxaho est un logiciel propriétaire. Ce dépôt contient le code source du produit ; toute reproduction, distribution ou utilisation commerciale sans autorisation est interdite.
