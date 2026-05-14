#!/bin/bash
set -e

APP_NAME="Voxaho"
VERSION="1.0.1"
DMG_NAME="${APP_NAME}-${VERSION}.dmg"
BUILD_DIR="dist"
APP_PATH="${BUILD_DIR}/${APP_NAME}.app"

echo ""
echo "📦  Build Voxaho.dmg v${VERSION}"
echo "=================================="

# ── 1. Vérifications ───────────────────────────────────────────────────────
if [ ! -d "venv" ]; then
  echo "❌  Lance d'abord : ./install.sh"
  exit 1
fi
source venv/bin/activate

# Installer py2app si absent
if ! python -c "import py2app" 2>/dev/null; then
  echo "📥  Installation de py2app..."
  pip install py2app --quiet
fi

# Installer Pillow si absent (requis pour la génération d'icône)
if ! python -c "import PIL" 2>/dev/null; then
  echo "📥  Installation de Pillow..."
  pip install Pillow --quiet
fi

# Installer create-dmg si absent
if ! command -v create-dmg &>/dev/null; then
  echo "📥  Installation de create-dmg (Homebrew)..."
  brew install create-dmg
fi

# ── 2. Nettoyer les builds précédents ──────────────────────────────────────
echo ""
echo "🧹  Nettoyage..."
rm -rf build dist

# ── 3. Créer l'icône si absente ────────────────────────────────────────────
if [ ! -f "assets/AppIcon.icns" ]; then
  echo ""
  echo "🎨  Génération de l'icône Voxaho (V monogram)..."
  mkdir -p assets
  python - <<'EOF'
import os

# Génère AppIcon.icns à partir du logo Voxaho :
# V minimaliste blanc + dot vert sur fond rounded square sombre.
try:
    from PIL import Image, ImageDraw
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    iconset = "assets/AppIcon.iconset"
    os.makedirs(iconset, exist_ok=True)

    def render(size: int) -> Image.Image:
        # Render at 4x then downscale for smoothing
        scale = max(1, 1024 // size)
        S = size * scale
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d   = ImageDraw.Draw(img)

        # Rounded square background
        radius = int(S * 0.225)
        d.rounded_rectangle([0, 0, S, S], radius=radius, fill=(7, 7, 13, 255))

        # V monogram : two strokes meeting at apex
        # Coords normalisées (sur base 1024) : (245,240) → (512,745) → (779,240)
        # Stroke 118
        stroke = max(2, int(S * 118 / 1024))
        x1, y1 = int(S * 245 / 1024), int(S * 240 / 1024)
        x2, y2 = int(S * 512 / 1024), int(S * 745 / 1024)
        x3, y3 = int(S * 779 / 1024), int(S * 240 / 1024)
        # Pillow ne fait pas stroke-linejoin=round nativement : on dessine 2 lignes
        # avec stroke-cap implicite + un cercle au joint pour adoucir
        d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 255), width=stroke)
        d.line([(x3, y3), (x2, y2)], fill=(255, 255, 255, 255), width=stroke)
        r_cap = stroke // 2
        # caps + apex join
        for cx, cy in [(x1, y1), (x3, y3), (x2, y2)]:
            d.ellipse([cx - r_cap, cy - r_cap, cx + r_cap, cy + r_cap],
                      fill=(255, 255, 255, 255))

        # Green dot nichée dans l'ouverture du V
        dot_r = int(S * 40 / 1024)
        cx, cy = int(S * 512 / 1024), int(S * 385 / 1024)
        d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
                  fill=(48, 209, 88, 255))

        if scale > 1:
            img = img.resize((size, size), Image.LANCZOS)
        return img

    for s in sizes:
        img = render(s)
        img.save(f"{iconset}/icon_{s}x{s}.png")
        if s <= 512:
            img2x = render(s * 2)
            img2x.save(f"{iconset}/icon_{s}x{s}@2x.png")

    os.system(f"iconutil -c icns {iconset} -o assets/AppIcon.icns")
    print("   ✓ AppIcon.icns générée (V monogram)")
except ImportError:
    print("   ❌ Pillow absent : impossible de générer l'icône.")
    print("      Installe-le avec : pip install Pillow")
    raise SystemExit(1)
EOF
fi

# ── 4. Builder l'app ───────────────────────────────────────────────────────
echo ""
echo "🔨  Compilation de ${APP_NAME}.app..."
python setup.py py2app 2>&1 | tail -20

if [ ! -d "$APP_PATH" ]; then
  echo "❌  La compilation a échoué. Voir les logs ci-dessus."
  exit 1
fi
echo "   ✓ ${APP_PATH} créé ($(du -sh "$APP_PATH" | cut -f1))"

# ── 5. Créer le DMG ────────────────────────────────────────────────────────
echo ""
echo "💿  Création du DMG..."

create-dmg \
  --volname "${APP_NAME}" \
  --volicon "assets/AppIcon.icns" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 128 \
  --icon "${APP_NAME}.app" 150 185 \
  --hide-extension "${APP_NAME}.app" \
  --app-drop-link 450 185 \
  --background "assets/dmg_background.png" \
  --no-internet-enable \
  "${DMG_NAME}" \
  "${BUILD_DIR}/"

echo ""
echo "✅  DMG créé : ${DMG_NAME} ($(du -sh "$DMG_NAME" | cut -f1))"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Prochaines étapes pour distribuer :"
echo ""
echo "  Option A — Distribution directe (recommandé v1) :"
echo "    1. Héberge ${DMG_NAME} sur ton serveur / GitHub Releases"
echo "    2. Mets le lien de téléchargement dans landing/index.html"
echo ""
echo "  Option B — Notarisation Apple (optionnel) :"
echo "    1. xcrun notarytool submit ${DMG_NAME} --apple-id TON_ID"
echo "    2. xcrun stapler staple ${DMG_NAME}"
echo "    → Supprime l'avertissement 'développeur non identifié'"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
