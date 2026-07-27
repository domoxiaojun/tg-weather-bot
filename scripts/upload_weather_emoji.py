#!/usr/bin/env python3
"""Upload prepared weather PNGs as a Telegram *custom emoji* sticker set.

Prerequisites
-------------
1. Bot owner has **Telegram Premium** (Bot API 9.4: custom emoji in
   private / group / supergroup messages).
2. Owner has messaged the bot at least once (``/start``).
3. PNGs prepared by ``scripts/prepare_weather_emoji_assets.py``
   (100×100, under ``data/weather_emoji_assets/``).
4. Env: ``BOT_TOKEN``, ``SUPER_ADMIN_ID`` (owner user id).

Usage::

    uv run python scripts/upload_weather_emoji.py
    uv run python scripts/upload_weather_emoji.py --name weather_icons_by_MyBot

Writes ``data/weather_custom_emoji.json``::

    {"version": 1, "sticker_set_name": "...", "icons": {"100": "<id>", ...}}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telegram import Bot, InputSticker
from telegram.constants import StickerFormat
from telegram.error import BadRequest, TelegramError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.weather_icons import UPLOAD_ICON_CODES, WEATHER_ICONS  # noqa: E402

ASSETS = ROOT / "data" / "weather_emoji_assets"
DEFAULT_MAP = ROOT / "data" / "weather_custom_emoji.json"


def _load_env() -> tuple[str, int]:
    load_dotenv(ROOT / ".env")
    token = os.environ.get("BOT_TOKEN", "").strip()
    owner = os.environ.get("SUPER_ADMIN_ID", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN missing (.env)")
    if not owner.isdigit():
        raise SystemExit("SUPER_ADMIN_ID must be the bot owner's numeric user id")
    return token, int(owner)


async def _sticker_ids(bot: Bot, name: str) -> set[str]:
    try:
        sticker_set = await bot.get_sticker_set(name)
    except TelegramError:
        return set()
    return {s.custom_emoji_id for s in sticker_set.stickers if s.custom_emoji_id}


async def _upload_file(bot: Bot, owner_id: int, path: Path) -> str:
    with path.open("rb") as fh:
        uploaded = await bot.upload_sticker_file(
            user_id=owner_id,
            sticker=fh,
            sticker_format=StickerFormat.STATIC,
        )
    return uploaded.file_id


def _input_sticker(file_id: str, code: str) -> InputSticker:
    emoji = WEATHER_ICONS.get(code, "☀️")
    return InputSticker(
        sticker=file_id,
        format=StickerFormat.STATIC,
        emoji_list=[emoji],
        keywords=[code, f"qweather{code}"],
    )


async def _upload(name: str, title: str, map_path: Path, assets: Path) -> int:
    token, owner_id = _load_env()
    bot = Bot(token)

    async with bot:
        me = await bot.get_me()
        username = (me.username or "").lower()
        if not username:
            raise SystemExit("Bot has no username; set one in BotFather first")

        suffix = f"_by_{username}"
        if not name.endswith(suffix):
            base = name.split("_by_")[0]
            name = f"{base}{suffix}"
        print(f"bot=@{username} owner={owner_id} set={name}")

        pngs = [(code, assets / f"{code}.png") for code in UPLOAD_ICON_CODES if (assets / f"{code}.png").is_file()]
        if not pngs:
            raise SystemExit(f"No PNGs in {assets}. Run prepare_weather_emoji_assets.py first.")

        icons: dict[str, str] = {}
        if map_path.is_file():
            try:
                raw = json.loads(map_path.read_text(encoding="utf-8"))
                icons = {str(k): str(v) for k, v in (raw.get("icons") or raw).items() if v}
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                icons = {}

        set_exists = bool(await _sticker_ids(bot, name))

        for index, (code, path) in enumerate(pngs):
            if code in icons:
                print(f"keep {code} → {icons[code]}")
                continue

            before = await _sticker_ids(bot, name)
            try:
                file_id = await _upload_file(bot, owner_id, path)
                sticker = _input_sticker(file_id, code)
                if not set_exists and index == 0 and not before:
                    await bot.create_new_sticker_set(
                        user_id=owner_id,
                        name=name,
                        title=title,
                        stickers=[sticker],
                        sticker_type="custom_emoji",
                    )
                    set_exists = True
                    print(f"created set with {code}")
                else:
                    await bot.add_sticker_to_set(user_id=owner_id, name=name, sticker=sticker)
                    print(f"added {code}")
            except BadRequest as exc:
                print(f"skip {code}: {exc}")
                continue

            after = await _sticker_ids(bot, name)
            new_ids = after - before
            if len(new_ids) == 1:
                icons[code] = next(iter(new_ids))
                print(f"map {code} → {icons[code]}")
            elif not new_ids:
                print(f"WARNING: no new custom_emoji_id for {code}", file=sys.stderr)
            else:
                # Ambiguous (rare); take an arbitrary new id and hope.
                icons[code] = sorted(new_ids)[0]
                print(f"map {code} → {icons[code]} (ambiguous +{len(new_ids)})")

        payload = {
            "version": 1,
            "sticker_set_name": name,
            "sticker_set_title": title,
            "bot_username": username,
            "icons": dict(sorted(icons.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0)),
        }
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {map_path} ({len(icons)} icons)")

        missing = [c for c, _ in pngs if c not in icons]
        if missing:
            print(f"WARNING: still unmapped: {missing}", file=sys.stderr)
            return 2
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name",
        default="qweather_icons",
        help="Sticker set short name (auto-suffixed with _by_<bot>)",
    )
    parser.add_argument("--title", default="QWeather Icons", help="Sticker set title")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP, help="Output JSON map path")
    parser.add_argument("--assets", type=Path, default=ASSETS, help="PNG assets directory")
    args = parser.parse_args(argv)
    return asyncio.run(_upload(args.name, args.title, args.map, args.assets))


if __name__ == "__main__":
    raise SystemExit(main())
