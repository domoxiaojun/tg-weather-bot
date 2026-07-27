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

# QWeather icon code → (中文语义, Unicode fallback emoji).
# Labels follow 和风天气官方图标说明（dev.qweather.com/docs/resource/icons/）。
# Insertion order == pack upload order. Do not reorder casually: the MDV2 import
# script pairs custom_emoji_id with codes by this sequence.
# Telegram custom-emoji 的关联基础 emoji 必须是「一个」emoji，禁止 ❄️🌨️ 拼接。
_WEATHER_ICON_ROWS: tuple[tuple[str, str, str], ...] = (
    # —— 白天晴云 ——
    ("100", "晴", "☀️"),
    ("101", "多云", "🌤️"),
    ("102", "少云", "☁️"),
    ("103", "晴间多云", "🌥️"),
    ("104", "阴", "⛅"),
    # —— 夜间晴云 ——
    ("150", "晴（夜）", "🌙"),
    ("151", "多云（夜）", "🌤️"),
    ("152", "少云（夜）", "☁️"),
    ("153", "晴间多云（夜）", "🌥️"),
    # —— 降雨 ——
    ("300", "阵雨", "🌦️"),
    ("301", "强阵雨", "🌧️"),
    ("302", "雷阵雨", "🌧️"),
    ("303", "强雷阵雨", "⛈️"),
    ("304", "雷阵雨伴有冰雹", "🌦️"),
    ("305", "小雨", "🌧️"),
    ("306", "中雨", "🌧️"),
    ("307", "大雨", "⛈️"),
    ("308", "极端降雨", "🌧️"),
    ("309", "毛毛雨/细雨", "🌦️"),
    ("310", "暴雨", "🌧️"),
    ("311", "大暴雨", "🌧️"),
    ("312", "特大暴雨", "⛈️"),
    ("313", "冻雨", "🌧️"),
    ("314", "小到中雨", "🌧️"),
    ("315", "中到大雨", "⛈️"),
    ("316", "大到暴雨", "🌧️"),
    ("317", "暴雨到大暴雨", "🌧️"),
    ("318", "大暴雨到特大暴雨", "⛈️"),
    # —— 夜间阵雨 / 雨 ——
    ("350", "阵雨（夜）", "🌨️"),
    ("351", "强阵雨（夜）", "🌨️"),
    ("399", "雨", "🌨️"),
    # —— 降雪 ——
    ("400", "小雪", "❄️"),
    ("401", "中雪", "❄️"),
    ("402", "大雪", "❄️"),
    ("403", "暴雪", "❄️"),
    ("404", "雨夹雪", "🌨️"),
    ("405", "雨雪天气", "❄️"),
    ("406", "阵雨夹雪", "❄️"),
    ("407", "阵雪", "❄️"),
    ("408", "小到中雪", "🌨️"),
    ("409", "中到大雪", "🌨️"),
    ("410", "大到暴雪", "🌨️"),
    ("456", "阵雨夹雪（夜）", "🌨️"),
    ("457", "阵雪（夜）", "❄️"),
    ("499", "雪", "❄️"),
    # —— 雾霾沙尘 ——
    ("500", "薄雾", "🌫️"),
    ("501", "雾", "🌫️"),
    ("502", "霾", "🌫️"),
    ("503", "扬沙", "🌪️"),
    ("504", "浮尘", "🌪️"),
    ("507", "沙尘暴", "🌪️"),
    ("508", "强沙尘暴", "🌪️"),
    ("509", "浓雾", "🌫️"),
    ("510", "强浓雾", "🌫️"),
    ("511", "中度霾", "🌫️"),
    ("512", "重度霾", "🌫️"),
    ("513", "严重霾", "🌫️"),
    ("514", "大雾", "🌫️"),
    ("515", "特强浓雾", "🌫️"),
    # —— 月相图标（和风 moonPhase 等字段；极少出现在 now.icon）——
    ("800", "新月", "🌑"),
    ("801", "蛾眉月", "🌒"),
    ("802", "上弦月", "🌓"),
    ("803", "盈凸月", "🌔"),
    ("804", "满月", "🌕"),
    ("805", "亏凸月", "🌖"),
    ("806", "下弦月", "🌗"),
    ("807", "残月", "🌘"),
    # —— 其他 ——
    ("900", "热", "🥵"),
    ("901", "冷", "🥶"),
    ("999", "未知", "❓"),
    # —— 常用预警图标（和风 warning.icon / 预警类型，官网 icons 1000+）——
    # 名称对齐 dev.qweather.com 预警图标与国内预警业务常用叫法。
    ("1001", "台风预警", "🌀"),
    ("1002", "龙卷风预警", "🌪️"),
    ("1003", "暴雨预警", "🌧️"),
    ("1004", "暴雪预警", "❄️"),
    ("1005", "寒潮预警", "🥶"),
    ("1006", "大风预警", "💨"),
    ("1007", "沙尘暴预警", "🌪️"),
    ("1008", "低温冻害预警", "🥶"),
    ("1009", "高温预警", "🥵"),
    ("1010", "热浪预警", "🥵"),
    ("1011", "干热风预警", "💨"),
    ("1012", "下击暴流预警", "💨"),
    ("1013", "雪崩预警", "❄️"),
    ("1014", "雷电预警", "⚡"),
    ("1015", "冰雹预警", "🧊"),
    ("1016", "霜冻预警", "❄️"),
    ("1017", "大雾预警", "🌫️"),
    ("1018", "低空风切变预警", "💨"),
    ("1019", "霾预警", "🌫️"),
    ("1020", "雷暴大风预警", "⛈️"),
    ("1021", "道路结冰预警", "🧊"),
    ("1022", "干旱预警", "☀️"),
    ("1023", "海上大风预警", "💨"),
    ("1024", "高温中暑预警", "🥵"),
    ("1025", "森林火险预警", "🔥"),
    ("1026", "草原火险预警", "🔥"),
    ("1027", "冰冻预警", "🧊"),
    ("1028", "空间天气预警", "🌌"),
    ("1029", "重污染预警", "😷"),
    ("1030", "低温雨雪冰冻预警", "❄️"),
    ("1031", "强对流预警", "⛈️"),
    ("1032", "臭氧预警", "😷"),
    ("1033", "大雪预警", "❄️"),
    ("1034", "寒冷预警", "🥶"),
    ("1035", "连阴雨预警", "🌧️"),
    ("1036", "渍涝风险预警", "🌊"),
    ("1037", "地质灾害气象风险预警", "⛰️"),
    ("1038", "强降雨预警", "🌧️"),
    ("1039", "强降温预警", "🥶"),
    ("1040", "雪灾预警", "❄️"),
    ("1041", "森林（草原）火险预警", "🔥"),
    ("1042", "雷暴预警", "⛈️"),
    ("1043", "严寒预警", "🥶"),
    ("1044", "沙尘预警", "🌪️"),
    ("1045", "海浪预警", "🌊"),
    ("1046", "海上雷电预警", "⚡"),
    ("1047", "海上大雾预警", "🌫️"),
    ("1048", "海上雷雨大风预警", "⛈️"),
    ("1049", "海上台风预警", "🌀"),
    ("1050", "寒冷潮预警", "🥶"),
)

# code → Unicode fallback (charts / alternative_text / unmapped surfaces)
WEATHER_ICONS: dict[str, str] = {code: emoji for code, _label, emoji in _WEATHER_ICON_ROWS}

# code → 中文语义（LLM / 文档 / 调试）
WEATHER_ICON_LABELS: dict[str, str] = {code: label for code, label, _emoji in _WEATHER_ICON_ROWS}

# Pack upload / MDV2 import order — identical to table order above.
UPLOAD_ICON_CODES: tuple[str, ...] = tuple(code for code, _label, _emoji in _WEATHER_ICON_ROWS)


def color_for_icon_code(code: str) -> str:
    """Hex fill for rasterising QWeather SVGs (they use currentColor).

    Colours are chosen to read well on Telegram dark/light bubbles — not a
    1:1 clone of the marketing site CSS, but clearly non-black.
    """
    try:
        n = int(str(code).strip())
    except ValueError:
        return "#5D6D7E"
    if 100 <= n <= 104:
        return "#F5A623"  # 白天晴云 — 金黄
    if 150 <= n <= 153:
        return "#8FA4E8"  # 夜间 — 淡紫蓝
    if 300 <= n <= 399:
        return "#3B8EEA"  # 雨 — 蓝
    if 400 <= n <= 499:
        return "#6EC6E6"  # 雪 — 冰蓝
    if 500 <= n <= 515:
        return "#A0A7B0"  # 雾霾 — 灰
    if 800 <= n <= 807:
        return "#E6D39A"  # 月相 — 月色
    if n == 900:
        return "#E74C3C"  # 热
    if n == 901:
        return "#5DADE2"  # 冷
    if n == 999:
        return "#95A5A6"
    if 1000 <= n < 2000:
        return "#E67E22"  # 预警 — 橙
    if n >= 2000:
        return "#C0392B"
    return "#5D6D7E"

# UI chrome keys → nearest QWeather icon code (pack has weather phenomena only).
# Used for table row labels / section headings so the card is not a mix of
# system emoji + custom weather icons.
UI_ICON_CODES: dict[str, str] = {
    "sun": "100",
    "day": "100",
    "clear": "100",
    "cloud": "104",
    "night": "150",
    "rain": "305",
    "shower": "300",
    "storm": "303",
    "snow": "400",
    "fog": "501",
    "haze": "502",
    "dust": "503",
    "air": "502",
    "hot": "900",
    "cold": "901",
    "unknown": "999",
    # Moon-phase defaults (override with moon_phase_code() when text is known).
    "moon": "804",
    "moon_new": "800",
    "moon_crescent": "801",
    "moon_first": "802",
    "moon_waxing": "803",
    "moon_full": "804",
    "moon_waning": "805",
    "moon_last": "806",
    "moon_waning_crescent": "807",
}

# 和风/中文月相名 → icon code
MOON_PHASE_CODES: dict[str, str] = {
    "新月": "800",
    "蛾眉月": "801",
    "娥眉月": "801",
    "上弦月": "802",
    "盈凸月": "803",
    "满月": "804",
    "亏凸月": "805",
    "下弦月": "806",
    "残月": "807",
    "新月(New Moon)": "800",
    "峨眉月": "801",
}

# Repo root (…/tg-weather-bot), independent of process CWD / Docker WORKDIR quirks.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Runtime lookup order (first existing file wins):
# 1) WEATHER_CUSTOM_EMOJI_MAP_PATH (absolute, or relative to project root)
# 2) data/weather_custom_emoji.json  — host volume / upload script output
# 3) resources/weather_custom_emoji.json — shipped with the image (not under ./data mount)
_DEFAULT_RELATIVE_CANDIDATES: tuple[str, ...] = (
    "data/weather_custom_emoji.json",
    "resources/weather_custom_emoji.json",
)

_map_override: Optional[Mapping[str, str]] = None
_resolved_map_path: Optional[Path] = None


def _settings_enabled() -> bool:
    try:
        from core.config import settings
        return bool(getattr(settings, "enable_custom_weather_emoji", True))
    except Exception:
        return True


def _settings_map_path_raw() -> Optional[str]:
    try:
        from core.config import settings
        raw = getattr(settings, "weather_custom_emoji_map_path", None)
        return str(raw).strip() if raw else None
    except Exception:
        return None


def _as_absolute(path: Path) -> Path:
    if path.is_absolute():
        return path
    return (_PROJECT_ROOT / path).resolve()


def resolve_custom_emoji_map_path() -> Optional[Path]:
    """Return the map file that will be (or was) loaded, if any exists."""
    candidates: list[Path] = []
    configured = _settings_map_path_raw()
    if configured:
        candidates.append(_as_absolute(Path(configured)))
    for relative in _DEFAULT_RELATIVE_CANDIDATES:
        path = _as_absolute(Path(relative))
        if path not in candidates:
            candidates.append(path)

    for path in candidates:
        if path.is_file():
            return path
    return candidates[0] if candidates else None


def _map_path() -> Path:
    """Best path for logging; may not exist yet."""
    return resolve_custom_emoji_map_path() or _as_absolute(Path(_DEFAULT_RELATIVE_CANDIDATES[0]))


def _parse_map_file(path: Path) -> dict[str, str]:
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


@lru_cache(maxsize=1)
def _load_custom_emoji_map() -> dict[str, str]:
    """Load code → custom_emoji_id mapping from disk (cached)."""
    global _resolved_map_path
    path = resolve_custom_emoji_map_path()
    _resolved_map_path = path
    if path is None or not path.is_file():
        return {}
    return _parse_map_file(path)


def reload_custom_emoji_map() -> None:
    """Drop the cached map (call after regenerating the JSON file)."""
    global _map_override, _resolved_map_path
    _map_override = None
    _resolved_map_path = None
    _load_custom_emoji_map.cache_clear()


def set_custom_emoji_map_for_tests(mapping: Optional[Mapping[str, str]]) -> None:
    """Inject a mapping in unit tests without touching disk."""
    global _map_override, _resolved_map_path
    _map_override = dict(mapping) if mapping is not None else None
    _resolved_map_path = None
    _load_custom_emoji_map.cache_clear()


def custom_emoji_map() -> Mapping[str, str]:
    if _map_override is not None:
        return _map_override
    return _load_custom_emoji_map()


def custom_emoji_map_source() -> Optional[Path]:
    """Path actually used after load (for startup logs)."""
    if _map_override is not None:
        return None
    custom_emoji_map()  # ensure cache warm
    return _resolved_map_path


def describe_custom_emoji_status() -> str:
    """One-line status for startup logs."""
    if not _settings_enabled():
        return "custom weather emoji disabled (ENABLE_CUSTOM_WEATHER_EMOJI=false)"
    mapping = custom_emoji_map()
    source = custom_emoji_map_source()
    if not mapping:
        return (
            "custom weather emoji enabled but no map loaded "
            f"(looked for data/ + resources/; last path={_map_path()})"
        )
    where = source if source is not None else "injected"
    return f"custom weather emoji: {len(mapping)} icons from {where}"


def emoji_for(icon: Optional[str]) -> str:
    """Unicode emoji for a QWeather code, or the raw value if already an emoji."""
    if not icon:
        return "❓"
    code = str(icon).strip()
    if code in WEATHER_ICONS:
        return WEATHER_ICONS[code]
    # Already an emoji (legacy Caiyun path) or unknown code: pass through.
    return code


def icon_label(icon: Optional[str], *, fallback: str = "未知") -> str:
    """Chinese weather meaning for a QWeather icon code.

    Prefers the official label table; if ``icon`` is already free text (legacy
    path) it is returned as-is.
    """
    if not icon:
        return fallback
    code = str(icon).strip()
    if code in WEATHER_ICON_LABELS:
        return WEATHER_ICON_LABELS[code]
    # Unknown numeric code or free-text sky description.
    if code.isdigit():
        return fallback
    return code


def markdown_v2_custom_emoji(custom_emoji_id: str, alternative_text: str) -> str:
    """Telegram MarkdownV2 custom-emoji entity (do not escape_v2 this fragment).

    Wire form from Bot API docs::

        ![👍](tg://emoji?id=5368324170671202286)
    """
    alt = alternative_text or "▪️"
    return f"![{alt}](tg://emoji?id={custom_emoji_id})"


def html_custom_emoji(custom_emoji_id: str, alternative_text: str) -> str:
    """Telegram HTML custom-emoji entity."""
    alt = html_escape(alternative_text or "▪️", quote=True)
    eid = html_escape(str(custom_emoji_id), quote=True)
    return f'<tg-emoji emoji-id="{eid}">{alt}</tg-emoji>'


def icon_meta(icon: Optional[str]) -> dict[str, Optional[str]]:
    """Structured icon info for logs, LLM payloads, and debugging."""
    code = str(icon).strip() if icon else ""
    if not code:
        return {
            "code": None,
            "label": None,
            "emoji": None,
            "custom_emoji_id": None,
            "markdown_v2": None,
            "html": None,
        }
    emoji = emoji_for(code)
    eid = custom_emoji_id_for(code)
    return {
        "code": code,
        "label": icon_label(code),
        "emoji": emoji,
        "custom_emoji_id": eid,
        "markdown_v2": markdown_v2_custom_emoji(eid, emoji) if eid else emoji,
        "html": html_custom_emoji(eid, emoji) if eid else html_escape(emoji, quote=True),
    }


def icon_legend(codes: Optional[Any] = None) -> dict[str, str]:
    """``{code: "中文语义"}`` for the given codes (or the full catalog).

    Used so the LLM only sees labels for icons present in this weather payload
    instead of the entire 70-row table every time.
    """
    if codes is None:
        return dict(WEATHER_ICON_LABELS)
    out: dict[str, str] = {}
    for raw in codes:
        if raw is None:
            continue
        code = str(raw).strip()
        if not code or code in out:
            continue
        out[code] = icon_label(code)
    return out


def custom_emoji_id_for(icon: Optional[str]) -> Optional[str]:
    """Return custom_emoji_id when enabled and mapped; else None."""
    if not icon or not _settings_enabled():
        return None
    code = str(icon).strip()
    return custom_emoji_map().get(code)


def weather_icon(icon: Optional[str]) -> str:
    """Plain Unicode fallback (charts, logs, string-only surfaces)."""
    return emoji_for(icon)


def moon_phase_code(phase: Optional[str]) -> str:
    """Map a moon-phase name to a QWeather moon icon code."""
    if not phase:
        return UI_ICON_CODES["moon"]
    text = str(phase).strip()
    if text in MOON_PHASE_CODES:
        return MOON_PHASE_CODES[text]
    for name, code in MOON_PHASE_CODES.items():
        if name in text:
            return code
    return UI_ICON_CODES["moon"]


def ui_icon_code(key: str) -> str:
    """Resolve a UI chrome key to a QWeather icon code."""
    return UI_ICON_CODES.get(key, UI_ICON_CODES["unknown"])


def ui_icon_rich(key: str) -> RichIcon:
    """Custom-emoji (or unicode fallback) for a UI chrome key."""
    return weather_icon_rich(ui_icon_code(key))


def ui_icon_md(key: str) -> str:
    """MarkdownV2 fragment for a UI chrome key."""
    return weather_icon_md(ui_icon_code(key))


def ui_icon_html(key: str) -> str:
    """HTML fragment for a UI chrome key."""
    return weather_icon_html(ui_icon_code(key))


def ui_label_rich(key: str, label: str) -> list[Any]:
    """``[custom_emoji, ' 日间']`` style RichText for table row labels."""
    return [ui_icon_rich(key), f" {label}"]


def ui_label_md(key: str, label: str) -> str:
    """MarkdownV2 label: custom emoji + plain label (label is not escaped)."""
    return f"{ui_icon_md(key)} {label}"


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
    if not eid:
        return html_escape(fallback, quote=True)
    return html_custom_emoji(eid, fallback)


def weather_icon_md(icon: Optional[str]) -> str:
    """MarkdownV2 fragment; custom emoji uses Telegram's emoji-link syntax.

    The returned string is ready to embed in a MarkdownV2 body (do **not**
    run ``escape_v2`` on it). Prefer this over plain ``weather_icon`` anywhere
    ``parse_mode=MarkdownV2`` is used.
    """
    fallback = emoji_for(icon)
    eid = custom_emoji_id_for(icon)
    if not eid:
        return fallback
    return markdown_v2_custom_emoji(eid, fallback)


def weather_icon_pair_md(day_icon: Optional[str], night_icon: Optional[str]) -> str:
    """Day icon, or ``day→night`` MarkdownV2 when codes differ."""
    if night_icon and night_icon != day_icon:
        return f"{weather_icon_md(day_icon)}→{weather_icon_md(night_icon)}"
    return weather_icon_md(day_icon)


def build_format_maps(
    id_map: Optional[Mapping[str, str]] = None,
) -> dict[str, dict[str, str]]:
    """Build full ``code → fragment`` tables for MarkdownV2 / HTML / labels.

    Used when writing ``weather_custom_emoji.json`` and for offline export so
    operators can paste MarkdownV2 without re-deriving the syntax.
    """
    source = dict(id_map) if id_map is not None else dict(custom_emoji_map())
    markdown_v2: dict[str, str] = {}
    html: dict[str, str] = {}
    labels: dict[str, str] = {}
    emojis: dict[str, str] = {}
    for code in UPLOAD_ICON_CODES:
        emoji = emoji_for(code)
        label = icon_label(code)
        eid = source.get(code)
        emojis[code] = emoji
        labels[code] = label
        if eid:
            markdown_v2[code] = markdown_v2_custom_emoji(eid, emoji)
            html[code] = html_custom_emoji(eid, emoji)
        else:
            markdown_v2[code] = emoji
            html[code] = html_escape(emoji, quote=True)
    return {
        "markdown_v2": markdown_v2,
        "html": html,
        "labels": labels,
        "emoji": emojis,
    }


def export_markdown_v2_pack_line(id_map: Optional[Mapping[str, str]] = None) -> str:
    """Single-line MarkdownV2 dump of the pack in upload order (for paste/import)."""
    formats = build_format_maps(id_map)
    # Only real custom-emoji fragments — skip pure unicode fallbacks.
    parts = []
    for code in UPLOAD_ICON_CODES:
        frag = formats["markdown_v2"].get(code, "")
        if frag.startswith("!["):
            parts.append(frag)
    return "".join(parts)


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
