"""Shared typhoon / tide payloads for /commands, Inline, and Guest Mode.

Keeps fetch + rich/plain rendering in one place so every surface stays aligned.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Optional

from loguru import logger

from core.config import settings

if TYPE_CHECKING:
    from core.handlers.common import BotDependencies


@dataclass(slots=True)
class SpecialQueryResult:
    title: str
    description: str
    blocks: list
    fallback_text: str
    fallback_parse_mode: str = "HTML"


def extract_special_query(parts: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Detect typhoon/tide keywords.

    Returns ``(mode, location)`` where mode is ``"typhoon"`` / ``"tide"`` / None.
    Accepts either order: ``台风 珠海`` / ``珠海 台风`` / bare ``台风``.
    """
    from core.handlers.common import split_glued_query

    if not parts:
        return None, None

    tokens: list[str] = []
    for part in parts:
        tokens.extend(split_glued_query(part.strip()))

    typhoon_words = {"台风", "typhoon", "飓风", "热带风暴", "热带气旋"}
    tide_words = {"潮汐", "潮", "tide", "潮位"}

    mode = None
    location_bits: list[str] = []
    for token in tokens:
        low = token.lower()
        if token in typhoon_words or low in typhoon_words:
            mode = "typhoon"
            continue
        if token in tide_words or low in tide_words:
            mode = "tide"
            continue
        location_bits.append(token)

    if mode is None:
        return None, None
    location = " ".join(location_bits).strip() or None
    return mode, location


async def resolve_typhoon(
    deps: "BotDependencies",
    location: Optional[str],
) -> SpecialQueryResult:
    """Active storms + optional impact assessment for a location."""
    from services.typhoon import assess_storms, format_threat_summary, storm_type_label
    from utils.rich_formatter import build_active_typhoon_blocks, build_typhoon_push_blocks

    if not settings.enable_typhoon_alerts:
        return SpecialQueryResult(
            title="🌀 台风功能已关闭",
            description="ENABLE_TYPHOON_ALERTS=false",
            blocks=[],
            fallback_text="⚠️ 台风功能已关闭（ENABLE_TYPHOON_ALERTS=false）。",
        )

    try:
        storms = await deps.weather_service.qweather.get_active_storms(settings.typhoon_basin)
    except Exception as error:
        logger.error(f"Typhoon lookup failed: {error}")
        return SpecialQueryResult(
            title="❌ 台风数据获取失败",
            description="请稍后再试",
            blocks=[],
            fallback_text="❌ 台风数据获取失败，请稍后再试。",
        )

    if not storms:
        text = (
            f"🌀 当前 {settings.typhoon_basin} 洋盆没有活跃台风。\n"
            "（和风天气目前仅提供西北太平洋 NP 洋盆数据）"
        )
        return SpecialQueryResult(
            title="🌀 暂无活跃台风",
            description=f"{settings.typhoon_basin} 洋盆",
            blocks=[],
            fallback_text=text,
        )

    location_name = ""
    threats = []
    if location:
        try:
            data = await deps.weather_service.get_fused_weather(location, profile="rain")
        except Exception as error:
            logger.debug(f"Typhoon location lookup failed: {error}")
            data = None
        if data is not None:
            location_name = data.location_name
            coords = deps.weather_service.qweather._parse_coords(data.coords)
            if coords is not None:
                threats = assess_storms(storms, coords[0], coords[1])

    if threats:
        threat = threats[0]
        blocks = build_typhoon_push_blocks(threat, location_name)
        # Append extra threats briefly as plain text blocks if any.
        summary = format_threat_summary(threat)
        desc = f"{location_name} · 距中心约 {threat.distance_km:.0f}km" if location_name else summary
        fallback = (
            f"🌀 <b>{escape(summary)}</b>\n"
            f"{escape(location_name)} · 距中心约 {threat.distance_km:.0f}km"
        )
        return SpecialQueryResult(
            title=f"🌀 {summary}",
            description=desc[:80],
            blocks=blocks,
            fallback_text=fallback,
        )

    blocks = build_active_typhoon_blocks(storms, location_name)
    lines = ["🌀 <b>当前活跃台风</b>"]
    for storm in storms:
        bits = [f"• <b>{escape(storm.display_name)}</b>"]
        if storm.now is not None:
            bits.append(storm_type_label(storm.now.type))
            bits.append(f"{storm.now.lat:.1f}°N {storm.now.lon:.1f}°E")
        lines.append(" · ".join(bits))
    if location_name:
        lines.append(f"\n对 {escape(location_name)} 暂无明显影响。")
    else:
        lines.append("\n可加城市名：@机器人 台风 珠海")
    names = "、".join(s.display_name for s in storms[:3])
    return SpecialQueryResult(
        title=f"🌀 活跃台风 · {len(storms)}个",
        description=names[:80] or "西北太平洋",
        blocks=blocks,
        fallback_text="\n".join(lines),
    )


async def resolve_tide(
    deps: "BotDependencies",
    location: str,
) -> SpecialQueryResult:
    """Nearest tide station forecast for a coastal location."""
    from utils.rich_formatter import build_tide_blocks

    if not settings.enable_tide:
        return SpecialQueryResult(
            title="🌊 潮汐功能已关闭",
            description="ENABLE_TIDE=false",
            blocks=[],
            fallback_text="⚠️ 潮汐功能已关闭（ENABLE_TIDE=false）。",
        )

    if not location:
        return SpecialQueryResult(
            title="🌊 需要沿海城市",
            description="例如：潮汐 青岛",
            blocks=[],
            fallback_text="请提供城市或位置，例如：@机器人 潮汐 青岛",
        )

    qweather = deps.weather_service.qweather
    try:
        loc_info = await qweather.get_geo_location(location)
        if not loc_info:
            return SpecialQueryResult(
                title="❌ 找不到地点",
                description=location,
                blocks=[],
                fallback_text=f"❌ 找不到地点：{location}",
            )
        lon = float(loc_info["lon"])
        lat = float(loc_info["lat"])
        stations = await qweather.get_tide_stations(lon, lat)
    except Exception as error:
        logger.error(f"Tide station lookup failed: {error}")
        return SpecialQueryResult(
            title="❌ 潮汐站查询失败",
            description="请稍后再试",
            blocks=[],
            fallback_text="❌ 潮汐站查询失败，请稍后再试。",
        )

    name = loc_info.get("name", location)
    if not stations:
        return SpecialQueryResult(
            title=f"🌊 {name} 无潮汐站",
            description="仅覆盖沿海主要港口",
            blocks=[],
            fallback_text=(
                f"🌊 {name} 附近没有可用的潮汐站（数据只覆盖沿海主要港口）。"
            ),
        )

    forecast = None
    for station in stations:
        try:
            forecast = await qweather.get_tide(station)
        except Exception as error:
            logger.debug(f"Tide fetch failed for {station.id}: {error}")
            continue
        if forecast is not None:
            break

    if forecast is None:
        return SpecialQueryResult(
            title="🌊 暂无潮汐数据",
            description=name,
            blocks=[],
            fallback_text="🌊 最近的潮汐站暂无当日数据，请稍后再试。",
        )

    blocks = build_tide_blocks(forecast)
    lines = [
        f"🌊 <b>{escape(forecast.station.name)} 潮汐</b> · {forecast.date.strftime('%m-%d')}"
    ]
    for item in forecast.extremes:
        label = "🔺 高潮" if item.is_high else "🔻 低潮"
        lines.append(f"{item.time.strftime('%H:%M')} {label} {item.height:.2f} m")
    if not forecast.extremes:
        lines.append("该站当日没有高低潮数据")

    first = forecast.extremes[0] if forecast.extremes else None
    desc = (
        f"{first.time.strftime('%H:%M')} "
        f"{'高潮' if first.is_high else '低潮'} {first.height:.2f}m"
        if first
        else "当日潮汐"
    )
    return SpecialQueryResult(
        title=f"🌊 {forecast.station.name} 潮汐",
        description=desc,
        blocks=blocks,
        fallback_text="\n".join(lines),
    )
