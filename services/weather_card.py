"""Raster weather card for callers that cannot render Telegram Rich blocks.

The Telegram bot normally renders the same data as Bot API rich blocks.  An
external chat bot may not have that capability, so this small Pillow renderer
provides a portable PNG fallback with the same hierarchy: location headline,
current condition, core daily rows, and attribution.
"""

from __future__ import annotations

import io
import os
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

from domain.models import WeatherData
from utils.formatter import format_precip_value
from utils.weather_icons import icon_label


_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/bundled/ResourceHanRoundedCN-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = list(_FONT_CANDIDATES)
    if bold:
        candidates.insert(0, "/usr/share/fonts/opentype/bundled/ResourceHanRoundedCN-Bold.ttf")
        candidates.insert(1, "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fmt(value, suffix: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value}{suffix}"


def _rows(data: WeatherData) -> list[tuple[str, str]]:
    day = data.get_current_daily_forecast()
    rows: list[tuple[str, str]] = []
    if day is not None:
        rows.append(("气温", f"{_fmt(day.temp_min)}~{_fmt(day.temp_max)}°C"))
        if day.text_day == day.text_night:
            rows.append(("全天", day.text_day))
        else:
            rows.append(
                (
                    "日间/夜间",
                    f"{day.text_day} → {day.text_night}",
                )
            )
    if data.now_humidity is not None:
        rows.append(("湿度", _fmt(data.now_humidity, "%")))
    wind = " ".join(
        str(part)
        for part in (
            data.now_wind_dir,
            f"{data.now_wind_scale}级" if data.now_wind_scale else None,
            _fmt(data.now_wind_speed, "km/h") if data.now_wind_speed is not None else None,
        )
        if part
    )
    if wind:
        rows.append(("风况", wind))
    if day is not None and day.precip is not None:
        rows.append(("降水", format_precip_value(day.precip, day.precip_kind)))
    if day is not None and day.uv_index:
        rows.append(("紫外线", str(day.uv_index)))
    if day is not None and (day.sunrise or day.sunset):
        rows.append(("日出/日落", f"{day.sunrise or '—'} / {day.sunset or '—'}"))
    if data.yesterday is not None and day is not None and data.yesterday.temp_max is not None:
        delta = day.temp_max - data.yesterday.temp_max
        rows.append(("比昨天", f"最高 {'+' if delta >= 0 else ''}{_fmt(round(delta, 1))}°"))
    if data.air_quality is not None:
        air = data.air_quality
        bits = [str(bit) for bit in (air.aqi, air.category) if bit not in (None, "")]
        if bits:
            rows.append(("空气质量", " · ".join(bits)))
    return rows


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> Iterable[str]:
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            yield current
            current = char
        else:
            current = candidate
    if current:
        yield current


def _source_label(source: str) -> str:
    return {
        "qweather": "和风天气",
        "caiyun": "彩云天气",
        "fusion": "和风天气 & 彩云天气",
    }.get(source, source)


def render_weather_card(data: WeatherData) -> bytes:
    """Return a PNG weather card suitable for ``send_photo``."""

    width = 970
    padding = 36
    title_font = _font(42, bold=True)
    body_font = _font(27)
    small_font = _font(20)
    row_height = 76
    rows = _rows(data)
    measure_draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    summary_lines: list[str] = []
    for paragraph in (data.summary or "").splitlines() or [""]:
        summary_lines.extend(_wrapped_lines(measure_draw, paragraph, body_font, width - 2 * padding))
    visible_summary_lines = summary_lines[:3] or ["暂无天气摘要"]
    source = _source_label(str(data.source))
    footer_lines: list[str] = [f"数据源：{source} · {data.update_time.strftime('%m-%d %H:%M')} 更新"]
    attributions = [entry.strip() for entry in (data.attributions or []) if entry.strip()]
    if attributions:
        footer_lines.extend(
            _wrapped_lines(measure_draw, f"来源声明：{' · '.join(attributions)}", small_font, width - 2 * padding)
        )
    height = 250 + len(visible_summary_lines) * 42 + len(rows) * row_height + max(0, len(footer_lines) - 1) * 30

    image = Image.new("RGB", (width, height), "#d9efcc")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((15, 15, width - 15, height - 15), radius=28, fill="#efffdf", outline="#b9dca8", width=3)

    weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    stamp = f"{data.update_time.strftime('%m-%d')} {weekdays[data.update_time.weekday()]}"
    title = f"{data.location_name} · {stamp}"
    draw.text((padding, 42), title, font=title_font, fill="#111111")

    current = f"{_fmt(data.now_temp, '°C')} {icon_label(data.now_icon)}"
    draw.text((padding, 102), current, font=body_font, fill="#111111")
    y = 145
    for line in visible_summary_lines:
        draw.text((padding, y), line, font=body_font, fill="#333333")
        y += 42

    table_top = y + 18
    label_width = 280
    for index, (label, value) in enumerate(rows):
        top = table_top + index * row_height
        fill = "#dff5d1" if index % 2 == 0 else "#efffdf"
        draw.rectangle((padding, top, width - padding, top + row_height), fill=fill, outline="#bdd9ad", width=2)
        draw.text((padding + 22, top + 20), label, font=body_font, fill="#111111")
        value_lines = list(_wrapped_lines(draw, value, body_font, width - padding - (padding + label_width) - 26))
        for offset, line in enumerate(value_lines[:2]):
            draw.text((padding + label_width, top + 20 + offset * 32), line, font=body_font, fill="#111111")

    footer_y = table_top + len(rows) * row_height + 26
    for line in footer_lines:
        draw.text((padding, footer_y), line, font=small_font, fill="#188d18")
        footer_y += 30

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
