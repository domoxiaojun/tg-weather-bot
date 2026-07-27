#!/usr/bin/env python3
"""Regenerate MarkdownV2 / HTML tables from an existing custom-emoji id map.

Does **not** call Telegram. Use after ids are known::

    uv run python scripts/export_weather_emoji_formats.py

Updates:
  - data/weather_custom_emoji.json   (adds markdown_v2 / html / labels)
  - resources/weather_custom_emoji.json
  - resources/weather_custom_emoji.markdown_v2.txt  (one-line pack dump)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.weather_icons import (  # noqa: E402
    UPLOAD_ICON_CODES,
    build_format_maps,
    export_markdown_v2_pack_line,
)

DATA_MAP = ROOT / "data" / "weather_custom_emoji.json"
BUNDLED_MAP = ROOT / "resources" / "weather_custom_emoji.json"
MDV2_EXPORT = ROOT / "resources" / "weather_custom_emoji.markdown_v2.txt"


def _load_icons(path: Path) -> tuple[dict, dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    icons = raw.get("icons") or {}
    if not isinstance(icons, dict):
        raise SystemExit(f"invalid icons map in {path}")
    # Flat code → id only (ignore nested objects if any).
    flat = {str(k): str(v) for k, v in icons.items() if v and not isinstance(v, dict)}
    return raw, flat


def _write(path: Path, base: dict, icons: dict[str, str]) -> None:
    formats = build_format_maps(icons)
    payload = {
        **{k: v for k, v in base.items() if k not in {"icons", "labels", "emoji", "markdown_v2", "html", "order"}},
        "version": max(2, int(base.get("version") or 1)),
        "order": list(UPLOAD_ICON_CODES),
        "icons": {code: icons[code] for code in UPLOAD_ICON_CODES if code in icons},
        "labels": formats["labels"],
        "emoji": formats["emoji"],
        "markdown_v2": formats["markdown_v2"],
        "html": formats["html"],
    }
    # Preserve any extra icon codes not in UPLOAD list.
    for code, eid in icons.items():
        payload["icons"].setdefault(code, eid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path} ({len(payload['icons'])} icons, markdown_v2={len(payload['markdown_v2'])})")


def main() -> int:
    source = DATA_MAP if DATA_MAP.is_file() else BUNDLED_MAP
    if not source.is_file():
        raise SystemExit(f"no map file at {DATA_MAP} or {BUNDLED_MAP}")
    base, icons = _load_icons(source)
    if not icons:
        raise SystemExit(f"empty icons in {source}")

    _write(DATA_MAP, base, icons)
    _write(BUNDLED_MAP, base, icons)
    MDV2_EXPORT.write_text(export_markdown_v2_pack_line(icons) + "\n", encoding="utf-8")
    print(f"wrote {MDV2_EXPORT}")
    # Preview first few MDV2 fragments
    formats = build_format_maps(icons)
    for code in list(UPLOAD_ICON_CODES)[:5]:
        print(f"  {code}: {formats['markdown_v2'][code]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
