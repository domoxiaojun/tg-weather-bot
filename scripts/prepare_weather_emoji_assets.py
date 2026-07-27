#!/usr/bin/env python3
"""Download QWeather SVG icons and rasterise them to 100×100 PNG for custom emoji.

Telegram static custom emoji requirements (core.telegram.org/stickers):
  - PNG or WEBP, square, **100×100** pixels, transparency allowed.

Dependencies (not installed by default — confirm with the operator first):
  - system Cairo (e.g. ``brew install cairo pkg-config``)
  - ``cairosvg`` + Pillow  (``uv pip install --python .venv/bin/python cairosvg``)

Usage::

    uv run python scripts/prepare_weather_emoji_assets.py
    uv run python scripts/prepare_weather_emoji_assets.py --codes 100,301,400
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.weather_icons import UPLOAD_ICON_CODES, WEATHER_ICONS  # noqa: E402

# MIT-licensed QWeather Icons (https://icons.qweather.com / qwd/Icons).
CDN = "https://cdn.jsdelivr.net/npm/qweather-icons@1.6.0/icons/{code}.svg"
OUT_DIR = ROOT / "data" / "weather_emoji_assets"
SIZE = 100


def _require_cairosvg():
    try:
        import cairosvg  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Missing cairosvg/Pillow. Install after confirmation:\n"
            "  brew install cairo pkg-config   # macOS\n"
            "  uv pip install --python .venv/bin/python cairosvg\n"
            f"(import error: {exc})"
        ) from exc
    return cairosvg, Image


def rasterise_svg(svg_bytes: bytes, size: int = SIZE) -> bytes:
    cairosvg, Image = _require_cairosvg()
    png = cairosvg.svg2png(
        bytestring=svg_bytes,
        output_width=size,
        output_height=size,
        background_color="rgba(0,0,0,0)",
    )
    # Force exact square + RGBA; pad if the SVG viewBox is not 1:1.
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


def download_svg(client: httpx.Client, code: str) -> bytes:
    url = CDN.format(code=code)
    resp = client.get(url)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} for {url}")
    return resp.content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codes",
        default="",
        help="Comma-separated QWeather codes (default: all WEATHER_ICONS codes)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR,
        help=f"Output directory (default: {OUT_DIR})",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Do not re-download codes that already have a PNG",
    )
    args = parser.parse_args(argv)

    codes = (
        [c.strip() for c in args.codes.split(",") if c.strip()]
        if args.codes.strip()
        else list(UPLOAD_ICON_CODES)
    )
    unknown = [c for c in codes if c not in WEATHER_ICONS]
    if unknown:
        print(f"warning: codes not in WEATHER_ICONS fallback table: {unknown}", file=sys.stderr)

    # Fail fast on missing converter before downloading dozens of files.
    _require_cairosvg()

    args.out.mkdir(parents=True, exist_ok=True)
    ok = fail = skip = 0
    # Numbered copies so manual Stickers-bot upload can follow pack order 01…N.
    order_lines = [
        "# Weather custom-emoji upload order (do not reshuffle)",
        f"# total={len(codes)}",
        "# index | code | fallback_emoji | file",
    ]
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for index, code in enumerate(codes, start=1):
            dest = args.out / f"{code}.png"
            numbered = args.out / f"{index:02d}_{code}.png"
            if args.skip_existing and dest.is_file():
                skip += 1
                emoji = WEATHER_ICONS.get(code, "?")
                order_lines.append(f"{index:02d} | {code} | {emoji} | {dest.name}")
                if dest.is_file() and not numbered.is_file():
                    numbered.write_bytes(dest.read_bytes())
                continue
            try:
                svg = download_svg(client, code)
                png = rasterise_svg(svg)
                dest.write_bytes(png)
                numbered.write_bytes(png)
                emoji = WEATHER_ICONS.get(code, "?")
                order_lines.append(f"{index:02d} | {code} | {emoji} | {numbered.name}")
                print(f"ok  {index:02d} {code} {emoji} → {numbered.name} ({len(png)} B)")
                ok += 1
            except Exception as exc:  # noqa: BLE001 — surface per-icon failures
                print(f"FAIL {index:02d} {code}: {exc}", file=sys.stderr)
                fail += 1

    order_path = args.out / "ORDER.txt"
    order_path.write_text("\n".join(order_lines) + "\n", encoding="utf-8")
    # Repo-level human checklist (always rewrite from canonical codes).
    checklist = ROOT / "data" / "weather_emoji_order.md"
    checklist.parent.mkdir(parents=True, exist_ok=True)
    md = [
        "# 和风自定义 Emoji 上传顺序",
        "",
        "按下面序号 **从 1 到 N** 依次加入表情包。导入 MarkdownV2 时也会按同一顺序对齐 code。",
        "",
        "| # | code | fallback |",
        "| --: | --- | --- |",
    ]
    for index, code in enumerate(UPLOAD_ICON_CODES if not args.codes.strip() else codes, start=1):
        md.append(f"| {index} | `{code}` | {WEATHER_ICONS.get(code, '?')} |")
    md.append("")
    checklist.write_text("\n".join(md), encoding="utf-8")

    print(f"\ndone: {ok} written, {skip} skipped, {fail} failed → {args.out}")
    print(f"order: {order_path}")
    print(f"checklist: {checklist}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
