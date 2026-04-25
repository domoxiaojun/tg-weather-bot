import re
import datetime
import logging
from typing import Optional, List, Tuple
from numbers import Number

from telegram.helpers import escape_markdown
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from domain.models import WeatherData, DailyForecast, HourlyForecast, LifeIndex, MinutelyPrecipitation

logger = logging.getLogger(__name__)

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

def foldable_text_v2(body_lines: List[str], folding_threshold: int = 8) -> str:
    """Formats a list of escaped strings into a MarkdownV2 foldable block."""
    if len(body_lines) <= folding_threshold:
        return "\n".join(body_lines)
    
    if not body_lines:
        return ""

    first = f"**> {body_lines[0]}"
    rest = [f"> {line}" for line in body_lines[1:]]
    all_lines = [first, *rest]
    
    if all_lines:
        if all_lines[-1].endswith("||"):
             all_lines[-1] += " ||"
        else:
             all_lines[-1] += "||"
             
    return "\n".join(all_lines)


# --- Formatters (With Strict Manual Escaping) ---

def format_realtime_weather(data: WeatherData) -> str:
    lines = [
        f"🌍 *{escape_v2(data.location_name)}*",
        f"🕐 {escape_v2(data.update_time.strftime('%m-%d %H:%M'))} 更新",
        "",
    ]
    
    if data.summary:
        summary_lines = data.summary.split('\n')
        for line in summary_lines:
            lines.append(f"**{escape_v2(line)}**")
        
    lines.extend([
        "",
        f"🌡️ 温度: *{escape_v2(data.now_temp)}°C* \\(体感 {escape_v2(data.now_feels_like)}°C\\)",
        f"🌤️ 天气: {data.now_icon} {escape_v2(data.now_text)}",
        f"💨 风况: {escape_v2(data.now_wind_dir)} {escape_v2(data.now_wind_scale)}级",
        f"💧 湿度: {escape_v2(data.now_humidity)}% \\| ☔️ 降水: {escape_v2(data.now_precip)}mm",
        f"👁️ 能见度: {escape_v2(data.now_vis)}km \\| 📈 气压: {escape_v2(data.now_pressure)}hPa",
    ])
    
    if data.air_quality:
        aqi = data.air_quality
        lines.append(f"🌫️ 空气: *{escape_v2(aqi.aqi)}* \\({escape_v2(aqi.category)}\\) PM2\\.5: {escape_v2(aqi.pm2p5)}")
    
    if data.alerts:
        lines.append("")
        for a in data.alerts:
            lines.append(f"⚠️ *{escape_v2(a.title)}* \\({escape_v2(a.level)}\\)")
    
    return "\n".join(lines)

def format_today_detail(day: DailyForecast, indices: List[LifeIndex], hourly_data: List[HourlyForecast]) -> str:
    """专门为今日详情设计的格式，块状布局而非树状"""
    date_str = day.date.strftime("%m-%d")
    moon = escape_v2(day.moon_phase) if day.moon_phase else ""
    
    temp_min = escape_v2(day.temp_min)
    temp_max = escape_v2(day.temp_max)
    
    day_icon = WEATHER_ICONS.get(day.icon_day, "❓")
    text_day = escape_v2(day.text_day)
    wdir_d = escape_v2(day.wind_dir_day or "N/A")
    wscale_d = escape_v2(day.wind_scale_day or "")
    
    night_icon = WEATHER_ICONS.get(day.icon_night, "❓")
    text_night = escape_v2(day.text_night)
    wdir_n = escape_v2(day.wind_dir_night or "N/A")
    wscale_n = escape_v2(day.wind_scale_night or "")
    
    humid = escape_v2(day.humidity or "N/A")
    precip = escape_v2(day.precip)
    sunrise = escape_v2(day.sunrise or "N/A")
    sunset = escape_v2(day.sunset or "N/A")
    vis = escape_v2(day.vis or "N/A")
    uv = escape_v2(day.uv_index or "N/A")
    
    # 计算未来6小时降水概率（始终显示）
    max_pop = 0
    if hourly_data:
        future_6h = hourly_data[:6]
        max_pop = max([h.pop for h in future_6h if h.pop is not None], default=0)
    
    lines = [
        "━━━━━━━━━━━━━━━━━━━━",
        f"📅 *今日详情 \\({escape_v2(date_str)}\\)*",
        f"🌡️ 气温: {temp_min}\\~{temp_max}°C \\| 🌙 {moon} \\(日出 {sunrise} / 日落 {sunset}\\)",
        "",
        f"☀️ 日间: {day_icon} {text_day} \\({wdir_d} {wscale_d}级\\)",
        f"🌙 夜间: {night_icon} {text_night} \\({wdir_n} {wscale_n}级\\)",
        "",
        f"💧 统计: 降水 {precip}mm \\| 湿度 {humid}% \\| 能见度 {vis}km",
        f"☔️ 预报: UV {uv} \\| 未来6h降水 {escape_v2(int(max_pop))}%",
    ]
    
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
        date_str = day.date.strftime("%m-%d")
        moon = escape_v2(day.moon_phase) if day.moon_phase else ""
        
        temp_min = escape_v2(day.temp_min)
        temp_max = escape_v2(day.temp_max)
        
        day_icon = WEATHER_ICONS.get(day.icon_day, "❓")
        text_day = escape_v2(day.text_day)
        wdir_d = escape_v2(day.wind_dir_day or "N/A")
        wscale_d = escape_v2(day.wind_scale_day or "")
        
        night_icon = WEATHER_ICONS.get(day.icon_night, "❓")
        text_night = escape_v2(day.text_night)
        wdir_n = escape_v2(day.wind_dir_night or "N/A")
        wscale_n = escape_v2(day.wind_scale_night or "")
        
        humid = escape_v2(day.humidity or "N/A")
        precip = escape_v2(day.precip)
        sunrise = escape_v2(day.sunrise or "N/A")
        sunset = escape_v2(day.sunset or "N/A")
        vis = escape_v2(day.vis or "N/A")
        uv = escape_v2(day.uv_index or "N/A")
        
        daily_info = [
            f"🗓 *{escape_v2(date_str)} {moon}*",
            f"├─ 温度: {temp_min}\\~{temp_max}°C", 
            f"├─ 日间: {day_icon} {text_day}",
            f"│   └─ {wdir_d} {wscale_d}级",
            f"├─ 夜间: {night_icon} {text_night}",
            f"│   └─ {wdir_n} {wscale_n}级",
            "└─ 详情:",
            f"    💧 湿度: {humid}% \\| ☔️ 降水: {precip}mm",
            f"    🌅 日出: {sunrise} \\| 🌄 日落: {sunset}",
            f"    👁️ 能见度: {vis}km \\| ☀️ UV: {uv}",
        ]
        result_lines.append("\n".join(daily_info))
    return "\n\n".join(result_lines)

def format_hourly_weather(hourly_data: List[HourlyForecast]) -> str:
    result_lines = []
    for hour in hourly_data:
        time_str = escape_v2(hour.time.strftime("%H:%M"))
        temp = escape_v2(hour.temp)
        icon = WEATHER_ICONS.get(hour.icon, "❓")
        text = escape_v2(hour.text)
        pop = escape_v2(hour.pop if hour.pop is not None else "N/A")
        
        lines = [
            f"⏰ {time_str}",
            f"🌡️ {temp}°C {icon} {text}",
            f"💨 {escape_v2(hour.wind_dir)} {escape_v2(hour.wind_scale)}级",
            f"💧 湿度: {escape_v2(hour.humidity or 'N/A')}% \\| ☔️ 降概: {pop}%",
            "━━━━━━━━━━━━━━━━━━━━" 
        ]
        result_lines.append("\n".join(lines))
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
        precip = escape_v2(m.precip)
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
            precip = escape_v2(h.precip)
            pop = escape_v2(int(h.pop)) if h.pop is not None else "N/A"
            lines.append(f"⏰ {time_str} \\| 降水 {precip}mm \\| 降概 {pop}%")
        return "\n".join(result) + "\n" + foldable_text_v2(lines, folding_threshold=5)

    return f"📝 {escape_v2(data.summary or '暂无可用降水预报')}"

def format_weather_response(data: WeatherData, view_type: str="default", days: Optional[int]=None, start_day: int=0) -> str:
    header = format_realtime_weather(data)
    body = ""
    if view_type == "daily":
        limit = days if days else len(data.daily)
        s_idx = max(0, start_day)
        e_idx = s_idx + limit
        body = format_daily_weather(data.daily[s_idx:e_idx])
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
        if data.daily:
            body = "\n" + format_today_detail(data.daily[0], data.indices, data.hourly)
    
    source_label = {
        "qweather": "和风天气",
        "caiyun": "彩云天气",
        "fusion": "和风天气 & 彩云天气",
    }.get(data.source, data.source.title())
    return f"{header}\n\n{body}\n\n_数据源: {escape_v2(source_label)}_"

def get_weather_keyboard(location_query: str, mode: str = "default", show_charts: bool = True) -> InlineKeyboardMarkup:
    """
    生成天气消息的按钮键盘
    :param mode: 'default' (文本模式), 'chart' (图表模式，显示返回按钮)
    :param show_charts: 是否显示图表切换按钮 (Inline模式下因无法切图，建议关闭)
    """
    if mode == "chart":
        # 图表模式：保留图表切换。Inline 图表消息无法可靠恢复成纯文本。
        keyboard = [[
            InlineKeyboardButton("🌡️ 温度趋势", callback_data=f"chart|{location_query}|temp"),
            InlineKeyboardButton("🌧️ 降水趋势", callback_data=f"chart|{location_query}|rain")
        ]]
    else:
        # 默认文本模式：功能按钮
        # 第一排：基础功能
        row1 = [
            InlineKeyboardButton("🔄 刷新", callback_data=f"refresh|{location_query}"),
            InlineKeyboardButton("🔔 降雨提醒", callback_data=f"sub|{location_query}")
        ]
        keyboard = [row1]
        
        # 第二排：图表按钮 (可选)
        if show_charts:
            row2 = [
                InlineKeyboardButton("🌡️ 温度趋势", callback_data=f"chart|{location_query}|temp"),
                InlineKeyboardButton("🌧️ 降水趋势", callback_data=f"chart|{location_query}|rain")
            ]
            keyboard.append(row2)

    return InlineKeyboardMarkup(keyboard)
