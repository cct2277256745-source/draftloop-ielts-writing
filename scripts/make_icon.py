"""Generate the DraftLoop macOS application icon."""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "app" / "resources"
ICONSET = RES / "app.iconset"
ICNS = RES / "app.icns"
PNG = RES / "app_icon.png"
INK = (38, 42, 44, 255)
CREAM = (244, 239, 230, 255)
S = 1024
SS = 4


def build_master() -> Image.Image:
    size = S * SS
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.225), fill=255
    )
    image.paste(Image.new("RGBA", (size, size), INK), (0, 0), mask)
    draw = ImageDraw.Draw(image)
    width = 64 * SS
    points = [(300 * SS, 540 * SS), (455 * SS, 695 * SS), (735 * SS, 360 * SS)]
    draw.line(points[:2], fill=CREAM, width=width, joint="curve")
    draw.line(points[1:], fill=CREAM, width=width, joint="curve")
    radius = width // 2
    for x, y in (points[0], points[-1]):
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=CREAM)
    return image.resize((S, S), Image.Resampling.LANCZOS)


def main() -> None:
    master = build_master()
    master.save(PNG)
    ICONSET.mkdir(parents=True, exist_ok=True)
    for child in ICONSET.iterdir():
        child.unlink()
    specs = [
        (16, "icon_16x16.png"), (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"), (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"), (1024, "icon_512x512@2x.png"),
    ]
    for size, name in specs:
        master.resize((size, size), Image.Resampling.LANCZOS).save(ICONSET / name)
    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    print(f"✓ 图标已生成：{ICNS}")


if __name__ == "__main__":
    main()
