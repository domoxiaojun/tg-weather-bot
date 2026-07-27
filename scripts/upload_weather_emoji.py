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

Re-run is safe: progress is saved after each icon; already-mapped codes and
stickers already present in the set (by pack order) are skipped.
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
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.request import HTTPXRequest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.weather_icons import UPLOAD_ICON_CODES, WEATHER_ICONS  # noqa: E402

ASSETS = ROOT / "data" / "weather_emoji_assets"
DEFAULT_MAP = ROOT / "data" / "weather_custom_emoji.json"

# Telegram media uploads can be slow from some networks.
_CONNECT_TIMEOUT = 30.0
_READ_TIMEOUT = 120.0
_WRITE_TIMEOUT = 120.0
_POOL_TIMEOUT = 30.0
_MAX_ATTEMPTS = 5
_PAUSE_BETWEEN = 0.35


def _load_env() -> tuple[str, int]:
    load_dotenv(ROOT / ".env")
    token = os.environ.get("BOT_TOKEN", "").strip()
    owner = os.environ.get("SUPER_ADMIN_ID", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN missing (.env)")
    if not owner.isdigit():
        raise SystemExit("SUPER_ADMIN_ID must be the bot owner's numeric user id")
    return token, int(owner)


def _save_map(
    map_path: Path,
    *,
    name: str,
    title: str,
    username: str,
    icons: dict[str, str],
) -> None:
    payload = {
        "version": 1,
        "sticker_set_name": name,
        "sticker_set_title": title,
        "bot_username": username,
        "order": list(UPLOAD_ICON_CODES),
        "icons": {
            code: icons[code]
            for code in UPLOAD_ICON_CODES
            if code in icons
        },
    }
    # Keep any unexpected keys after the canonical ordered block.
    for code, eid in sorted(icons.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        payload["icons"].setdefault(code, eid)
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _retry(label: str, coro_factory, attempts: int = _MAX_ATTEMPTS):
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except RetryAfter as exc:
            wait = float(exc.retry_after) + 0.5
            print(f"  rate-limit on {label}, sleep {wait:.1f}s", file=sys.stderr)
            await asyncio.sleep(wait)
            last = exc
        except (TimedOut, NetworkError) as exc:
            wait = min(2 ** attempt, 20)
            print(f"  {type(exc).__name__} on {label} (try {attempt}/{attempts}), sleep {wait}s", file=sys.stderr)
            await asyncio.sleep(wait)
            last = exc
    assert last is not None
    raise last


async def _get_set(bot: Bot, name: str):
    try:
        return await _retry("getStickerSet", lambda: bot.get_sticker_set(name))
    except TelegramError:
        return None


async def _sticker_ids(bot: Bot, name: str) -> set[str]:
    sticker_set = await _get_set(bot, name)
    if sticker_set is None:
        return set()
    return {s.custom_emoji_id for s in sticker_set.stickers if s.custom_emoji_id}


async def _upload_file(bot: Bot, owner_id: int, path: Path) -> str:
    async def once():
        with path.open("rb") as fh:
            uploaded = await bot.upload_sticker_file(
                user_id=owner_id,
                sticker=fh,
                sticker_format=StickerFormat.STATIC,
            )
        return uploaded.file_id

    return await _retry(f"upload {path.name}", once)


def _input_sticker(file_id: str, code: str) -> InputSticker:
    emoji = WEATHER_ICONS.get(code, "☀️")
    return InputSticker(
        sticker=file_id,
        format=StickerFormat.STATIC,
        emoji_list=[emoji],
        keywords=[code, f"qweather{code}"],
    )


def _hydrate_from_set_order(sticker_set, icons: dict[str, str]) -> int:
    """Map stickers[i] → UPLOAD_ICON_CODES[i] when the pack was built in order."""
    filled = 0
    for index, sticker in enumerate(sticker_set.stickers):
        if index >= len(UPLOAD_ICON_CODES):
            break
        code = UPLOAD_ICON_CODES[index]
        eid = sticker.custom_emoji_id
        if not eid:
            continue
        if code not in icons:
            icons[code] = eid
            filled += 1
        elif icons[code] != eid:
            # Prefer live set order if local map disagrees on prefix.
            icons[code] = eid
            filled += 1
    return filled


async def _upload(name: str, title: str, map_path: Path, assets: Path) -> int:
    token, owner_id = _load_env()
    request = HTTPXRequest(
        connection_pool_size=4,
        connect_timeout=_CONNECT_TIMEOUT,
        read_timeout=_READ_TIMEOUT,
        write_timeout=_WRITE_TIMEOUT,
        pool_timeout=_POOL_TIMEOUT,
    )
    bot = Bot(token, request=request)

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

        pngs = [
            (code, assets / f"{code}.png")
            for code in UPLOAD_ICON_CODES
            if (assets / f"{code}.png").is_file()
        ]
        if not pngs:
            raise SystemExit(f"No PNGs in {assets}. Run prepare_weather_emoji_assets.py first.")

        icons: dict[str, str] = {}
        if map_path.is_file():
            try:
                raw = json.loads(map_path.read_text(encoding="utf-8"))
                icons = {str(k): str(v) for k, v in (raw.get("icons") or raw).items() if v}
            except (OSError, json.JSONDecodeError, TypeError, AttributeError):
                icons = {}

        existing = await _get_set(bot, name)
        set_exists = existing is not None
        if existing is not None:
            n = _hydrate_from_set_order(existing, icons)
            if n:
                print(f"hydrated {n} ids from existing set order ({len(existing.stickers)} stickers)")
            _save_map(map_path, name=name, title=title, username=username, icons=icons)

        for index, (code, path) in enumerate(pngs):
            if code in icons:
                print(f"keep {code} → {icons[code]}")
                continue

            before = await _sticker_ids(bot, name)
            try:
                file_id = await _upload_file(bot, owner_id, path)
                sticker = _input_sticker(file_id, code)

                async def add_or_create(sticker=sticker, code=code, before=before):
                    if not set_exists and not before:
                        await bot.create_new_sticker_set(
                            user_id=owner_id,
                            name=name,
                            title=title,
                            stickers=[sticker],
                            sticker_type="custom_emoji",
                        )
                        return "created"
                    await bot.add_sticker_to_set(user_id=owner_id, name=name, sticker=sticker)
                    return "added"

                action = await _retry(f"add {code}", add_or_create)
                set_exists = True
                print(f"{action} {code}")
            except BadRequest as exc:
                print(f"skip {code}: {exc}")
                # Maybe already present — rehydrate by order.
                refreshed = await _get_set(bot, name)
                if refreshed is not None:
                    _hydrate_from_set_order(refreshed, icons)
                    _save_map(map_path, name=name, title=title, username=username, icons=icons)
                continue

            after = await _sticker_ids(bot, name)
            new_ids = after - before
            if len(new_ids) == 1:
                icons[code] = next(iter(new_ids))
                print(f"map {code} → {icons[code]}")
            elif not new_ids:
                refreshed = await _get_set(bot, name)
                if refreshed is not None:
                    _hydrate_from_set_order(refreshed, icons)
                if code not in icons:
                    print(f"WARNING: no new custom_emoji_id for {code}", file=sys.stderr)
            else:
                icons[code] = sorted(new_ids)[0]
                print(f"map {code} → {icons[code]} (ambiguous +{len(new_ids)})")

            _save_map(map_path, name=name, title=title, username=username, icons=icons)
            await asyncio.sleep(_PAUSE_BETWEEN)

        _save_map(map_path, name=name, title=title, username=username, icons=icons)
        print(f"wrote {map_path} ({len(icons)} icons)")

        missing = [c for c, _ in pngs if c not in icons]
        if missing:
            print(f"WARNING: still unmapped: {missing}", file=sys.stderr)
            return 2
        print("done: full pack mapped")
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
