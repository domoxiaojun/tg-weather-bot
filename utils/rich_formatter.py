"""Render WeatherData into Bot API 10.2 rich blocks.

The MarkdownV2 renderers in :mod:`utils.formatter` stay as the fallback path.
Rich blocks need no escaping at all (plain strings are passed through), and
tables give the column alignment that the text views can only approximate.
"""

import re
from html import unescape
from typing import List, Optional

from domain.models import WeatherData
from services.telegram_rich import (
    bold,
    bullet_list,
    cell,
    details,
    footer,
    heading,
    italic,
    link,
    marked,
    paragraph,
    table,
)
from utils.formatter import (
    _display_summary_lines,
    _weekday_cn,
    format_attribution,
    format_life_index_entry,
    format_precip_value,
    format_weather_number,
    normalize_warning_level,
    ordered_indices_for_day,
)
from utils.weather_icons import (
    moon_phase_code,
    rich_icon_pair,
    rich_icon_text,
    ui_icon_rich,
    ui_label_rich,
    weather_icon_rich,
)

SOURCE_LABELS = {
    "qweather": "和风天气",
    "caiyun": "彩云天气",
    "fusion": "和风天气 & 彩云天气",
}

# Keep tables readable on a phone.
HOURLY_TABLE_LIMIT = 24
DAILY_TABLE_LIMIT = 15
MINUTELY_TABLE_LIMIT = 12


def _plain_wind(direction, degrees, scale, speed) -> str:
    """Wind text without MarkdownV2 escaping (rich blocks take plain text)."""
    parts: List[str] = []
    if direction:
        parts.append(str(direction))
    elif degrees is not None:
        parts.append(f"{format_weather_number(degrees, 0)}°")
    if scale:
        parts.append(f"{scale}级")
    if speed is not None:
        parts.append(f"{format_weather_number(speed)}km/h")
    return " ".join(parts)


def _source_label(data: WeatherData) -> str:
    return SOURCE_LABELS.get(data.source, str(data.source).title())


def build_footer(data: WeatherData) -> dict:
    text = f"数据源: {_source_label(data)} · {data.update_time.strftime('%m-%d %H:%M')} 更新"
    attribution = format_attribution(data)
    if not attribution:
        return footer(text)

    # Attribution is a licensing requirement, not an optional credit. Rich
    # messages disable entity detection, so a bare URL would not be tappable —
    # wrap it in an explicit link.
    if attribution.startswith("http") and " " not in attribution:
        return footer([f"{text}\n", link("数据来源声明", attribution)])
    return footer(f"{text}\n{attribution}")


def build_header(data: WeatherData, subtitle: Optional[str] = None) -> List[dict]:
    stamp = data.update_time
    suffix = f" {data.location_name}"
    if stamp is not None:
        # Date + weekday ride on the title line — a separate line wastes space.
        suffix += f" · {stamp.strftime('%m-%d')} {_weekday_cn(stamp)}"
    title = rich_icon_text(data.now_icon, suffix.lstrip(), sep=" ")
    blocks = [heading(title, size=2)]
    if subtitle:
        blocks.append(paragraph(italic(subtitle)))
    return blocks


def build_alert_blocks(data: WeatherData) -> List[dict]:
    """Keep warnings prominent without letting long official copy take over."""
    blocks: List[dict] = []
    for alert in data.alerts[:3]:
        level = normalize_warning_level(alert.level)
        title = alert.title if not level or level in alert.title else f"{alert.title}（{level}）"
        text = (alert.text or "").strip()
        expanded: List[dict] = []
        if text:
            body = text[:500] + ("…" if len(text) > 500 else "")
            expanded.append(
                {"type": "blockquote", "blocks": [paragraph(body)], "credit": alert.source or "预警"}
            )
        else:
            expanded.append(paragraph(italic(alert.source or "暂无详细说明")))
        blocks.append(
            details([weather_icon_rich(alert_icon_code(alert)), " ", bold(title)], expanded)
        )
    return blocks


# Intraday buckets for the period overview matrix: hour // 6.
PERIOD_LABELS = ("凌晨", "上午", "下午", "夜间")
PERIOD_MATRIX_MAX_DAYS = 4


def build_period_matrix(data: WeatherData) -> List[dict]:
    """Rows = date, columns = 凌晨/上午/下午/夜间 — "when exactly will it rain".

    Only covers the days the hourly forecast reaches (72h by default): daily
    forecasts carry day/night granularity only, so anything further out cannot
    be split into periods honestly.
    """
    if not data.hourly:
        return []

    grouped: dict = {}
    for hour in data.hourly:
        day = hour.time.date()
        grouped.setdefault(day, {}).setdefault(hour.time.hour // 6, []).append(hour)

    days = sorted(grouped)[:PERIOD_MATRIX_MAX_DAYS]
    if len(days) < 2:
        return []

    rows: List[list] = []
    any_rain = False
    for day in days:
        row = [cell(f"{day.strftime('%m-%d')} {_weekday_cn(day)}", align="left")]
        for period in range(4):
            hours = grouped[day].get(period)
            if not hours:
                # Omitting text renders an invisible cell — right for past or
                # not-yet-forecast periods.
                row.append(cell(align="center"))
                continue

            temps = [hour.temp for hour in hours if hour.temp is not None]
            icons = [hour.icon for hour in hours if hour.icon]
            if temps:
                low, high = int(round(min(temps))), int(round(max(temps)))
                temp_text = f"{low}~{high}°" if low != high else f"{low}°"
            else:
                temp_text = "—"

            if icons:
                dominant = max(set(icons), key=icons.count)
                text = rich_icon_text(dominant, temp_text)
            else:
                text = f"— {temp_text}"

            pops = [hour.pop for hour in hours if hour.pop is not None]
            wet = any((hour.precip or 0) > 0 for hour in hours) or (pops and max(pops) >= 50)
            if wet:
                any_rain = True
                row.append(cell(marked(text), align="center"))
            else:
                row.append(cell(text, align="center"))
        rows.append(row)

    caption = "时段概览 · 高亮表示该时段可能降水" if any_rain else "时段概览"
    return [
        table(
            rows,
            headers=["日期", *PERIOD_LABELS],
            aligns=["left", "center", "center", "center", "center"],
            bordered=True,
            caption=caption,
        )
    ]


# What each pollutant is, plus 国标单项指数分级限值 (HJ 633: 1h values for
# gases, 24h for particulates — the closest published ladder for realtime
# numbers). A bare concentration means nothing to a layperson; the level and
# the one-line "what is this" are the actual information.
_POLLUTANT_META = {
    "PM2.5": ("细颗粒物，可深入肺部", (35, 75, 115, 150, 250)),
    "PM10": ("可吸入颗粒物（扬尘）", (50, 150, 250, 350, 420)),
    "O₃": ("臭氧，晴热午后偏高", (160, 200, 300, 400, 800)),
    "NO₂": ("二氧化氮，多来自尾气", (100, 200, 700, 1200, 2340)),
    "SO₂": ("二氧化硫，燃煤排放", (150, 500, 650, 800, 1600)),
    "CO": ("一氧化碳，通风不良时危险", (5, 10, 35, 60, 90)),
}
_POLLUTANT_LEVELS = ("优", "良", "轻度", "中度", "重度", "严重")


def pollutant_level(name: str, value) -> Optional[str]:
    """优/良/轻度/中度/重度/严重 for one pollutant concentration."""
    meta = _POLLUTANT_META.get(name)
    if meta is None or value is None:
        return None
    for limit, label in zip(meta[1], _POLLUTANT_LEVELS):
        if value <= limit:
            return label
    return _POLLUTANT_LEVELS[-1]


def build_air_quality_blocks(data: WeatherData) -> List[dict]:
    """Collapsible pollutant breakdown — six fields the text views cannot fit."""
    air = data.air_quality
    if air is None:
        return []

    pollutants = (
        ("PM2.5", air.pm2p5),
        ("PM10", air.pm10),
        ("O₃", air.o3),
        ("NO₂", air.no2),
        ("SO₂", air.so2),
        ("CO", air.co),
    )
    rows = []
    for name, value in pollutants:
        if value is None:
            continue
        level = pollutant_level(name, value)
        # Levels beyond 良 are highlighted — that is the "should I care" bit.
        level_cell = (
            marked(level) if level and level not in ("优", "良") else (level or "—")
        )
        rows.append([name, format_weather_number(value), level_cell])
    if not rows and air.aqi is None and not air.category and not air.description and not air.primary:
        return []

    inner: List[dict] = []
    if rows:
        inner.append(
            table(
                rows,
                headers=["污染物", "浓度", "水平"],
                aligns=["left", "right", "center"],
                caption="μg/m³（CO 为 mg/m³）",
            )
        )
    else:
        inner.append(paragraph(italic("暂无污染物分项数据")))
    if air.primary:
        inner.append(paragraph(["主要污染物: ", bold(air.primary)]))
    if air.description:
        inner.append(paragraph(italic(air.description)))
    if data.air_stations:
        inner.append(paragraph(italic(f"附近监测站: {'、'.join(data.air_stations[:3])}")))

    summary = [
        ui_icon_rich("air"),
        " 空气质量",
    ]
    if air.aqi is not None:
        summary.append(f" · AQI {air.aqi}")
    if air.category:
        summary.append(f" · {air.category}")
    # Collapsed by default — say so, or nobody discovers the breakdown.
    summary.append("（点击展开详情）")
    return [details(summary, inner)]


def _current_stats_rows(data: WeatherData) -> List[list]:
    """Full realtime observation table — used by the warning push, not /tq.

    The /tq card uses :func:`_core_stats_rows` plus a collapsed extras block;
    a push has no buttons to expand, so it keeps everything inline.
    """
    rows: List[list] = []
    wind = _plain_wind(
        data.now_wind_dir, data.now_wind_direction_degrees, data.now_wind_scale, data.now_wind_speed
    )
    if wind:
        rows.append([ui_label_rich("wind", "风况"), wind])
    if data.now_humidity is not None:
        rows.append([ui_label_rich("fog", "湿度"), f"{data.now_humidity}%"])
    if data.now_precip is not None:
        rows.append([
            ui_label_rich("rain", "当前降水"),
            format_precip_value(data.now_precip, data.now_precip_kind),
        ])
    if data.now_vis is not None:
        rows.append([ui_label_rich("fog", "能见度"), f"{format_weather_number(data.now_vis)}km"])
    if data.now_pressure is not None:
        rows.append([ui_label_rich("cloud", "气压"), f"{format_weather_number(data.now_pressure)}hPa"])
    if data.air_quality:
        aqi = data.air_quality
        air_bits = []
        if aqi.aqi is not None:
            air_bits.append(str(aqi.aqi))
        if aqi.category:
            air_bits.append(aqi.category)
        if aqi.pm2p5 is not None:
            air_bits.append(f"PM2.5 {format_weather_number(aqi.pm2p5)}")
        if air_bits:
            rows.append([ui_label_rich("air", "空气"), " · ".join(air_bits)])
    return rows


# 和风 UV index 分级（0-2 弱 … 11+ 极强）。一个裸数字说明不了任何事情，
# 而分级正是「今天要不要防晒」的答案。
_UV_LEVELS = ((2, "弱"), (5, "中等"), (7, "强"), (10, "很强"))


def uv_level_text(value) -> str:
    """``11`` -> ``11 极强``；无法解析成数字时原样返回。"""
    try:
        index = float(value)
    except (TypeError, ValueError):
        return str(value)
    for limit, label in _UV_LEVELS:
        if index <= limit:
            break
    else:
        label = "极强"
    number = format_weather_number(index, 0)
    return f"{number} {label}"


def _core_stats_rows(data: WeatherData) -> List[list]:
    """今日核心指标，按用户实际关心的顺序排列。

    顺序是刻意的：冷热（气温）→ 全天概貌（日间/夜间）→ 体感（湿度、风）→
    要不要带伞（降水）→ 防晒（紫外线）→ 天光时间 → 与昨天的趋势。
    次要参数一律进 :func:`_extra_stats_details`，主表不超过 8 行。
    """
    day = data.get_current_daily_forecast()
    rows: List[list] = []

    temp_key = "sun"
    if day is not None:
        temp_key = "hot" if (day.temp_max is not None and day.temp_max >= 30) else (
            "cold" if (day.temp_min is not None and day.temp_min <= 5) else "sun"
        )
        rows.append([
            ui_label_rich(temp_key, "气温"),
            f"{format_weather_number(day.temp_min)}~{format_weather_number(day.temp_max)}°C",
        ])
        # 日间/夜间合成一行：风况在下面单独有行，这里只留现象文字。
        # 昼夜同一现象时不写「多云 → 多云」这种废话。
        if day.text_day == day.text_night:
            rows.append([
                ui_label_rich("day", "全天"),
                [weather_icon_rich(day.icon_day), f" {day.text_day}"],
            ])
        else:
            rows.append([
                ui_label_rich("day", "日间/夜间"),
                [
                    weather_icon_rich(day.icon_day),
                    f" {day.text_day} → ",
                    weather_icon_rich(day.icon_night),
                    f" {day.text_night}",
                ],
            ])

    if data.now_humidity is not None:
        rows.append([ui_label_rich("fog", "湿度"), f"{data.now_humidity}%"])

    wind = _plain_wind(
        data.now_wind_dir, data.now_wind_direction_degrees, data.now_wind_scale, data.now_wind_speed
    )
    if wind:
        rows.append([ui_label_rich("wind", "风况"), wind])

    # 今日累计降水 + 未来 6h 降水概率合成一行——两者回答的是同一个问题。
    precip_bits: List[str] = []
    if day is not None and day.precip is not None:
        precip_bits.append(f"今日 {format_precip_value(day.precip, day.precip_kind)}")
    pops = [hour.pop for hour in data.hourly[:6] if hour.pop is not None]
    if pops:
        precip_bits.append(f"6h 降概 {int(max(pops))}%")
    if precip_bits:
        rows.append([ui_label_rich("rain", "降水"), " · ".join(precip_bits)])

    if day is not None and day.uv_index:
        rows.append([ui_label_rich("sun", "紫外线"), uv_level_text(day.uv_index)])

    if day is not None and (day.sunrise or day.sunset):
        # 日出 = 100 晴（昼）, 日落 = 150 晴（夜）— the icon pair reads as the
        # label itself, and both come from the uploaded QWeather pack.
        # Kept flat: the API takes a flat segment array, not nested lists.
        rows.append([
            [weather_icon_rich("100"), weather_icon_rich("150"), " 日出/日落"],
            f"{day.sunrise or 'N/A'} / {day.sunset or 'N/A'}",
        ])

    # Day-over-day context: the trend is what people actually want to know.
    if data.yesterday is not None and day is not None:
        deltas = []
        if data.yesterday.temp_max is not None and day.temp_max is not None:
            delta = day.temp_max - data.yesterday.temp_max
            deltas.append(f"最高 {'+' if delta >= 0 else ''}{format_weather_number(delta)}°")
        if data.yesterday.temp_min is not None and day.temp_min is not None:
            delta = day.temp_min - data.yesterday.temp_min
            deltas.append(f"最低 {'+' if delta >= 0 else ''}{format_weather_number(delta)}°")
        if deltas:
            rows.append([ui_label_rich("cloud", "比昨天"), " · ".join(deltas)])

    return rows


def _extra_stats_details(data: WeatherData, *, include_air: bool) -> List[dict]:
    """次要参数（天文 + 细节观测）收进一个折叠块，主卡默认只显示核心表。"""
    day = data.get_current_daily_forecast()
    rows: List[list] = []

    if data.now_precip is not None:
        rows.append([
            ui_label_rich("rain", "当前降水"),
            format_precip_value(data.now_precip, data.now_precip_kind),
        ])
    if data.now_cloud is not None:
        rows.append([ui_label_rich("cloud", "云量"), f"{data.now_cloud}%"])
    if data.now_vis is not None:
        rows.append([ui_label_rich("fog", "能见度"), f"{format_weather_number(data.now_vis)}km"])
    if data.now_pressure is not None:
        rows.append([ui_label_rich("cloud", "气压"), f"{format_weather_number(data.now_pressure)}hPa"])

    if day is not None:
        day_wind = _plain_wind(
            day.wind_dir_day, day.wind_direction_day_degrees, day.wind_scale_day, day.wind_speed_day
        )
        night_wind = _plain_wind(
            day.wind_dir_night,
            day.wind_direction_night_degrees,
            day.wind_scale_night,
            day.wind_speed_night,
        )
        if day_wind or night_wind:
            rows.append([
                ui_label_rich("wind", "昼/夜风"),
                f"{day_wind or 'N/A'} / {night_wind or 'N/A'}",
            ])
        if day.temp_avg is not None:
            rows.append([ui_label_rich("sun", "日均温"), f"{format_weather_number(day.temp_avg)}°C"])
        if day.precip_day is not None or day.precip_night is not None:
            rows.append([
                ui_label_rich("rain", "昼/夜降水"),
                f"{format_precip_value(day.precip_day, day.precip_kind)} / "
                f"{format_precip_value(day.precip_night, day.precip_kind)}",
            ])
        if day.moon_phase:
            rows.append([
                [weather_icon_rich(moon_phase_code(day.moon_phase)), " 月相"],
                day.moon_phase,
            ])
        if day.moon_rise or day.moon_set:
            rows.append([
                [weather_icon_rich(moon_phase_code(day.moon_phase)), " 月升/月落"],
                f"{day.moon_rise or 'N/A'} / {day.moon_set or 'N/A'}",
            ])

    if include_air and data.air_quality:
        aqi = data.air_quality
        air_bits = []
        if aqi.aqi is not None:
            air_bits.append(str(aqi.aqi))
        if aqi.category:
            air_bits.append(aqi.category)
        if air_bits:
            rows.append([ui_label_rich("air", "空气"), " · ".join(air_bits)])

    if not rows:
        return []
    # 102 少云 rather than a system emoji: every label on this card comes from
    # the uploaded QWeather custom-emoji pack.
    summary = [
        ui_icon_rich("detail"),
        " 更多气象参数",
        f" · {len(rows)}项",
        "（点击展开详情）",
    ]
    return [details(summary, [table(rows, aligns=["left", "left"], bordered=True)])]


def _index_pair_blocks(
    indices: List,
    target_date=None,
    *,
    include_heading: bool,
    collapsible: bool = False,
) -> List[dict]:
    """Life indices as a two-column table: each cell is one tip (Unicode emoji).

    On the default /tq card pass ``collapsible=True`` so the block matches air
    quality (``details``, collapsed until the user taps).
    """
    ordered = ordered_indices_for_day(indices, target_date)
    if not ordered:
        return []
    entries = [format_life_index_entry(index, escape=False) for index in ordered]
    rows: List[list] = []
    for offset in range(0, len(entries), 2):
        left = entries[offset]
        right = entries[offset + 1] if offset + 1 < len(entries) else ""
        rows.append([left, right])
    table_block = table(rows, aligns=["left", "left"], bordered=True)
    if collapsible:
        summary: list = ["💡 生活指数", f" · {len(ordered)}项", "（点击展开详情）"]
        return [details(summary, [table_block])]
    blocks: List[dict] = []
    if include_heading:
        blocks.append(heading("💡 生活指数", size=4))
    blocks.append(table_block)
    return blocks


_REPORT_TAG_RE = re.compile(r"<(/?)([bi])>")
_REPORT_SECTION_TITLES = ("预警", "现在", "接下来", "未来几天", "建议")
# Leading unicode emoji(s) then optional <b>title</b> or bare title.
_REPORT_SECTION_RE = re.compile(
    r"^(?:[\U0001F300-\U0001FAFF\u2600-\u27BF\u23F0-\u23FA\u2190-\u21FF]"
    r"(?:\uFE0F)?\s*)*"
    r"(?:<b>)?(?P<title>预警|现在|接下来|未来几天|建议)(?:</b>)?",
)

_ALERT_ICON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("台风", "1001"),
    ("龙卷", "1002"),
    ("暴雨", "1003"),
    ("暴雪", "1004"),
    ("寒潮", "1005"),
    ("大风", "1006"),
    ("沙尘暴", "1007"),
    ("沙尘", "1044"),
    ("高温", "1009"),
    ("热浪", "1010"),
    ("雷电", "1014"),
    ("雷暴", "1042"),
    ("冰雹", "1015"),
    ("大雾", "1017"),
    ("雾", "1017"),
    ("霾", "1019"),
    ("道路结冰", "1021"),
    ("干旱", "1022"),
    ("火险", "1025"),
    ("强对流", "1031"),
    ("大雪", "1033"),
    ("寒冷", "1034"),
    ("海浪", "1045"),
)


def _report_line_richtext(line: str) -> list:
    """Convert one line of Telegram-HTML (<b>/<i> only) into RichText segments."""
    segments: list = []
    position = 0
    bold_on = italic_on = False
    for match in _REPORT_TAG_RE.finditer(line):
        text = unescape(line[position:match.start()])
        if text:
            segments.append(bold(text) if bold_on else italic(text) if italic_on else text)
        if match.group(2) == "b":
            bold_on = match.group(1) != "/"
        else:
            italic_on = match.group(1) != "/"
        position = match.end()
    tail = unescape(line[position:])
    if tail:
        segments.append(bold(tail) if bold_on else italic(tail) if italic_on else tail)
    return segments or [""]


def alert_icon_code(alert) -> str:
    """Pick the warning-family icon (1001-1045) that matches one alert."""
    hay = f"{getattr(alert, 'title', '') or ''} {getattr(alert, 'type', '') or ''}"
    for keyword, code in _ALERT_ICON_KEYWORDS:
        if keyword in hay:
            return code
    return "1003"


def _warning_icon_code(weather: Optional[WeatherData]) -> str:
    if weather is None or not weather.alerts:
        return "1003"
    for alert in weather.alerts:
        hay = f"{alert.title or ''} {alert.type or ''}"
        for keyword, code in _ALERT_ICON_KEYWORDS:
            if keyword in hay:
                return code
    return "1003"


def _report_section_icon(title: str, weather: Optional[WeatherData]):
    if title == "现在" and weather is not None and weather.now_icon:
        return weather_icon_rich(weather.now_icon)
    if title == "预警":
        return weather_icon_rich(_warning_icon_code(weather))
    if title == "接下来":
        return weather_icon_rich("305")
    if title == "未来几天":
        return weather_icon_rich("104")
    return None


def _strip_leading_section_emoji(line: str) -> str:
    return re.sub(
        r"^(?:[\U0001F300-\U0001FAFF\u2600-\u27BF\u23F0-\u23FA\u2190-\u21FF]"
        r"(?:\uFE0F)?\s*)+",
        "",
        line,
    ).lstrip()


def _report_line_with_weather_icons(line: str, weather: Optional[WeatherData]) -> list:
    """Section titles get QWeather custom emoji; body stays LLM HTML text."""
    plain = unescape(_REPORT_TAG_RE.sub("", line)).strip()
    match = _REPORT_SECTION_RE.match(line) or _REPORT_SECTION_RE.match(plain)
    if not match:
        return _report_line_richtext(line)

    title = match.group("title")
    if title not in _REPORT_SECTION_TITLES:
        return _report_line_richtext(line)

    icon = _report_section_icon(title, weather)
    if icon is None:
        return _report_line_richtext(line)

    stripped = _strip_leading_section_emoji(line)
    # Ensure the section title is still bold after emoji strip.
    if not stripped.lower().startswith("<b>") and not stripped.startswith(title):
        stripped = f"<b>{title}</b>"
    elif stripped.startswith(title) and not stripped.lower().startswith("<b>"):
        stripped = f"<b>{title}</b>{stripped[len(title):]}"
    body = _report_line_richtext(stripped)
    return [icon, " ", *body]


# Sections that answer "what do I do today" stay open; planning-horizon
# sections are folded so the report opens short (user feedback 2026-07-29).
_REPORT_COLLAPSED_SECTIONS = ("未来几天", "建议")


def _report_section_title(line: str) -> Optional[str]:
    plain = unescape(_REPORT_TAG_RE.sub("", line)).strip()
    match = _REPORT_SECTION_RE.match(line) or _REPORT_SECTION_RE.match(plain)
    return match.group("title") if match else None


def build_report_blocks(
    report_html: str,
    *,
    title: Optional[str] = None,
    weather: Optional[WeatherData] = None,
    collapse_tail: bool = False,
) -> List[dict]:
    """AI report / daily brief as rich blocks.

    Never send reports via rich ``html=``: InputRichMessage treats content as
    real HTML, so newlines collapse and the report becomes one blob. Blocks
    keep the paragraph structure AND the rich look.

    When ``weather`` is set, section headings (现在/预警/…) are re-prefixed
    with QWeather custom emoji; the model only outputs Unicode + HTML.

    ``collapse_tail`` folds 未来几天/建议 (and everything after them) into a
    collapsed ``details`` block — the report card then opens with just
    预警/现在/接下来. Off by default so the static help text in
    :mod:`core.handlers.guide` renders unchanged.
    """
    blocks: List[dict] = []
    if title:
        if weather is not None and weather.now_icon:
            # The caller's title may open with any system emoji (🤖 for the AI
            # report, ☀️ for the morning brief). Strip it — the QWeather icon
            # replaces it, and keeping both renders two glyphs side by side.
            title_text = _strip_leading_section_emoji(title) or title
            blocks.append(
                heading([weather_icon_rich(weather.now_icon), f" {title_text}"], size=4)
            )
        else:
            blocks.append(heading(title, size=4))

    tail: List[dict] = []
    tail_titles: List[str] = []
    trailing_footer: Optional[dict] = None
    for raw_line in report_html.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        plain = _REPORT_TAG_RE.sub("", line)
        if plain.startswith(("🤖 Generated by", "Generated by")):
            # Attribution belongs at the very bottom, outside the fold.
            trailing_footer = footer(unescape(plain))
            continue
        if weather is not None:
            block = paragraph(_report_line_with_weather_icons(line, weather))
        else:
            block = paragraph(_report_line_richtext(line))

        section = _report_section_title(line) if collapse_tail else None
        if section in _REPORT_COLLAPSED_SECTIONS and section not in tail_titles:
            tail_titles.append(section)
        # Once the first collapsed section starts, everything after it folds
        # too — a later 建议 block must not jump back above the fold.
        if tail_titles:
            tail.append(block)
        else:
            blocks.append(block)

    if tail:
        # 104 matches the icon the 未来几天 section heading itself uses.
        summary = [
            weather_icon_rich("104"),
            " ",
            " · ".join(tail_titles) or "更多内容",
            "（点击展开）",
        ]
        blocks.append(details(summary, tail))
    if trailing_footer is not None:
        blocks.append(trailing_footer)
    return blocks


def build_realtime_blocks(data: WeatherData) -> List[dict]:
    blocks = build_header(data)

    # Icon + temp on one line so custom emoji is visible even if the heading
    # is truncated on small clients.
    hero: list = [
        weather_icon_rich(data.now_icon),
        " ",
        bold(f"{format_weather_number(data.now_temp)}°C"),
    ]
    if data.now_text:
        hero.append(f" {data.now_text}")
    if data.now_feels_like is not None:
        hero.append(f"（体感 {format_weather_number(data.now_feels_like)}°C）")
    blocks.append(paragraph(hero))

    for line in _display_summary_lines(data.summary):
        blocks.append(paragraph(italic(line)))

    blocks.extend(build_alert_blocks(data))

    current_day = data.get_current_daily_forecast()
    if current_day is not None and current_day.date.date() != data.local_update_date:
        # Today's slot already rolled over — say which day the table describes
        # instead of silently presenting tomorrow's numbers as "now".
        blocks.append(
            paragraph([
                bold([
                    ui_icon_rich("cloud"),
                    f" 最近预报 ({current_day.date.strftime('%m-%d')} "
                    f"{_weekday_cn(current_day.date)})",
                ])
            ])
        )

    # One core table, then three collapsed blocks. Everything a person checks
    # daily stays visible; the rest is one tap away.
    core_rows = _core_stats_rows(data)
    if core_rows:
        blocks.append(table(core_rows, aligns=["left", "left"], bordered=True))
    air_blocks = build_air_quality_blocks(data)
    blocks.extend(_extra_stats_details(data, include_air=not air_blocks))
    index_date = current_day.date.date() if current_day is not None else data.local_update_date
    blocks.extend(
        _index_pair_blocks(
            data.indices, index_date, include_heading=True, collapsible=True
        )
    )
    blocks.extend(air_blocks)
    blocks.append(build_footer(data))
    return blocks


def build_hourly_blocks(data: WeatherData, limit: Optional[int] = None) -> List[dict]:
    hours = data.hourly[: min(limit or HOURLY_TABLE_LIMIT, HOURLY_TABLE_LIMIT)]
    if not hours:
        return build_realtime_blocks(data)

    show_feels_like = any(
        hour.feels_like is not None and not hour.feels_like_estimated for hour in hours
    )
    headers = ["时间", "天气", "温度"]
    if show_feels_like:
        headers.append("体感")
    headers += ["降概", "降水"]
    width = len(headers)

    peak_pop = max((hour.pop for hour in hours if hour.pop is not None), default=None)

    rows: List[list] = []
    previous_date = None
    for hour in hours:
        current_date = hour.time.date()
        if previous_date is not None and current_date != previous_date:
            # Full-width separator row keeps multi-day tables readable.
            rows.append([
                cell(
                    f"{hour.time.strftime('%m-%d')} {_weekday_cn(hour.time)}",
                    is_header=True,
                    align="left",
                    colspan=width,
                )
            ])
        previous_date = current_date

        row: list = [
            hour.time.strftime("%H:%M"),
            weather_icon_rich(hour.icon),
            f"{format_weather_number(hour.temp)}°",
        ]
        if show_feels_like:
            row.append(
                f"{format_weather_number(hour.feels_like)}°"
                if hour.feels_like is not None and not hour.feels_like_estimated
                else "—"
            )
        if hour.pop is None:
            row.append("—")
        else:
            pop_text = f"{format_weather_number(hour.pop, decimals=0)}%"
            row.append(marked(pop_text) if peak_pop and hour.pop == peak_pop and hour.pop > 0 else pop_text)
        row.append(
            format_precip_value(hour.precip, hour.precip_kind) if hour.precip is not None else "—"
        )
        rows.append(row)

    aligns = ["left", "center", "right"] + (["right"] if show_feels_like else []) + ["right", "right"]
    return [
        *build_header(data, f"未来 {len(hours)} 小时 · 当地时间"),
        table(rows, headers=headers, aligns=aligns, bordered=True),
        *_hourly_extras_blocks(hours),
        build_footer(data),
    ]


def _hourly_extras_blocks(hours: List) -> List[dict]:
    """Collapsible table for metrics that would make the main table too wide.

    Dew point, pressure, cloud cover, visibility and hourly AQI are all
    fetched but had no user-visible surface before; a collapsed table keeps the
    primary view dense while making them reachable.
    """
    columns = [
        ("露点", lambda hour: f"{format_weather_number(hour.dew)}°" if hour.dew is not None else None),
        ("气压", lambda hour: f"{format_weather_number(hour.pressure)}" if hour.pressure is not None else None),
        ("云量", lambda hour: f"{hour.cloud}%" if hour.cloud is not None else None),
        ("能见度", lambda hour: f"{format_weather_number(hour.visibility)}km" if hour.visibility is not None else None),
        ("AQI", lambda hour: str(hour.aqi) if hour.aqi is not None else None),
        ("辐射", lambda hour: f"{format_weather_number(hour.radiation, 0)}" if hour.radiation is not None else None),
    ]
    active = [
        (label, getter)
        for label, getter in columns
        if any(getter(hour) is not None for hour in hours)
    ]
    if not active:
        return []

    rows = []
    for hour in hours:
        rows.append([
            hour.time.strftime("%H:%M"),
            *[getter(hour) or "—" for _label, getter in active],
        ])
    return [
        details(
            [
                ui_icon_rich("detail"),
                " 更多逐小时指标（" + "/".join(label for label, _getter in active) + "）",
            ],
            [
                table(
                    rows,
                    headers=["时间", *[label for label, _getter in active]],
                    aligns=["left", *["right"] * len(active)],
                    caption="气压 hPa · 辐射 W/m²",
                )
            ],
        )
    ]


def build_tide_blocks(forecast) -> List[dict]:
    """Tide table for one station: high/low moments plus the full curve."""
    station = forecast.station
    subtitle_bits = [station.name]
    if station.distance_km is not None:
        subtitle_bits.append(f"约 {station.distance_km:.0f}km")
    blocks: List[dict] = [
        heading([ui_icon_rich("tide"), f" {station.name} 潮汐"], size=2),
        paragraph(italic(f"{forecast.date.strftime('%m-%d')} · {' · '.join(subtitle_bits)}")),
    ]

    if forecast.extremes:
        peak = max((item.height for item in forecast.extremes), default=None)
        rows = []
        for item in forecast.extremes:
            label = "🔺 高潮" if item.is_high else "🔻 低潮"
            height = f"{format_weather_number(item.height, 2)} m"
            rows.append([
                item.time.strftime("%H:%M"),
                label,
                marked(height) if peak is not None and item.height == peak else height,
            ])
        blocks.append(
            table(
                rows,
                headers=["时间", "类型", "潮高"],
                aligns=["left", "left", "right"],
                bordered=True,
                caption="高亮为当日最高潮位",
            )
        )
    else:
        blocks.append(paragraph("该站当日没有高低潮数据"))

    blocks.append(footer("数据源: 和风天气海洋潮汐 · 仅供参考，作业请以官方潮汐表为准"))
    return blocks


def build_daily_blocks(
    data: WeatherData, days: Optional[int] = None, start_day: int = 0
) -> List[dict]:
    forecasts = data.get_daily_forecasts(start_day=max(0, start_day))
    forecasts = forecasts[: min(days or DAILY_TABLE_LIMIT, DAILY_TABLE_LIMIT)]
    if not forecasts:
        return build_realtime_blocks(data)

    headers = ["日期", "天气", "气温", "降水", "UV"]
    rows: List[list] = []
    for day in forecasts:
        rows.append([
            f"{day.date.strftime('%m-%d')} {_weekday_cn(day.date)}",
            rich_icon_pair(day.icon_day, day.icon_night),
            f"{format_weather_number(day.temp_min)}~{format_weather_number(day.temp_max)}°",
            format_precip_value(day.precip, day.precip_kind) if day.precip is not None else "—",
            str(day.uv_index) if day.uv_index else "—",
        ])

    blocks = [
        *build_header(data, f"未来 {len(forecasts)} 天"),
        # Period matrix first: it answers "which part of which day" at a glance.
        *build_period_matrix(data),
        table(rows, headers=headers, aligns=["left", "center", "right", "right", "right"], bordered=True),
    ]

    # Day/night wording is the detail people scan for; keep it collapsible.
    detail_items = []
    for day in forecasts[:7]:
        wind = _plain_wind(
            day.wind_dir_day, day.wind_direction_day_degrees, day.wind_scale_day, day.wind_speed_day
        )
        line: list = [
            bold(f"{day.date.strftime('%m-%d')} {_weekday_cn(day.date)}"),
            f" {day.text_day} → {day.text_night}",
        ]
        if wind:
            line.append(f" · {wind}")
        detail_items.append(paragraph(line))
    if detail_items:
        blocks.append(
            details([ui_icon_rich("detail"), " 逐日文字描述"], [bullet_list(detail_items)])
        )

    blocks.append(build_footer(data))
    return blocks


def build_indices_blocks(data: WeatherData) -> List[dict]:
    if not data.indices:
        return build_realtime_blocks(data)

    blocks = build_header(data, "生活指数")
    blocks.extend(_index_pair_blocks(data.indices, include_heading=False))

    # With a 3-day range the later days go into collapsed sections instead of
    # repeating every index three times in one wall of text.
    dated = [index for index in data.indices if index.date is not None]
    days = sorted({index.date.date() for index in dated})
    for offset, day in enumerate(days[1:3], start=1):
        label = "明天" if offset == 1 else "后天"
        group_blocks = _index_pair_blocks(data.indices, day, include_heading=False)
        if group_blocks:
            blocks.append(
                details(ui_label_rich("sun", f"{label}（{day.strftime('%m-%d')}）"), group_blocks)
            )

    blocks.append(build_footer(data))
    return blocks


def build_rain_blocks(data: WeatherData) -> List[dict]:
    blocks = build_header(data, "降水预报")
    summary = (data.summary or "").split("\n")
    for line in _display_summary_lines("\n".join(summary)):
        blocks.append(paragraph(italic(line)))

    if data.minutely:
        entries = data.minutely[:MINUTELY_TABLE_LIMIT]
        peak = max((item.precip for item in entries), default=0)
        rows = []
        for item in entries:
            value = f"{format_weather_number(item.precip, decimals=2)}mm"
            rows.append([
                item.time.strftime("%H:%M"),
                marked(value) if peak and item.precip == peak and peak > 0 else value,
            ])
        blocks.append(
            table(rows, headers=["时间", "降水"], aligns=["left", "right"], bordered=True,
                  caption="分钟级降水（和风天气）")
        )
    elif data.hourly:
        rows = []
        for hour in data.hourly[:6]:
            rows.append([
                hour.time.strftime("%H:%M"),
                format_precip_value(hour.precip, hour.precip_kind) if hour.precip is not None else "—",
                f"{format_weather_number(hour.pop, decimals=0)}%" if hour.pop is not None else "—",
            ])
        blocks.append(
            table(rows, headers=["时间", "降水", "降概"], aligns=["left", "right", "right"], bordered=True)
        )
    else:
        blocks.append(paragraph("暂无可用降水预报"))

    blocks.append(build_footer(data))
    return blocks


def build_weather_blocks(
    data: WeatherData,
    view_type: str = "default",
    days: Optional[int] = None,
    start_day: int = 0,
) -> List[dict]:
    """Rich counterpart of :func:`utils.formatter.format_weather_response`."""
    if view_type == "hourly":
        return build_hourly_blocks(data, days)
    if view_type == "daily":
        return build_daily_blocks(data, days, start_day)
    if view_type == "indices":
        return build_indices_blocks(data)
    if view_type == "rain":
        return build_rain_blocks(data)
    return build_realtime_blocks(data)


def build_rain_alert_blocks(data: WeatherData) -> List[dict]:
    """Push notification for the rain watcher."""
    return [
        heading([ui_icon_rich("rain_alert"), " 降雨提醒"], size=2),
        paragraph([bold(data.location_name), f" · {data.update_time.strftime('%m-%d %H:%M')}"]),
        *build_alert_blocks(data),
        *build_rain_blocks(data)[1:],
    ]


def build_alert_push_blocks(data: WeatherData, alert) -> List[dict]:
    """One official warning, pushed on its own so it cannot be scrolled past."""
    level = normalize_warning_level(alert.level)
    title = alert.title if not level or level in alert.title else f"{alert.title}（{level}）"

    blocks: List[dict] = [
        heading([weather_icon_rich(alert_icon_code(alert)), f" {title}"], size=2),
        paragraph([bold(data.location_name), f" · {alert.source or '官方预警'}"]),
    ]
    timing = []
    if alert.pub_time:
        timing.append(f"发布 {alert.pub_time.strftime('%m-%d %H:%M')}")
    if alert.expire_time:
        # "How long does this last" is the second question after "what is it".
        timing.append(f"有效期至 {alert.expire_time.strftime('%m-%d %H:%M')}")
    if timing:
        blocks.append(paragraph(italic(" · ".join(timing))))

    text = (alert.text or "").strip()
    if text:
        blocks.append({"type": "blockquote", "blocks": [paragraph(text[:900])]})

    rows = _current_stats_rows(data)
    if rows:
        blocks.append(details("当前实况", [table(rows, aligns=["left", "left"])]))
    blocks.append(build_footer(data))
    return blocks


def build_event_push_blocks(
    data: WeatherData, title: str, detail: str, *, icon_key: Optional[str] = None
) -> List[dict]:
    """Threshold event (air quality / temperature / wind) push.

    ``icon_key`` is a :data:`utils.weather_icons.UI_ICON_CODES` key so the
    heading carries a QWeather custom emoji instead of a system one.
    """
    return [
        heading(
            [ui_icon_rich(icon_key), f" {title}"] if icon_key else title,
            size=2,
        ),
        paragraph([bold(data.location_name), f" · {data.update_time.strftime('%m-%d %H:%M')}"]),
        paragraph(detail),
        build_footer(data),
    ]


def build_active_typhoon_blocks(storms: list, location_name: str = "") -> List[dict]:
    """Rich list for active storms that do not currently threaten a location."""
    from services.typhoon import storm_type_label

    items = []
    for storm in storms[:6]:
        line: list = [bold(storm.display_name)]
        if storm.now is not None:
            line.extend([
                f" · {storm_type_label(storm.now.type)}",
                f" · {storm.now.lat:.1f}°N {storm.now.lon:.1f}°E",
            ])
        items.append(line)

    blocks: List[dict] = [
        heading([ui_icon_rich("typhoon"), " 当前活跃台风"], size=2),
        bullet_list(items),
    ]
    if location_name:
        blocks.append(paragraph(f"对 {location_name} 暂无明显影响。台风逼近时，已订阅城市会自动收到提醒。"))
    else:
        blocks.append(paragraph("发送 /typhoon 城市 可进一步判断台风对该地点的影响。"))
    blocks.append(footer("数据源: 和风天气热带气旋 · 以官方预警为准"))
    return blocks


def build_typhoon_push_blocks(threat, location_name: str) -> List[dict]:
    """Tropical cyclone alert: what it is, where it is, and where it goes next."""
    from services.typhoon import format_threat_summary, hours_until, storm_type_label

    storm = threat.storm
    now_point = storm.now

    blocks: List[dict] = [
        heading([ui_icon_rich("typhoon"), f" {format_threat_summary(threat)}"], size=2)
    ]
    blocks.append(paragraph([bold(location_name), f" · 距中心约 {threat.distance_km:.0f}km"]))
    if threat.inside_circle:
        blocks.append(
            paragraph([
                ui_icon_rich("wind"),
                marked(f" 已进入{threat.wind_label}，请做好防风准备"),
            ])
        )

    if now_point is not None:
        rows = [[ui_label_rich("typhoon", "强度"), storm_type_label(now_point.type)]]
        if now_point.wind_speed is not None:
            rows.append([
                ui_label_rich("wind", "中心风速"),
                f"{format_weather_number(now_point.wind_speed)} km/h",
            ])
        if now_point.pressure is not None:
            # Same icon as the 气压 row on the /tq card.
            rows.append([
                ui_label_rich("cloud", "中心气压"),
                f"{format_weather_number(now_point.pressure)} hPa",
            ])
        if now_point.move_dir or now_point.move_speed is not None:
            move = " ".join(
                part
                for part in (
                    now_point.move_dir,
                    f"{format_weather_number(now_point.move_speed)} km/h"
                    if now_point.move_speed is not None
                    else None,
                )
                if part
            )
            rows.append(["➡️ 移动", move])
        rows.append(["📍 当前位置", f"{now_point.lat:.1f}°N, {now_point.lon:.1f}°E"])
        if now_point.time:
            rows.append(["🕐 观测时间", now_point.time.strftime("%m-%d %H:%M")])
        blocks.append(table(rows, aligns=["left", "left"]))

    if storm.forecast:
        rows = []
        for point in storm.forecast[:8]:
            eta = hours_until(point.time)
            rows.append([
                point.time.strftime("%m-%d %H:%M") if point.time else "—",
                f"+{eta:.0f}h" if eta else "—",
                storm_type_label(point.type),
                f"{format_weather_number(point.wind_speed)}" if point.wind_speed is not None else "—",
                f"{point.lat:.1f},{point.lon:.1f}",
            ])
        blocks.append(
            details(
                "🧭 预测路径",
                [
                    table(
                        rows,
                        headers=["时间", "时距", "强度", "风速", "位置"],
                        aligns=["left", "right", "left", "right", "right"],
                        bordered=True,
                        caption="风速单位 km/h · 数据源 和风天气",
                    )
                ],
                is_open=True,
            )
        )

    blocks.append(footer("数据源: 和风天气热带气旋 · 以官方预警为准"))
    return blocks
