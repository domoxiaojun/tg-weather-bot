import datetime
from typing import Optional, List
from numbers import Number

from telegram.helpers import escape_markdown
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from domain.models import (
    DailyForecast,
    HourlyForecast,
    LifeIndex,
    MinutelyPrecipitation,
    WeatherData,
    normalize_warning_level,
)

# --- Constants & Mappings ---

WEATHER_ICONS = {
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

INDICES_EMOJI = {
    "1": "🏃", "2": "🚗", "3": "👕", "4": "🎣", "5": "☀️", "6": "🏞️",
    "7": "🤧", "8": "😊", "9": "🤒", "10": "🌫️", "11": "❄️", "12": "🕶️",
    "13": "💄", "14": "👔", "15": "🚦", "16": "🧴",
}

CATEGORIES = {
    "户外活动": ["1", "4", "6"],
    "出行建议": ["2", "15"],
    "生活起居": ["3", "8", "11", "14"],
    "健康关注": ["7", "9", "10"],
    "美妆护理": ["5", "12", "13", "16"],
}

# --- Markdown Utilities (Optimized for Safety) ---

def escape_v2(text: str | Number) -> str:
    """Escapes text for Telegram MarkdownV2."""
    if text is None:
        return ""
    return escape_markdown(str(text), version=2)


def format_weather_number(value: Optional[Number], decimals: int = 1, fallback: str = "N/A") -> str:
    """Format API numbers compactly before Telegram escaping."""
    if value is None:
        return fallback
    number = float(value)
    if number.is_integer():
        return str(int(number))
    if decimals <= 0:
        return str(int(round(number)))
    return f"{number:.{decimals}f}".rstrip("0").rstrip(".")


def format_precip_value(value: Optional[Number], kind: Optional[str], decimals: int = 2) -> str:
    """Format precipitation without mixing accumulation and intensity units."""
    if value is None:
        return "N/A"
    unit = "mm/h" if kind == "intensity" else "mm"
    return f"{format_weather_number(value, decimals=decimals)}{unit}"


def format_attribution(data, limit: int = 2) -> str:
    """Provider attribution line.

    QWeather's terms make attribution a licensing requirement, not an optional
    credit, so every weather surface must carry it (see
    docs/qweather-api-reference-2026-07.md).
    """
    entries = [entry.strip() for entry in (getattr(data, "attributions", None) or []) if entry.strip()]
    if not entries:
        return ""
    shown = entries[:limit]
    suffix = " 等" if len(entries) > limit else ""
    return f"{' · '.join(shown)}{suffix}"


def weather_icon(icon: str) -> str:
    """Support both QWeather icon codes and Caiyun emoji fallbacks."""
    if not icon:
        return "❓"
    return WEATHER_ICONS.get(icon, icon)


def alert_level_suffix(title: str, level: str) -> str:
    """Render a classified alert level once, suppressing provider placeholders."""
    label = normalize_warning_level(level)
    if not label or label in title:
        return ""
    return f" \\({escape_v2(label)}\\)"

def foldable_text_v2(body_lines: List[str], folding_threshold: int = 8) -> str:
    """Formats a list of escaped strings into a MarkdownV2 foldable block."""
    if len(body_lines) <= folding_threshold:
        return "\n".join(body_lines)

    first = f"**> {body_lines[0]}"
    rest = [f"> {line}" for line in body_lines[1:]]
    all_lines = [first, *rest]
    
    if all_lines:
        if all_lines[-1].endswith("||"):
             all_lines[-1] += " ||"
        else:
             all_lines[-1] += "||"
             
    return "\n".join(all_lines)


_WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _weekday_cn(value) -> str:
    return _WEEKDAYS_CN[value.weekday()]


def _display_summary_lines(summary: Optional[str]) -> List[str]:
    """Summary lines worth showing; drops the auto '当前 X，温度 Y' duplicate."""
    if not summary:
        return []
    lines = []
    for line in summary.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("当前 ") and "温度" in line:
            continue
        lines.append(line)
    return lines


def _wind_parts(
    direction: Optional[str],
    degrees: Optional[Number],
    scale: Optional[str],
    speed: Optional[Number],
) -> List[str]:
    """统一的风况拼装：方向（或度数回退）、风力等级、风速。已完成 MarkdownV2 转义。"""
    parts: List[str] = []
    if direction:
        parts.append(escape_v2(direction))
    elif degrees is not None:
        parts.append(f"{escape_v2(format_weather_number(degrees, 0))}°")
    if scale:
        parts.append(f"{escape_v2(scale)}级")
    if speed is not None:
        parts.append(f"{escape_v2(format_weather_number(speed))}km/h")
    return parts


def _day_display_fields(day: DailyForecast) -> dict:
    """今日详情与多日预报共用的字段准备（取值、回退与转义）。"""
    day_wind = " ".join(
        _wind_parts(day.wind_dir_day, day.wind_direction_day_degrees, day.wind_scale_day, day.wind_speed_day)
    )
    night_wind = " ".join(
        _wind_parts(day.wind_dir_night, day.wind_direction_night_degrees, day.wind_scale_night, day.wind_speed_night)
    )
    return {
        "date_str": day.date.strftime("%m-%d"),
        "weekday": _weekday_cn(day.date),
        "moon": escape_v2(day.moon_phase) if day.moon_phase else "",
        "temp_min": escape_v2(format_weather_number(day.temp_min)),
        "temp_max": escape_v2(format_weather_number(day.temp_max)),
        "day_icon": weather_icon(day.icon_day),
        "text_day": escape_v2(day.text_day),
        "day_wind": day_wind or "N/A",
        "night_icon": weather_icon(day.icon_night),
        "text_night": escape_v2(day.text_night),
        "night_wind": night_wind or "N/A",
        "humid": escape_v2(day.humidity if day.humidity is not None else "N/A"),
        "precip": escape_v2(format_precip_value(day.precip, day.precip_kind)),
        "sunrise": escape_v2(day.sunrise or "N/A"),
        "sunset": escape_v2(day.sunset or "N/A"),
        "vis": escape_v2(format_weather_number(day.vis)) if day.vis is not None else "N/A",
        "uv": escape_v2(day.uv_index or "N/A"),
    }


# --- Formatters (With Strict Manual Escaping) ---

def format_realtime_weather(data: WeatherData) -> str:
    lines = [
        f"🌍 *{escape_v2(data.location_name)}*",
        f"🕐 {escape_v2(data.update_time.strftime('%m-%d %H:%M'))} 更新",
        "",
    ]
    
    for line in _display_summary_lines(data.summary):
        lines.append(f"*{escape_v2(line)}*")

    lines.append("")
    temperature_line = f"🌡️ 温度: *{escape_v2(format_weather_number(data.now_temp))}°C*"
    if data.now_feels_like is not None:
        temperature_line += (
            f" \\(体感 {escape_v2(format_weather_number(data.now_feels_like))}°C\\)"
        )
    lines.append(temperature_line)
    lines.append(f"🌤️ 天气: {weather_icon(data.now_icon)} {escape_v2(data.now_text or '暂无描述')}")

    wind_parts = _wind_parts(
        data.now_wind_dir,
        data.now_wind_direction_degrees,
        data.now_wind_scale,
        data.now_wind_speed,
    )
    if wind_parts:
        lines.append(f"💨 风况: {' '.join(wind_parts)}")

    moisture_parts = []
    if data.now_humidity is not None:
        moisture_parts.append(f"💧 湿度: {escape_v2(data.now_humidity)}%")
    if data.now_precip is not None:
        moisture_parts.append(
            f"☔️ 降水: {escape_v2(format_precip_value(data.now_precip, data.now_precip_kind))}"
        )
    if moisture_parts:
        lines.append(" \\| ".join(moisture_parts))

    environment_parts = []
    if data.now_vis is not None:
        environment_parts.append(f"👁️ 能见度: {escape_v2(format_weather_number(data.now_vis))}km")
    if data.now_pressure is not None:
        environment_parts.append(f"📈 气压: {escape_v2(format_weather_number(data.now_pressure))}hPa")
    if environment_parts:
        lines.append(" \\| ".join(environment_parts))
    if data.now_cloud is not None or data.now_radiation is not None:
        extra_parts = []
        if data.now_cloud is not None:
            extra_parts.append(f"☁️ 云量: {escape_v2(data.now_cloud)}%")
        if data.now_radiation is not None:
            extra_parts.append(f"☀️ 辐射: {escape_v2(format_weather_number(data.now_radiation))}W/m²")
        lines.append(" \\| ".join(extra_parts))
    
    if data.air_quality:
        aqi = data.air_quality
        air_parts = []
        if aqi.aqi is not None:
            air_parts.append(f"*{escape_v2(aqi.aqi)}*")
        if aqi.category:
            air_parts.append(f"\\({escape_v2(aqi.category)}\\)")
        if aqi.pm2p5 is not None:
            air_parts.append(f"PM2\\.5: {escape_v2(format_weather_number(aqi.pm2p5))}")
        if air_parts:
            lines.append(f"🌫️ 空气: {' '.join(air_parts)}")
    
    if data.alerts:
        lines.append("")
        for a in data.alerts:
            level = alert_level_suffix(a.title, a.level)
            lines.append(f"⚠️ *{escape_v2(a.title)}*{level}")
    
    return "\n".join(lines)

def format_forecast_header(data: WeatherData, title: Optional[str] = None) -> str:
    lines = [
        f"🌍 *{escape_v2(data.location_name)}*",
        f"🕐 {escape_v2(data.update_time.strftime('%m-%d %H:%M'))} 更新",
    ]
    if title:
        lines.append(f"📅 *{escape_v2(title)}*")
    return "\n".join(lines)

def format_today_detail(
    day: DailyForecast,
    indices: List[LifeIndex],
    hourly_data: List[HourlyForecast],
    title: str = "今日详情",
) -> str:
    """专门为今日详情设计的格式，块状布局而非树状"""
    fields = _day_display_fields(day)
    date_str = fields["date_str"]
    moon = fields["moon"]
    temp_min = fields["temp_min"]
    temp_max = fields["temp_max"]
    day_icon = fields["day_icon"]
    text_day = fields["text_day"]
    day_wind = fields["day_wind"]
    night_icon = fields["night_icon"]
    text_night = fields["text_night"]
    night_wind = fields["night_wind"]
    humid = fields["humid"]
    precip = fields["precip"]
    sunrise = fields["sunrise"]
    sunset = fields["sunset"]
    vis = fields["vis"]
    uv = fields["uv"]

    # 计算未来6小时降水概率（始终显示）
    max_pop = None
    if hourly_data:
        future_6h = hourly_data[:6]
        available_pops = [h.pop for h in future_6h if h.pop is not None]
        max_pop = max(available_pops) if available_pops else None

    weekday = fields["weekday"]
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 *{escape_v2(title)} \\({escape_v2(date_str)} {weekday}\\)*",
        f"🌡️ 气温: {temp_min}\\~{temp_max}°C \\| 🌙 {moon} \\(日出 {sunrise} / 日落 {sunset}\\)",
        "",
        f"☀️ 日间: {day_icon} {text_day} \\({day_wind}\\)",
        f"🌙 夜间: {night_icon} {text_night} \\({night_wind}\\)",
        "",
    ]
    stats_parts = [f"☔️ 降水 {precip}"]
    if fields["humid"] != "N/A":
        stats_parts.append(f"💧 湿度 {humid}%")
    if fields["vis"] != "N/A":
        stats_parts.append(f"👁️ 能见度 {vis}km")
    lines.append(" \\| ".join(stats_parts))

    forecast_parts = []
    if fields["uv"] != "N/A":
        forecast_parts.append(f"☀️ UV {uv}")
    if max_pop is not None:
        forecast_parts.append(f"未来6h降概 {escape_v2(int(max_pop))}%")
    if day.precip_day_probability is not None:
        forecast_parts.append(f"白天降概 {escape_v2(int(day.precip_day_probability))}%")
    if day.precip_night_probability is not None:
        forecast_parts.append(f"夜间降概 {escape_v2(int(day.precip_night_probability))}%")
    if forecast_parts:
        lines.append(" \\| ".join(forecast_parts))
    
    # 生活指数
    tips = []
    target_indices = {"3": "🧥", "8": "😊", "2": "🚗"}
    
    if indices:
        for idx in indices:
             if idx.type in target_indices:
                 tips.append(f"{target_indices[idx.type]} {escape_v2(idx.name)}: {escape_v2(idx.category)}")
    
    if tips:
        tips_str = " \\| ".join(tips)
        lines.append(f"💡 贴士: {tips_str}")
    
    return "\n".join(lines)

def format_daily_weather(daily_data: List[DailyForecast]) -> str:
    """用于多日预报的树状格式"""
    result_lines = []
    for day in daily_data:
        fields = _day_display_fields(day)
        title = f"🗓 *{escape_v2(fields['date_str'])} {fields['weekday']}*"
        if fields["moon"]:
            title += f" · {fields['moon']}"
        daily_info = [
            title,
            f"{fields['day_icon']} {fields['text_day']} → {fields['night_icon']} {fields['text_night']}"
            f" · {fields['temp_min']}\\~{fields['temp_max']}°C",
        ]

        if fields["day_wind"] != "N/A" and fields["night_wind"] not in ("N/A", fields["day_wind"]):
            daily_info.append(f"💨 {fields['day_wind']}（夜间 {fields['night_wind']}）")
        elif fields["day_wind"] != "N/A":
            daily_info.append(f"💨 {fields['day_wind']}")

        stats_parts = [f"☔️ 降水 {fields['precip']}"]
        if fields["humid"] != "N/A":
            stats_parts.append(f"💧 {fields['humid']}%")
        if fields["uv"] != "N/A":
            stats_parts.append(f"☀️ UV {fields['uv']}")
        if fields["vis"] != "N/A":
            stats_parts.append(f"👁️ {fields['vis']}km")
        daily_info.append(" · ".join(stats_parts))

        if day.sunrise or day.sunset:
            daily_info.append(f"🌅 {fields['sunrise']} / 🌇 {fields['sunset']}")

        result_lines.append("\n".join(daily_info))
    return "\n\n".join(result_lines)

def format_unavailable_daily_weather(data: WeatherData, start_day: int, limit: int) -> str:
    target_date = data.local_update_date + datetime.timedelta(days=max(0, start_day))
    request_text = target_date.strftime("%m-%d")
    if limit > 1:
        end_date = target_date + datetime.timedelta(days=limit - 1)
        request_text = f"{request_text}~{end_date.strftime('%m-%d')}"

    if data.daily:
        available_start = data.daily[0].date.strftime("%m-%d")
        available_end = data.daily[-1].date.strftime("%m-%d")
        available_text = f"当前可查 {available_start}~{available_end}"
    else:
        available_text = "当前没有可用的逐日预报数据"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"⚠️ *暂无 {escape_v2(request_text)} 的逐日预报*",
        f"{escape_v2(available_text)}，请改查范围内日期。",
        f"如需更远预报，请将 {escape_v2('QWEATHER_DAILY_DAYS')} 配成 {escape_v2('15d')} 并确认接口套餐支持。",
    ]
    return "\n".join(lines)


def format_unavailable_current_daily_weather(data: WeatherData) -> str:
    date_text = data.local_update_date.strftime("%m-%d")
    if data.daily:
        latest_date = max(day.date for day in data.daily).strftime("%m-%d")
        detail = f"供应商最新逐日数据停留在 {latest_date}，已停止把过期预报显示为今日。"
    else:
        detail = "供应商当前没有返回可用的逐日预报数据。"
    return "\n".join(
        [
            "━━━━━━━━━━━━━━━━━━━━",
            f"⚠️ *暂无 {escape_v2(date_text)} 的今日预报*",
            escape_v2(detail),
        ]
    )

def format_hourly_weather(hourly_data: List[HourlyForecast]) -> str:
    """紧凑两行/小时的逐小时预报，跨天时插入日期分隔行。"""
    result_lines = []
    previous_date = None
    for hour in hourly_data:
        hour_date = hour.time.date() if hasattr(hour.time, "date") else None
        if hour_date is not None and previous_date is not None and hour_date != previous_date:
            result_lines.append(
                f"—— {escape_v2(hour.time.strftime('%m-%d'))} {_weekday_cn(hour.time)} ——"
            )
        previous_date = hour_date

        time_str = escape_v2(hour.time.strftime("%H:%M"))
        temp = escape_v2(format_weather_number(hour.temp))
        icon = weather_icon(hour.icon)
        text = escape_v2(hour.text)
        precip = escape_v2(format_precip_value(hour.precip, hour.precip_kind))

        head = f"⏰ {time_str} {icon} {text} · 🌡️ {temp}°C"
        if hour.feels_like is not None and not hour.feels_like_estimated:
            feels_like = escape_v2(format_weather_number(hour.feels_like))
            head += f" \\(体感 {feels_like}°C\\)"

        if hour.pop is not None:
            pop = escape_v2(format_weather_number(hour.pop, decimals=0))
            detail_parts = [f"☔️ 降概 {pop}% / 降水 {precip}"]
        else:
            detail_parts = [f"☔️ 降水 {precip}"]
        if hour.humidity is not None:
            detail_parts.append(f"💧 {escape_v2(hour.humidity)}%")
        wind_parts = _wind_parts(
            hour.wind_dir,
            hour.wind_direction_degrees,
            hour.wind_scale,
            hour.wind_speed,
        )
        if wind_parts:
            detail_parts.append(f"💨 {' '.join(wind_parts)}")
        if hour.uv_index is not None:
            detail_parts.append(f"☀️ UV {escape_v2(format_weather_number(hour.uv_index))}")

        result_lines.append(head)
        result_lines.append(" · ".join(detail_parts))
    return "\n".join(result_lines)

def format_indices_data(indices: List[LifeIndex]) -> str:
    if not indices: return ""
    result = []
    for category_name, type_ids in CATEGORIES.items():
        category_indices = [idx for idx in indices if idx.type in type_ids]
        if category_indices:
            result.append(f"\n*【{escape_v2(category_name)}】*")
            for index in category_indices:
                emoji = INDICES_EMOJI.get(index.type, "ℹ️")
                result.append(f"{emoji} *{escape_v2(index.name)}*: {escape_v2(index.category)}")
                if index.text:
                    result.append(f"    ↳ {escape_v2(index.text)}")
    return "\n".join(result)

def format_minutely_weather(minutely: List[MinutelyPrecipitation], summary: str) -> str:
    result = [f"📝 {escape_v2(summary)}", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
    lines = []
    for m in minutely:
        time_str = escape_v2(m.time.strftime("%H:%M"))
        precip = escape_v2(format_weather_number(m.precip, decimals=2))
        precip_type = {"rain": "雨", "snow": "雪"}.get(m.precip_type or "", "降水")
        if m.probability is None:
            lines.append(f"⏰ {time_str} \\| 🌧️ {escape_v2(precip_type)} {precip}mm")
        else:
            prob = escape_v2(int(m.probability * 100))
            lines.append(f"⏰ {time_str} \\| 🌧️ {precip}mm \\(概率 {prob}%\\)")
    return "\n".join(result) + "\n" + foldable_text_v2(lines, folding_threshold=5)

def format_rain_weather(data: WeatherData) -> str:
    if data.minutely:
        return format_minutely_weather(data.minutely, data.summary)

    if data.hourly:
        result = [f"📝 {escape_v2(data.summary or '暂无分钟级降水，使用逐小时预报兜底')}", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        lines = []
        for h in data.hourly[:6]:
            time_str = escape_v2(h.time.strftime("%H:%M"))
            precip = escape_v2(format_precip_value(h.precip, h.precip_kind))
            if h.pop is not None:
                lines.append(f"⏰ {time_str} \\| 降水 {precip} \\| 降概 {escape_v2(int(h.pop))}%")
            else:
                lines.append(f"⏰ {time_str} \\| 降水 {precip}")
        return "\n".join(result) + "\n" + foldable_text_v2(lines, folding_threshold=5)

    return f"📝 {escape_v2(data.summary or '暂无可用降水预报')}"

def format_weather_response(data: WeatherData, view_type: str="default", days: Optional[int]=None, start_day: int=0) -> str:
    header = format_realtime_weather(data)
    body = ""
    if view_type == "daily":
        s_idx = max(0, start_day)
        available_daily = data.get_daily_forecasts(start_day=s_idx)
        limit = days if days else len(available_daily)
        daily_data = available_daily[:limit]
        if daily_data:
            if limit == 1:
                header = format_forecast_header(data, f"{daily_data[0].date.strftime('%m-%d')} 预报")
            body = format_daily_weather(daily_data)
        else:
            header = format_forecast_header(data)
            body = format_unavailable_daily_weather(data, s_idx, limit)
    elif view_type == "hourly":
        limit = days if days else 24
        body = format_hourly_weather(data.hourly[:limit])
        body = foldable_text_v2(body.split("\n"), folding_threshold=10)
    elif view_type == "indices":
        body = format_indices_data(data.indices)
    elif view_type == "rain":
        body = format_rain_weather(data)
    else:
        # Default: 使用专门的今日详情格式
        current_day = data.get_current_daily_forecast()
        if current_day:
            detail_title = (
                "今日详情"
                if current_day.date.date() == data.local_update_date
                else "最近预报"
            )
            body = format_today_detail(
                current_day,
                data.indices,
                data.hourly,
                title=detail_title,
            )
        else:
            body = format_unavailable_current_daily_weather(data)
    
    source_label = {
        "qweather": "和风天气",
        "caiyun": "彩云天气",
        "fusion": "和风天气 & 彩云天气",
    }.get(data.source, data.source.title())
    footer_text = f"数据源: {source_label}"
    attribution = format_attribution(data)
    if attribution:
        footer_text = f"{footer_text} · {attribution}"
    return f"{header}\n\n{body}\n\n_{escape_v2(footer_text)}_"

# Telegram limits callback_data to 64 bytes; the longest pattern is
# "refresh|{token}|indices|0|24", so the location token itself must stay small.
_CALLBACK_LOCATION_MAX_BYTES = 40

# In-place switchable views: (view_type, label, start_day, limit)
_VIEW_SWITCHES = (
    ("default", "🌤 实时", 0, 0),
    ("hourly", "⏰ 逐小时", 0, 24),
    ("daily", "📅 未来7天", 0, 7),
    ("indices", "💡 指数", 0, 0),
)


def styled_button(text: str, style: Optional[str] = None, **kwargs) -> InlineKeyboardButton:
    """Inline button with an optional Bot API 10.x colour.

    ``style`` is not typed by PTB 22.8, so it rides along via ``api_kwargs``
    (verified to reach ``to_dict``); older clients simply ignore it. Only
    semantic actions get colour — colouring everything is noise.
    """
    if style:
        kwargs["api_kwargs"] = {"style": style}
    return InlineKeyboardButton(text, **kwargs)


def callback_location_token(location_query: str, coords: Optional[str] = None) -> str:
    """Return a callback-safe location token, falling back to coordinates."""
    if len(location_query.encode("utf-8")) <= _CALLBACK_LOCATION_MAX_BYTES:
        return location_query
    if coords and len(coords.encode("utf-8")) <= _CALLBACK_LOCATION_MAX_BYTES:
        return coords
    encoded = location_query.encode("utf-8")[:_CALLBACK_LOCATION_MAX_BYTES]
    return encoded.decode("utf-8", errors="ignore")


def get_weather_keyboard(
    location_query: str,
    mode: str = "default",
    show_charts: bool = True,
    coords: Optional[str] = None,
    view_type: str = "default",
) -> InlineKeyboardMarkup:
    """
    生成天气消息的按钮键盘
    :param mode: 'default' (文本模式), 'chart' (图表模式，显示返回按钮)
    :param show_charts: 是否显示图表切换按钮 (Inline模式下因无法切图，建议关闭)
    :param coords: 坐标字符串，地名过长超出 callback_data 限制时作为回退
    :param view_type: 当前视图；刷新保持该视图，切换按钮隐藏当前项
    """
    token = callback_location_token(location_query, coords)
    if mode == "chart":
        # 图表模式：三种图表可互切，并提供回到文字天气的出口。
        keyboard = [
            [
                InlineKeyboardButton("🌡️ 温度趋势", callback_data=f"chart|{token}|temp"),
                InlineKeyboardButton("🌧️ 降水趋势", callback_data=f"chart|{token}|rain"),
                InlineKeyboardButton("📅 逐日图", callback_data=f"chart|{token}|daily"),
            ],
            [InlineKeyboardButton("📝 文字天气", callback_data=f"tq|{token}|default|0|0")],
        ]
        return InlineKeyboardMarkup(keyboard)

    known_views = {view for view, _, _, _ in _VIEW_SWITCHES}
    current_view = view_type if view_type in known_views else "default"
    current_args = next(
        (view, start, limit) for view, _, start, limit in _VIEW_SWITCHES if view == current_view
    )

    # 第一排：基础功能（刷新携带当前视图，刷新后不丢失展示形态）
    row1 = [
        InlineKeyboardButton(
            "🔄 刷新",
            callback_data=f"refresh|{token}|{current_args[0]}|{current_args[1]}|{current_args[2]}",
        ),
        styled_button("🔔 降雨提醒", style="success", callback_data=f"sub|{token}"),
    ]
    keyboard = [row1]

    # 第二排：视图切换（原地编辑消息，不刷屏；不显示当前视图）
    view_row = [
        InlineKeyboardButton(label, callback_data=f"view|{token}|{view}|{start}|{limit}")
        for view, label, start, limit in _VIEW_SWITCHES
        if view != current_view
    ]
    keyboard.append(view_row)

    # 第三排：图表按钮 (可选)
    if show_charts:
        keyboard.append([
            InlineKeyboardButton("🌡️ 温度图", callback_data=f"chart|{token}|temp"),
            InlineKeyboardButton("🌧️ 降水图", callback_data=f"chart|{token}|rain"),
            InlineKeyboardButton("📆 逐日图", callback_data=f"chart|{token}|daily"),
        ])

    # 第四排：AI 日报 + 分享给别人（switch_inline_query 让用户选聊天后直接发天气卡片）
    keyboard.append([
        styled_button("🤖 AI日报", style="primary", callback_data=f"report|{token}"),
        InlineKeyboardButton("📤 分享", switch_inline_query=location_query),
    ])

    return InlineKeyboardMarkup(keyboard)
