# 🎙 Voxaho — Dictée vocale 100 % locale

**Voxaho** est une application de dictée vocale pour **macOS** et **Windows** qui transforme votre voix en texte et l'insère instantanément dans n'importe quelle application — email, document, code, messagerie — là où se trouve votre curseur.

> Maintenez une touche. Parlez. Relâchez. Le texte apparaît.

Contrairement aux solutions concurrentes basées sur le cloud, **Voxaho fonctionne entièrement en local** : votre voix ne quitte **jamais** votre machine. Aucun serveur, aucune connexion requise pour dicter, aucune donnée collectée. Confidentialité absolue — y compris pour les professions sensibles (juridique, médical, entreprise).

🌐 Site officiel : [voxaho.com](https://voxaho.com)

---

## ✨ Fonctionnalités

**Dictée**
- **Dictée universelle** : fonctionne dans toutes vos applications (Mail, Word, Slack, VS Code, navigateur…)
- **Push-to-talk** : maintenez `Fn` (macOS) ou `Ctrl droit` (Windows, configurable) pendant que vous parlez
- **Transcription locale rapide** propulsée par [Whisper](https://github.com/openai/whisper) via `faster-whisper` — modèles tiny → `large-v3-turbo`, préchargement au démarrage (1ʳᵉ dictée sans attente), accélération **Metal/MLX** optionnelle sur Apple Silicon
- **~38 langues de dictée** + détection automatique
- **Choix du microphone** et réglage **Vitesse / Qualité**
- **Préservation du presse-papiers** : votre copier-coller en cours n'est jamais perdu

**Intelligence (100 % locale)**
- **Reformatage par IA locale** (opt-in) : un LLM local (**Qwen 2.5**, ~1 Go) reformule et met en forme votre dictée — hors-ligne, désactivable, avec repli automatique sur le nettoyage par règles
- **Nettoyage par règles** : suppression des hésitations (« euh », « um »…), ponctuation, majuscules (Unicode), filtre anti-hallucinations Whisper
- **Dictionnaire personnel** : vos termes/noms propres/jargon, mieux reconnus et corrigés automatiquement
- **Snippets vocaux** : un déclencheur parlé → un texte complet inséré

**Espace de travail (Workspace)**
- **Fenêtre principale à sidebar** : Accueil, Historique, Notes, Statistiques
- **Historique des dictées** : recherche, favoris, copier — stocké localement (SQLite)
- **Notes vocales** (scratchpad) et **statistiques** (mots dictés, temps économisé, activité)

**Confort**
- **Barre flottante discrète** : indicateur d'état animé, personnalisable (position, couleur, taille)
- **Assistant de configuration** : onboarding guidé — permissions, choix du modèle, test micro, tutoriel
- **Démarrage automatique** à l'ouverture de session (optionnel)
- **Hors-ligne** : après le téléchargement initial des modèles, aucune connexion n'est nécessaire

## 💼 Modèle de licence

- **Essai gratuit de 14 jours**, sans carte bancaire
- **Licence à vie** — achat unique sur [voxaho.com](https://voxaho.com), activable/désactivable par machine

---

## 🗂 Architecture

```
localflow/
├── main.py               # Point d'entrée, config, gate licence/trial
├── core/
│   ├── recorder.py       # Capture micro (sounddevice, 16 kHz mono, choix du device)
│   ├── transcriber.py    # Transcription faster-whisper/MLX + reformatage (règles/IA)
│   ├── llm.py            # LLM local (Qwen 2.5 via llama.cpp) — reformatage IA
│   ├── injector.py       # Injection du texte (presse-papiers + collage + restauration)
│   ├── hotkey.py         # Touche globale (CGEventTap macOS / pynput Windows)
│   ├── license.py        # Licence Lemon Squeezy + trial 14 j signé HMAC
│   ├── db.py             # Base SQLite locale (~/.voxaho/voxaho.db, WAL)
│   ├── history.py        # Historique des dictées
│   ├── notes.py          # Notes vocales
│   ├── stats.py          # Statistiques d'usage
│   ├── dictionary.py     # Dictionnaire personnel (termes + correction fuzzy)
│   ├── snippets.py       # Snippets vocaux (déclencheur → expansion)
│   ├── autostart.py      # Lancement à la session (LaunchAgent / registre)
│   └── relaunch.py       # Redémarrage après octroi de permissions
├── ui/
│   ├── floating_bar.py   # Barre flottante animée (états visuels)
│   ├── workspace_window.py # Fenêtre principale à sidebar (Accueil/Historique/Notes/Stats)
│   ├── settings_window.py# Préférences (onglets)
│   └── setup_wizard.py   # Assistant premier lancement
└── tests/                # Suite pytest (300+ tests)
```

**Pipeline de dictée** : touche pressée → enregistrement → touche relâchée → transcription Whisper → reformatage (IA locale ou règles) → dictionnaire + snippets → injection dans l'app active → enregistrement dans l'historique local.

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
