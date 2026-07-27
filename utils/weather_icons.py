"""QWeather icon codes → Unicode emoji / Telegram custom emoji.

QWeather returns numeric icon codes (``100``, ``301``, …). Caiyun is normalised
to the same codes in the adapter. At render time we prefer a custom emoji id
from ``data/weather_custom_emoji.json`` (produced by
``scripts/upload_weather_emoji.py``); otherwise we fall back to the Unicode
emoji table so every surface keeps working without a sticker pack.
"""

from __future__ import annotations

import json
from functools import lru_cache
from html import escape as html_escape
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from loguru import logger

# RichText-compatible fragment: plain string or a typed rich dict / list.
RichIcon = Union[str, dict, list]

# QWeather icon code → Unicode fallback (also used as custom-emoji alternative_text).
WEATHER_ICONS: dict[str, str] = {
    "100": "☀️", "101": "🌤️", "102": "☁️", "103": "🌥️", "104": "⛅",
    "150": "🌙", "151": "🌤️", "152": "☁️", "153": "🌥️",
    "300": "🌦️", "301": "🌧️", "302": "🌧️", "303": "⛈️", "304": "🌦️",
    "305": "🌧️", "306": "🌧️", "307": "⛈️", "308": "🌧️", "309": "🌦️",
    "310": "🌧️", "311": "🌧️", "312": "⛈️", "313": "🌧️", "314": "🌧️",
    "315": "⛈️", "316": "🌧️", "317": "🌧️", "318": "⛈️",
    "350": "🌨️", "351": "🌨️", "399": "🌨️",
    "400": "❄️", "401": "❄️", "402": "❄️", "403": "❄️", "404": "🌨️",
    "405": "❄️", "406": "❄️", "407": "❄️", "408": "❄️🌨️", "409": "❄️🌨️", "410": "❄️🌨️",
    "456": "🌪️", "457": "🌪️", "499": "❓",
    "500": "⛈️", "501": "⛈️", "502": "⛈️", "503": "⛈️", "504": "⛈️",
    "507": "⛈️🌨️", "508": "⛈️🌨️", "509": "⚡", "510": "⚡", "511": "⚡",
    "512": "⚡", "513": "⚡", "514": "⚡", "515": "⚡",
    "800": "☀️", "801": "🌤️", "802": "☁️", "803": "☁️", "804": "☁️",
    "805": "🌫️", "806": "🌫️", "807": "🌧️",
    "900": "🌪️", "901": "🌀", "999": "❓",
}

# Codes we actually ship in the custom-emoji pack (matches WEATHER_ICONS).
UPLOAD_ICON_CODES: tuple[str, ...] = tuple(sorted(WEATHER_ICONS.keys(), key=lambda c: int(c)))

_DEFAULT_MAP_PATH = Path("data/weather_custom_emoji.json")
_map_override: Optional[Mapping[str, str]] = None


def _settings_enabled() -> bool:
    try:
        from core.config import settings
        return bool(getattr(settings, "enable_custom_weather_emoji", True))
    except Exception:
        return True


def _map_path() -> Path:
    try:
        from core.config import settings
        raw = getattr(settings, "weather_custom_emoji_map_path", None) or str(_DEFAULT_MAP_PATH)
        return Path(raw)
    except Exception:
        return _DEFAULT_MAP_PATH


@lru_cache(maxsize=1)
def _load_custom_emoji_map() -> dict[str, str]:
    """Load code → custom_emoji_id mapping from disk (cached)."""
    path = _map_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load weather custom emoji map from {}: {}", path, exc)
        return {}

    if not isinstance(data, dict):
        logger.warning("Weather custom emoji map must be a JSON object: {}", path)
        return {}

    # Accept either flat {"100": "id"} or {"icons": {"100": "id"}, ...}.
    icons = data.get("icons") if "icons" in data else data
    if not isinstance(icons, dict):
        return {}

    out: dict[str, str] = {}
    for key, value in icons.items():
        code = str(key).strip()
        eid = str(value).strip() if value is not None else ""
        if code and eid:
            out[code] = eid
    return out


def reload_custom_emoji_map() -> None:
    """Drop the cached map (call after regenerating the JSON file)."""
    global _map_override
    _map_override = None
    _load_custom_emoji_map.cache_clear()


def set_custom_emoji_map_for_tests(mapping: Optional[Mapping[str, str]]) -> None:
    """Inject a mapping in unit tests without touching disk."""
    global _map_override
    _map_override = dict(mapping) if mapping is not None else None
    _load_custom_emoji_map.cache_clear()


def custom_emoji_map() -> Mapping[str, str]:
    if _map_override is not None:
        return _map_override
    return _load_custom_emoji_map()


def emoji_for(icon: Optional[str]) -> str:
    """Unicode emoji for a QWeather code, or the raw value if already an emoji."""
    if not icon:
        return "❓"
    code = str(icon).strip()
    if code in WEATHER_ICONS:
        return WEATHER_ICONS[code]
    # Already an emoji (legacy Caiyun path) or unknown code: pass through.
    return code


def custom_emoji_id_for(icon: Optional[str]) -> Optional[str]:
    """Return custom_emoji_id when enabled and mapped; else None."""
    if not icon or not _settings_enabled():
        return None
    code = str(icon).strip()
    return custom_emoji_map().get(code)


def weather_icon(icon: Optional[str]) -> str:
    """Plain Unicode fallback (charts, logs, string-only surfaces)."""
    return emoji_for(icon)


def weather_icon_rich(icon: Optional[str]) -> RichIcon:
    """RichText fragment: custom_emoji dict or plain emoji string."""
    fallback = emoji_for(icon)
    eid = custom_emoji_id_for(icon)
    if not eid:
        return fallback
    return {
        "type": "custom_emoji",
        "custom_emoji_id": eid,
        "alternative_text": fallback,
    }


def weather_icon_html(icon: Optional[str]) -> str:
    """HTML fragment with ``<tg-emoji>`` when mapped."""
    fallback = emoji_for(icon)
    eid = custom_emoji_id_for(icon)
    safe = html_escape(fallback, quote=True)
    if not eid:
        return safe
    return f'<tg-emoji emoji-id="{html_escape(eid, quote=True)}">{safe}</tg-emoji>'


def weather_icon_md(icon: Optional[str]) -> str:
    """MarkdownV2 fragment; custom emoji uses Telegram's emoji-link syntax.

    The returned string is ready to embed in a MarkdownV2 body (do **not**
    run ``escape_v2`` on it).
    """
    fallback = emoji_for(icon)
    eid = custom_emoji_id_for(icon)
    if not eid:
        return fallback
    # MarkdownV2 custom emoji: ![👍](tg://emoji?id=…)
    return f"![{fallback}](tg://emoji?id={eid})"


def rich_icon_text(icon: Optional[str], text: str, *, sep: str = " ") -> list[Any]:
    """``[icon, sep, text]`` as a RichText array (icon may be a custom_emoji dict)."""
    parts: list[Any] = [weather_icon_rich(icon)]
    if sep:
        parts.append(sep)
    parts.append(text)
    return parts


def rich_icon_pair(day_icon: Optional[str], night_icon: Optional[str]) -> RichIcon:
    """Day icon, or ``day→night`` when the two codes differ."""
    if night_icon and night_icon != day_icon:
        return [
            weather_icon_rich(day_icon),
            "→",
            weather_icon_rich(night_icon),
        ]
    return weather_icon_rich(day_icon)
