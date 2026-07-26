"""Render WeatherData into Bot API 10.2 rich blocks.

The MarkdownV2 renderers in :mod:`utils.formatter` stay as the fallback path.
Rich blocks need no escaping at all (plain strings are passed through), and
tables give the column alignment that the text views can only approximate.
"""

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
    marked,
    paragraph,
    table,
    thinking,
)
from utils.formatter import (
    CATEGORIES,
    INDICES_EMOJI,
    _display_summary_lines,
    _weekday_cn,
    format_attribution,
    format_precip_value,
    format_weather_number,
    normalize_warning_level,
    select_indices_for_day,
    weather_icon,
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
    if attribution:
        # Attribution is a licensing requirement, not an optional credit.
        text = f"{text}\n{attribution}"
    return footer(text)


def build_header(data: WeatherData, subtitle: Optional[str] = None) -> List[dict]:
    blocks = [heading(f"{weather_icon(data.now_icon)} {data.location_name}", size=2)]
    if subtitle:
        blocks.append(paragraph(italic(subtitle)))
    return blocks


def build_alert_blocks(data: WeatherData) -> List[dict]:
    """Warnings first and visually separated — they matter most."""
    blocks: List[dict] = []
    for alert in data.alerts[:3]:
        level = normalize_warning_level(alert.level)
        title = alert.title if not level or level in alert.title else f"{alert.title}（{level}）"
        inner = [paragraph(marked(f"⚠️ {title}"))]
        text = (alert.text or "").strip()
        if text:
            inner.append(paragraph(text[:220] + ("…" if len(text) > 220 else "")))
        blocks.append({"type": "blockquote", "blocks": inner, "credit": alert.source or "预警"})
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
            icon = weather_icon(max(set(icons), key=icons.count)) if icons else "—"
            if temps:
                low, high = int(round(min(temps))), int(round(max(temps)))
                temp_text = f"{low}~{high}°" if low != high else f"{low}°"
            else:
                temp_text = "—"

            pops = [hour.pop for hour in hours if hour.pop is not None]
            wet = any((hour.precip or 0) > 0 for hour in hours) or (pops and max(pops) >= 50)
            text = f"{icon} {temp_text}"
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
    rows = [
        [name, format_weather_number(value)]
        for name, value in pollutants
        if value is not None
    ]
    if not rows and not air.description and not air.primary:
        return []

    inner: List[dict] = []
    if rows:
        inner.append(
            table(
                rows,
                headers=["污染物", "浓度"],
                aligns=["left", "right"],
                caption="浓度单位 μg/m³（CO 为 mg/m³）",
            )
        )
    if air.primary:
        inner.append(paragraph(["主要污染物: ", bold(air.primary)]))
    if air.description:
        inner.append(paragraph(italic(air.description)))
    if data.air_stations:
        inner.append(paragraph(italic(f"附近监测站: {'、'.join(data.air_stations[:3])}")))

    summary_bits = ["🌫️ 空气质量"]
    if air.aqi is not None:
        summary_bits.append(f"AQI {air.aqi}")
    if air.category:
        summary_bits.append(air.category)
    return [details(" · ".join(summary_bits), inner)]


def _current_stats_rows(data: WeatherData, *, include_air: bool = True) -> List[list]:
    rows: List[list] = []
    wind = _plain_wind(
        data.now_wind_dir, data.now_wind_direction_degrees, data.now_wind_scale, data.now_wind_speed
    )
    if wind:
        rows.append(["💨 风况", wind])
    if data.now_humidity is not None:
        rows.append(["💧 湿度", f"{data.now_humidity}%"])
    if data.now_precip is not None:
        rows.append(["☔️ 降水", format_precip_value(data.now_precip, data.now_precip_kind)])
    if data.now_vis is not None:
        rows.append(["👁️ 能见度", f"{format_weather_number(data.now_vis)}km"])
    if data.now_pressure is not None:
        rows.append(["📈 气压", f"{format_weather_number(data.now_pressure)}hPa"])
    if data.now_cloud is not None:
        rows.append(["☁️ 云量", f"{data.now_cloud}%"])
    if include_air and data.air_quality:
        aqi = data.air_quality
        air_bits = []
        if aqi.aqi is not None:
            air_bits.append(str(aqi.aqi))
        if aqi.category:
            air_bits.append(aqi.category)
        if aqi.pm2p5 is not None:
            air_bits.append(f"PM2.5 {format_weather_number(aqi.pm2p5)}")
        if air_bits:
            rows.append(["🌫️ 空气", " · ".join(air_bits)])
    return rows


def _today_detail_blocks(data: WeatherData) -> List[dict]:
    day = data.get_current_daily_forecast()
    if day is None:
        return []

    rows: List[list] = [
        [
            "🌡️ 气温",
            f"{format_weather_number(day.temp_min)}~{format_weather_number(day.temp_max)}°C",
        ]
    ]
    day_wind = _plain_wind(
        day.wind_dir_day, day.wind_direction_day_degrees, day.wind_scale_day, day.wind_speed_day
    )
    night_wind = _plain_wind(
        day.wind_dir_night,
        day.wind_direction_night_degrees,
        day.wind_scale_night,
        day.wind_speed_night,
    )
    rows.append(["☀️ 日间", f"{weather_icon(day.icon_day)} {day.text_day}" + (f"（{day_wind}）" if day_wind else "")])
    rows.append(["🌙 夜间", f"{weather_icon(day.icon_night)} {day.text_night}" + (f"（{night_wind}）" if night_wind else "")])
    if day.temp_avg is not None:
        rows.append(["🌡️ 日均温", f"{format_weather_number(day.temp_avg)}°C"])
    if day.sunrise or day.sunset:
        rows.append(["🌅 日出/日落", f"{day.sunrise or 'N/A'} / {day.sunset or 'N/A'}"])
    if day.moon_phase or day.moon_rise or day.moon_set:
        moon_bits = [day.moon_phase] if day.moon_phase else []
        if day.moon_rise or day.moon_set:
            moon_bits.append(f"{day.moon_rise or 'N/A'} / {day.moon_set or 'N/A'}")
        rows.append(["🌙 月相/月升落", " · ".join(moon_bits)])
    if day.uv_index:
        rows.append(["☀️ 紫外线", str(day.uv_index)])
    if day.precip_day is not None or day.precip_night is not None:
        rows.append([
            "☔️ 昼/夜降水",
            f"{format_precip_value(day.precip_day, day.precip_kind)} / "
            f"{format_precip_value(day.precip_night, day.precip_kind)}",
        ])

    # Day-over-day context: the trend is what people actually want to know.
    if data.yesterday is not None:
        deltas = []
        if data.yesterday.temp_max is not None and day.temp_max is not None:
            delta = day.temp_max - data.yesterday.temp_max
            deltas.append(f"最高 {'+' if delta >= 0 else ''}{format_weather_number(delta)}°")
        if data.yesterday.temp_min is not None and day.temp_min is not None:
            delta = day.temp_min - data.yesterday.temp_min
            deltas.append(f"最低 {'+' if delta >= 0 else ''}{format_weather_number(delta)}°")
        if deltas:
            rows.append(["📊 比昨天", " · ".join(deltas)])

    pops = [hour.pop for hour in data.hourly[:6] if hour.pop is not None]
    if pops:
        rows.append(["☔️ 未来6h降概", f"{int(max(pops))}%"])
    if day.precip is not None:
        rows.append(["💧 全天降水", format_precip_value(day.precip, day.precip_kind)])

    blocks = [table(rows, aligns=["left", "left"])]

    tips = []
    wanted = {"3": "🧥", "8": "😊", "2": "🚗", "5": "🕶️", "9": "🤒"}
    for index in data.indices:
        if index.type in wanted:
            tips.append(paragraph([f"{wanted[index.type]} ", bold(index.name), f": {index.category}"]))
    if tips:
        blocks.append(bullet_list(tips))

    title = "今日详情" if day.date.date() == data.local_update_date else "最近预报"
    return [
        details(
            f"📅 {title}（{day.date.strftime('%m-%d')} {_weekday_cn(day.date)}）",
            blocks,
            is_open=True,
        )
    ]


def build_realtime_blocks(data: WeatherData) -> List[dict]:
    blocks = build_header(data)

    hero: list = [bold(f"{format_weather_number(data.now_temp)}°C")]
    if data.now_text:
        hero.append(f" {data.now_text}")
    if data.now_feels_like is not None:
        hero.append(f"（体感 {format_weather_number(data.now_feels_like)}°C）")
    blocks.append(paragraph(hero))

    for line in _display_summary_lines(data.summary):
        blocks.append(paragraph(italic(line)))

    blocks.extend(build_alert_blocks(data))

    # The pollutant breakdown carries its own always-visible summary line, so
    # the stats table drops its duplicate air row when that block is present.
    air_blocks = build_air_quality_blocks(data)
    rows = _current_stats_rows(data, include_air=not air_blocks)
    if rows:
        blocks.append(table(rows, aligns=["left", "left"]))

    blocks.extend(_today_detail_blocks(data))
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
            weather_icon(hour.icon),
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
            "🔬 更多逐小时指标（露点/气压/云量/能见度/AQI）",
            [
                table(
                    rows,
                    headers=["时间", *[label for label, _getter in active]],
                    aligns=["left", *["right"] * len(active)],
                    caption="气压单位 hPa",
                )
            ],
        )
    ]


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
        weather_cell = weather_icon(day.icon_day)
        if day.icon_night and day.icon_night != day.icon_day:
            weather_cell = f"{weather_icon(day.icon_day)}→{weather_icon(day.icon_night)}"
        rows.append([
            f"{day.date.strftime('%m-%d')} {_weekday_cn(day.date)}",
            weather_cell,
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
        blocks.append(details("🔎 逐日文字描述", [bullet_list(detail_items)]))

    blocks.append(build_footer(data))
    return blocks


def _indices_group_blocks(indices: List) -> List[dict]:
    groups: List[dict] = []
    for category_name, type_ids in CATEGORIES.items():
        group = [index for index in indices if index.type in type_ids]
        if not group:
            continue
        groups.append(heading(category_name, size=4))
        items = []
        for index in group:
            emoji = INDICES_EMOJI.get(index.type, "ℹ️")
            entry = [paragraph([f"{emoji} ", bold(index.name), f": {index.category}"])]
            if index.text:
                entry.append(paragraph(italic(index.text)))
            items.append(entry)
        groups.append(bullet_list(items))
    return groups


def build_indices_blocks(data: WeatherData) -> List[dict]:
    if not data.indices:
        return build_realtime_blocks(data)

    blocks = build_header(data, "生活指数")
    today = select_indices_for_day(data.indices)
    blocks.extend(_indices_group_blocks(today))

    # With a 3-day range the later days go into collapsed sections instead of
    # repeating every index three times in one wall of text.
    dated = [index for index in data.indices if index.date is not None]
    days = sorted({index.date.date() for index in dated})
    for offset, day in enumerate(days[1:3], start=1):
        label = "明天" if offset == 1 else "后天"
        group_blocks = _indices_group_blocks(select_indices_for_day(data.indices, day))
        if group_blocks:
            blocks.append(details(f"💡 {label}（{day.strftime('%m-%d')}）", group_blocks))

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
        heading("🚨 降雨提醒", size=2),
        paragraph([bold(data.location_name), f" · {data.update_time.strftime('%m-%d %H:%M')}"]),
        *build_alert_blocks(data),
        *build_rain_blocks(data)[1:],
    ]


def build_alert_push_blocks(data: WeatherData, alert) -> List[dict]:
    """One official warning, pushed on its own so it cannot be scrolled past."""
    level = normalize_warning_level(alert.level)
    title = alert.title if not level or level in alert.title else f"{alert.title}（{level}）"

    blocks: List[dict] = [
        heading(f"⚠️ {title}", size=2),
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


def build_event_push_blocks(data: WeatherData, title: str, detail: str) -> List[dict]:
    """Threshold event (air quality / temperature / wind) push."""
    return [
        heading(title, size=2),
        paragraph([bold(data.location_name), f" · {data.update_time.strftime('%m-%d %H:%M')}"]),
        paragraph(detail),
        build_footer(data),
    ]


def build_typhoon_push_blocks(threat, location_name: str) -> List[dict]:
    """Tropical cyclone alert: what it is, where it is, and where it goes next."""
    from services.typhoon import format_threat_summary, hours_until, storm_type_label

    storm = threat.storm
    now_point = storm.now

    blocks: List[dict] = [heading(f"🌀 {format_threat_summary(threat)}", size=2)]
    blocks.append(paragraph([bold(location_name), f" · 距中心约 {threat.distance_km:.0f}km"]))
    if threat.inside_circle:
        blocks.append(paragraph(marked(f"⚠️ 已进入{threat.wind_label}，请做好防风准备")))

    if now_point is not None:
        rows = [["🌀 强度", storm_type_label(now_point.type)]]
        if now_point.wind_speed is not None:
            rows.append(["💨 中心风速", f"{format_weather_number(now_point.wind_speed)} km/h"])
        if now_point.pressure is not None:
            rows.append(["📉 中心气压", f"{format_weather_number(now_point.pressure)} hPa"])
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


def build_report_blocks(title: str, report_html: str, *, in_progress: bool = False) -> List[dict]:
    """AI report as rich blocks.

    The report body is already Telegram HTML, so it rides in a paragraph-free
    ``html`` payload elsewhere; here we only need the streaming variant, which
    uses the dedicated "thinking" block while text is still arriving.
    """
    blocks: List[dict] = [heading(title, size=2)]
    if in_progress:
        blocks.append(thinking(report_html))
    else:
        blocks.append(paragraph(report_html))
    return blocks
