"""
Génère assets/AppIcon.ico (multi-résolution) pour le build Windows PyInstaller.
Dessine le logo Voxaho (V monogram + dot vert sur fond rounded square sombre)
en Python pur via Pillow — pas besoin de rsvg-convert ou autre outil natif.
"""

import os
from PIL import Image, ImageDraw


def render(size: int) -> Image.Image:
    """Rend le logo Voxaho à la taille donnée (carré, RGBA, transparent)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)

    # Fond rounded square sombre
    radius = int(size * 0.225)
    d.rounded_rectangle([0, 0, size, size], radius=radius, fill=(7, 7, 13, 255))

    # V monogram (deux traits blancs)
    stroke = max(2, int(size * 118 / 1024))
    x1, y1 = int(size * 245 / 1024), int(size * 240 / 1024)
    x2, y2 = int(size * 512 / 1024), int(size * 745 / 1024)
    x3, y3 = int(size * 779 / 1024), int(size * 240 / 1024)
    d.line([(x1, y1), (x2, y2)], fill=(255, 255, 255, 255), width=stroke)
    d.line([(x3, y3), (x2, y2)], fill=(255, 255, 255, 255), width=stroke)

    # Caps + apex arrondis
    r = stroke // 2
    for cx, cy in [(x1, y1), (x3, y3), (x2, y2)]:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))

    # Dot vert dans l'ouverture du V
    dot_r = int(size * 40 / 1024)
    cx, cy = int(size * 512 / 1024), int(size * 385 / 1024)
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
              fill=(48, 209, 88, 255))

    return img


def main() -> None:
    os.makedirs("assets", exist_ok=True)

    sizes = [16, 32, 48, 64, 128, 256]
    # Pillow génère le .ico depuis la PLUS GRANDE image et downscale en interne
    # pour chaque taille demandée. Donner la 256×256 = qualité max à tous les niveaux.
    biggest = render(256)
    biggest.save(
        "assets/AppIcon.ico",
        format="ICO",
        sizes=[(s, s) for s in sizes],
    )
    print(f"OK — assets/AppIcon.ico ({len(sizes)} resolutions, source 256x256)")


if __name__ == "__main__":
    main()
