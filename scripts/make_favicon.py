"""Generate compact egg-shaped app icons for Kostolany Watch."""
from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "public"


def make(size: int, path: Path) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = size * 0.04
    d.ellipse([pad, pad, size - pad, size - pad], fill=(232, 239, 230, 255))

    cx, cy = size / 2, size / 2 + size * 0.015
    rx, ry = size * 0.28, size * 0.375

    for i in range(24):
        t = i / 23
        r = int(244 + (197 - 244) * t)
        g = int(240 + (212 - 240) * t)
        b = int(230 + (198 - 230) * t)
        shrink = t * 0.08
        d.ellipse(
            [
                cx - rx * (1 - shrink),
                cy - ry * (1 - shrink) + t * ry * 0.15,
                cx + rx * (1 - shrink),
                cy + ry * (1 - shrink) + t * ry * 0.05,
            ],
            fill=(r, g, b, 255),
        )

    stroke = max(2, int(size * 0.035))
    steps = 72
    dash_on = True
    for i in range(steps):
        a0 = 2 * math.pi * i / steps
        a1 = 2 * math.pi * (i + 1) / steps
        if dash_on:
            x0 = cx + rx * math.cos(a0)
            y0 = cy + ry * math.sin(a0)
            x1 = cx + rx * math.cos(a1)
            y1 = cy + ry * math.sin(a1)
            d.line(
                [(x0, y0), (x1, y1)],
                fill=(47, 93, 80, 90),
                width=max(1, stroke // 2),
            )
        dash_on = not dash_on

    outer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    od = ImageDraw.Draw(outer)
    od.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], outline=(30, 61, 53, 255), width=stroke)

    hx, hy = cx - rx * 0.25, cy - ry * 0.35
    hr = rx * 0.45
    d.ellipse(
        [hx - hr, hy - hr * 0.7, hx + hr, hy + hr * 0.7],
        fill=(255, 255, 255, 70),
    )

    img = Image.alpha_composite(img, outer)
    d = ImageDraw.Draw(img)

    ang = -0.55
    px = cx + rx * math.cos(ang)
    py = cy + ry * math.sin(ang)
    pr = size * 0.055
    d.ellipse(
        [px - pr, py - pr, px + pr, py + pr],
        fill=(196, 92, 62, 255),
        outline=(30, 61, 53, 255),
        width=max(1, stroke // 2),
    )
    ir = pr * 0.35
    d.ellipse([px - ir, py - ir, px + ir, py + ir], fill=(247, 243, 234, 255))

    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    print(path.name, img.size, os.path.getsize(path))


if __name__ == "__main__":
    make(180, OUT / "apple-touch-icon.png")
    make(32, OUT / "favicon-32.png")
