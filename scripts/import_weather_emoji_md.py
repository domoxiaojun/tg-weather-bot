#!/usr/bin/env python3
"""Build ``data/weather_custom_emoji.json`` from a MarkdownV2 custom-emoji dump.

Workflow
--------
1. Upload the pack in **exactly** :data:`utils.weather_icons.UPLOAD_ICON_CODES`
   order (see ``data/weather_emoji_order.md`` / prepare script ORDER.txt).
2. In Telegram, copy the whole pack as MarkdownV2 custom-emoji entities, e.g.::

       ![☀️](tg://emoji?id=5368324170671202286)![🌤️](tg://emoji?id=…)…

3. Import::

       uv run python scripts/import_weather_emoji_md.py path/to/pack.md
       # or paste on stdin:
       pbpaste | uv run python scripts/import_weather_emoji_md.py -

Also accepts HTML ``<tg-emoji emoji-id="…">`` and bare ``tg://emoji?id=…`` lines.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.weather_icons import UPLOAD_ICON_CODES, WEATHER_ICONS  # noqa: E402

DEFAULT_MAP = ROOT / "data" / "weather_custom_emoji.json"

# MarkdownV2: ![👍](tg://emoji?id=5368324170671202286)
_MD_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\(tg://emoji\?id=(?P<id>\d+)\)",
    re.IGNORECASE,
)
# HTML: <tg-emoji emoji-id="5368…">👍</tg-emoji>
_HTML_RE = re.compile(
    r'<tg-emoji\s+emoji-id=["\'](?P<id>\d+)["\']\s*>(?P<alt>.*?)</tg-emoji>',
    re.IGNORECASE | re.DOTALL,
)
# Bare link / entity dump
_BARE_RE = re.compile(r"tg://emoji\?id=(?P<id>\d+)", re.IGNORECASE)
_HTML_BARE_RE = re.compile(r'emoji-id=["\'](?P<id>\d+)["\']', re.IGNORECASE)


def extract_ids(text: str) -> list[str]:
    """Return custom_emoji_id list in appearance order (dedupe consecutive only)."""
    ids: list[str] = []
    if _MD_RE.search(text):
        ids = [m.group("id") for m in _MD_RE.finditer(text)]
    elif _HTML_RE.search(text):
        ids = [m.group("id") for m in _HTML_RE.finditer(text)]
    else:
        # Prefer tg:// form; fall back to emoji-id attributes.
        ids = [m.group("id") for m in _BARE_RE.finditer(text)]
        if not ids:
            ids = [m.group("id") for m in _HTML_BARE_RE.finditer(text)]

    # Keep first occurrence of each id (pack dumps sometimes repeat).
    seen: set[str] = set()
    ordered: list[str] = []
    for eid in ids:
        if eid not in seen:
            seen.add(eid)
            ordered.append(eid)
    return ordered


def build_map(ids: list[str], codes: tuple[str, ...] = UPLOAD_ICON_CODES) -> dict[str, str]:
    if not ids:
        raise SystemExit("No custom_emoji_id found in input")
    if len(ids) < len(codes):
        raise SystemExit(
            f"Only {len(ids)} emoji ids in dump, but pack order has {len(codes)} codes.\n"
            f"Missing from position {len(ids) + 1}: {', '.join(codes[len(ids):len(ids)+8])}…"
        )
    if len(ids) > len(codes):
        print(
            f"warning: dump has {len(ids)} ids, pack order has {len(codes)}; "
            f"extra ids ignored",
            file=sys.stderr,
        )
    return {code: eid for code, eid in zip(codes, ids)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        help="File path, or '-' to read stdin",
    )
    parser.add_argument(
        "--map",
        type=Path,
        default=DEFAULT_MAP,
        help=f"Output JSON (default: {DEFAULT_MAP})",
    )
    parser.add_argument(
        "--sticker-set-name",
        default="",
        help="Optional sticker set short name to store in the JSON",
    )
    args = parser.parse_args(argv)

    if args.source == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.source).read_text(encoding="utf-8")

    ids = extract_ids(text)
    icons = build_map(ids)
    payload = {
        "version": 1,
        "source": "markdown_v2_import",
        "sticker_set_name": args.sticker_set_name or None,
        "order": list(UPLOAD_ICON_CODES),
        "icons": icons,
        # Helpful for eyeballing: first few code → fallback emoji → id
        "preview": {
            code: {"emoji": WEATHER_ICONS[code], "id": icons[code]}
            for code in list(UPLOAD_ICON_CODES)[:5]
        },
    }
    # Drop null sticker_set_name for a cleaner file.
    if not payload["sticker_set_name"]:
        del payload["sticker_set_name"]

    args.map.parent.mkdir(parents=True, exist_ok=True)
    args.map.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.map} ({len(icons)} icons)")
    for i, code in enumerate(UPLOAD_ICON_CODES[:8], 1):
        print(f"  {i:02d}. {code} {WEATHER_ICONS[code]} → {icons[code]}")
    if len(icons) > 8:
        print(f"  … {len(icons) - 8} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
