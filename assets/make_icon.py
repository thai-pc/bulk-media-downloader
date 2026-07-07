"""Generate assets/icon.ico for the app (run once; not needed at runtime).

Design: a rounded-square app tile with a vertical indigo→violet gradient, a bold
white download arrow, and a tray line underneath — reads clearly from 256px down
to 16px. Regenerate with:  python assets/make_icon.py
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "icon.ico")
# Master canvas is rendered large then downscaled to each .ico size for crisp
# antialiasing.
MASTER = 1024
SIZES = [256, 128, 64, 48, 32, 16]

TOP = (79, 70, 229)     # indigo  #4F46E5
BOTTOM = (124, 58, 237) # violet  #7C3AED
WHITE = (255, 255, 255, 255)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    """Return an L-mode rounded-rectangle mask."""
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _gradient(size: int) -> Image.Image:
    """Vertical TOP→BOTTOM gradient as an RGBA image."""
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), (
            round(TOP[0] + (BOTTOM[0] - TOP[0]) * t),
            round(TOP[1] + (BOTTOM[1] - TOP[1]) * t),
            round(TOP[2] + (BOTTOM[2] - TOP[2]) * t),
        ))
    return grad.resize((size, size)).convert("RGBA")


def build_master() -> Image.Image:
    s = MASTER
    tile = _gradient(s)
    tile.putalpha(_rounded_mask(s, radius=int(s * 0.22)))

    d = ImageDraw.Draw(tile)
    cx = s // 2

    # Download arrow: a vertical shaft + a downward chevron head.
    shaft_w = int(s * 0.11)
    shaft_top = int(s * 0.22)
    shaft_bot = int(s * 0.55)
    d.rounded_rectangle(
        [cx - shaft_w // 2, shaft_top, cx + shaft_w // 2, shaft_bot],
        radius=shaft_w // 2, fill=WHITE,
    )
    head_half = int(s * 0.20)
    head_top = int(s * 0.46)
    head_tip = int(s * 0.70)
    d.polygon(
        [(cx - head_half, head_top), (cx + head_half, head_top), (cx, head_tip)],
        fill=WHITE,
    )

    # Tray / baseline the arrow points into.
    tray_y = int(s * 0.78)
    tray_h = int(s * 0.055)
    tray_half = int(s * 0.26)
    d.rounded_rectangle(
        [cx - tray_half, tray_y, cx + tray_half, tray_y + tray_h],
        radius=tray_h // 2, fill=WHITE,
    )
    return tile


def main() -> None:
    master = build_master()
    frames = [master.resize((n, n), Image.LANCZOS) for n in SIZES]
    frames[0].save(OUT, format="ICO",
                   sizes=[(n, n) for n in SIZES], append_images=frames[1:])
    print(f"wrote {OUT} with sizes {SIZES}")


if __name__ == "__main__":
    main()
