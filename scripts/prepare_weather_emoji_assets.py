#!/usr/bin/env python3
"""Download QWeather SVG icons, **colorize** them, rasterise to 100×100 PNG.

Official SVGs use ``fill="currentColor"`` (web inherits text color). Without a
colour, cairosvg renders pure black — that was the bug in the first pack.

Telegram static custom emoji: PNG/WEBP, square **100×100**, transparency OK.

Usage::

    uv run python scripts/prepare_weather_emoji_assets.py
    uv run python scripts/prepare_weather_emoji_assets.py --prefer-fill
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.weather_icons import (  # noqa: E402
    UPLOAD_ICON_CODES,
    WEATHER_ICON_LABELS,
    WEATHER_ICONS,
    color_for_icon_code,
)

CDN = "https://cdn.jsdelivr.net/npm/qweather-icons@1.6.0/icons/{name}.svg"
OUT_DIR = ROOT / "data" / "weather_emoji_assets"
SIZE = 100


def _require_cairosvg():
    try:
        import cairosvg  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Missing cairosvg/Pillow. Install after confirmation:\n"
            "  brew install cairo pkg-config\n"
            "  uv pip install --python .venv/bin/python cairosvg\n"
            f"(import error: {exc})"
        ) from exc
    return cairosvg, Image


def colorize_svg(svg_bytes: bytes, color: str) -> bytes:
    """Replace currentColor / bare fill defaults with an explicit hex colour."""
    text = svg_bytes.decode("utf-8", errors="replace")
    # Official icons use fill="currentColor" on the root <svg>.
    text = text.replace('fill="currentColor"', f'fill="{color}"')
    text = text.replace("fill='currentColor'", f"fill='{color}'")
    # Some paths omit fill and inherit — force on root svg element.
    if f'fill="{color}"' not in text and "fill=" not in text[:200]:
        text = re.sub(r"<svg\b", f'<svg fill="{color}"', text, count=1)
    return text.encode("utf-8")


def rasterise_svg(svg_bytes: bytes, *, color: str, size: int = SIZE) -> bytes:
    cairosvg, Image = _require_cairosvg()
    colored = colorize_svg(svg_bytes, color)
    png = cairosvg.svg2png(
        bytestring=colored,
        output_width=size,
        output_height=size,
        background_color="rgba(0,0,0,0)",
    )
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    if img.size != (size, size):
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        ox = (size - img.width) // 2
        oy = (size - img.height) // 2
        canvas.paste(img, (ox, oy), img)
        img = canvas
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def download_svg(client: httpx.Client, code: str, *, prefer_fill: bool) -> tuple[bytes, str]:
    names = [f"{code}-fill", code] if prefer_fill else [code, f"{code}-fill"]
    last_status = None
    for name in names:
        url = CDN.format(name=name)
        resp = client.get(url)
        last_status = resp.status_code
        if resp.status_code == 200 and resp.content:
            return resp.content, name
    raise RuntimeError(f"HTTP {last_status} for icon {code}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codes", default="", help="Comma-separated codes (default: UPLOAD_ICON_CODES)")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--prefer-fill", action="store_true", default=True, help="Prefer *-fill.svg shapes")
    parser.add_argument("--outline", action="store_true", help="Use outline SVG instead of fill")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args(argv)
    prefer_fill = not args.outline

    codes = (
        [c.strip() for c in args.codes.split(",") if c.strip()]
        if args.codes.strip()
        else list(UPLOAD_ICON_CODES)
    )

    _require_cairosvg()
    args.out.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    order_lines = [
        "# Colored QWeather custom-emoji assets",
        f"# total={len(codes)} prefer_fill={prefer_fill}",
        "# index | code | label | color | file",
    ]

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for index, code in enumerate(codes, start=1):
            dest = args.out / f"{code}.png"
            numbered = args.out / f"{index:02d}_{code}.png"
            color = color_for_icon_code(code)
            label = WEATHER_ICON_LABELS.get(code, code)
            if args.skip_existing and dest.is_file():
                skip += 1
                order_lines.append(f"{index:02d} | {code} | {label} | {color} | {dest.name}")
                continue
            try:
                svg, name = download_svg(client, code, prefer_fill=prefer_fill)
                png = rasterise_svg(svg, color=color)
                dest.write_bytes(png)
                numbered.write_bytes(png)
                order_lines.append(f"{index:02d} | {code} | {label} | {color} | {numbered.name} ({name})")
                print(f"ok  {index:03d} {code} {label} {color} ← {name} ({len(png)} B)")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {index:03d} {code}: {exc}", file=sys.stderr)
                fail += 1

    (args.out / "ORDER.txt").write_text("\n".join(order_lines) + "\n", encoding="utf-8")
    # Human checklist
    checklist = ROOT / "data" / "weather_emoji_order.md"
    md = [
        "# 和风自定义 Emoji 上传顺序与语义（彩色）",
        "",
        "按序号 **1→N** 上传；MarkdownV2 导入按同一顺序对齐 code。",
        "",
        "| # | code | 语义 | fallback | 色值 |",
        "| --: | --- | --- | --- | --- |",
    ]
    for index, code in enumerate(codes, start=1):
        md.append(
            f"| {index} | `{code}` | {WEATHER_ICON_LABELS.get(code, code)} | "
            f"{WEATHER_ICONS.get(code, '❓')} | `{color_for_icon_code(code)}` |"
        )
    md.append("")
    checklist.write_text("\n".join(md), encoding="utf-8")
    print(f"\ndone: {ok} written, {skip} skipped, {fail} failed → {args.out}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
