import asyncio
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from loguru import logger
from telegram import InputFile

from core.config import settings
from domain.models import WeatherData
from services.visualizer import Visualizer
from utils.cache import cache

ChartType = Literal["temp", "rain", "daily", "minutely"]


@dataclass(frozen=True, slots=True)
class PreparedChart:
    """Chart media ready to embed in one rich weather message."""

    chart_type: ChartType
    caption: str
    file_id: Optional[str] = None
    png_bytes: Optional[bytes] = None

# Which QWeather request profile each chart needs.
CHART_PROFILES = {
    "daily": "daily",
    "rain": "rain",
    "temp": "hourly",
    "minutely": "rain",
}
CHART_CACHE_TTL = 1800
CHART_FAILURE_TTL = 120
_CHART_FAILURE_SENTINEL = "__chart_failed__"

# Matplotlib rendering is CPU-bound; charts are drawn with the thread-safe
# OO API (Figure + FigureCanvasAgg) on a small worker pool to keep the
# asyncio event loop free while allowing concurrent renders.
_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chart-render")


async def run_chart_render(func: Callable[..., Any], *args: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_RENDER_EXECUTOR, func, *args)


def normalize_chart_type(chart_type: str) -> ChartType:
    if chart_type == "rain":
        return "rain"
    if chart_type == "daily":
        return "daily"
    if chart_type in ("minutely", "minute", "分钟"):
        return "minutely"
    return "temp"


def get_chart_caption(weather_data: WeatherData, chart_type: str) -> str:
    """Photo caption under the embedded chart.

    Location already appears on the weather card heading *and* inside the PNG
    header — repeating it in the caption just looks like a duplicate city line.
    """
    del weather_data  # reserved for future per-chart context
    normalized = normalize_chart_type(chart_type)
    if normalized == "rain":
        return "🌧️ 逐小时降水"
    if normalized == "daily":
        return "📈 逐日温度"
    if normalized == "minutely":
        return "☔️ 分钟级降水"
    return "📈 逐小时温度"


def render_chart_bytes(weather_data: WeatherData, chart_type: str) -> Optional[bytes]:
    normalized = normalize_chart_type(chart_type)
    if normalized == "rain":
        return Visualizer.draw_hourly_rain_chart(weather_data)
    if normalized == "daily":
        return Visualizer.draw_daily_temp_chart(weather_data)
    if normalized == "minutely":
        return Visualizer.draw_minutely_rain_chart(weather_data)
    return Visualizer.draw_hourly_temp_chart(weather_data)


async def render_chart_bytes_async(weather_data: WeatherData, chart_type: str) -> Optional[bytes]:
    return await run_chart_render(render_chart_bytes, weather_data, chart_type)


async def prepare_chart(weather_data: WeatherData, chart_type: str) -> Optional[PreparedChart]:
    """Resolve cached Telegram media or render bytes for a rich-message upload."""
    normalized = normalize_chart_type(chart_type)
    file_id = await get_cached_chart_file_id(weather_data, normalized)
    if file_id:
        return PreparedChart(
            chart_type=normalized,
            caption=get_chart_caption(weather_data, normalized),
            file_id=file_id,
        )

    png_bytes = await render_chart_bytes_async(weather_data, normalized)
    if not png_bytes:
        return None
    return PreparedChart(
        chart_type=normalized,
        caption=get_chart_caption(weather_data, normalized),
        png_bytes=png_bytes,
    )


def chart_cache_key(weather_data: WeatherData, chart_type: str) -> str:
    normalized = normalize_chart_type(chart_type)
    if normalized == "rain":
        values = [
            (hour.time.isoformat(), hour.pop, hour.precip, hour.precip_kind)
            for hour in weather_data.hourly[:Visualizer.HOURLY_POINT_LIMIT]
        ]
    elif normalized == "daily":
        values = [
            (day.date.isoformat(), day.temp_min, day.temp_max)
            for day in weather_data.get_daily_forecasts()
        ]
    elif normalized == "minutely":
        values = [
            (item.time.isoformat(), item.precip, item.precip_kind)
            for item in weather_data.minutely
        ]
    else:
        values = [
            (hour.time.isoformat(), hour.temp, hour.feels_like, hour.feels_like_source)
            for hour in weather_data.hourly[:Visualizer.HOURLY_POINT_LIMIT]
        ]
    # update_time deliberately excluded: values already cover every rendered
    # datum, and qw:now refreshes update_time every 10 min — keeping it here
    # invalidated identical charts long before the data actually changed.
    fingerprint_payload = {
        "location": weather_data.location_name,
        "values": values,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"chart:v15:{weather_data.coords}:{normalized}:{fingerprint}"


async def remember_chart_file_id(weather_data: WeatherData, chart_type: str, message) -> None:
    """Store the file_id of an already-sent chart photo for instant reuse.

    Telegram returns the uploaded photo's file_id on every send; caching it
    means the next request for the same data skips both render and upload.
    """
    try:
        photos = getattr(message, "photo", None)
        file_id = photos[-1].file_id if photos else None
        if not file_id:
            # PTB 22.8 predates Message.rich_message. Unknown Bot API 10.2
            # fields are retained in api_kwargs, so recover the uploaded
            # photo from the raw RichBlockPhoto response.
            api_kwargs = getattr(message, "api_kwargs", None) or {}
            rich_message = api_kwargs.get("rich_message") or {}
            blocks = rich_message.get("blocks") or []
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != "photo":
                    continue
                rich_photos = block.get("photo") or []
                if rich_photos:
                    largest = rich_photos[-1]
                    file_id = (
                        largest.get("file_id")
                        if isinstance(largest, dict)
                        else getattr(largest, "file_id", None)
                    )
                if file_id:
                    break
        if file_id:
            await cache.set(chart_cache_key(weather_data, chart_type), file_id, ttl=CHART_CACHE_TTL)
    except Exception as e:
        logger.debug(f"Failed to remember chart file_id: {e}")


async def get_cached_chart_file_id(weather_data: WeatherData, chart_type: str) -> Optional[str]:
    cached = await cache.get(chart_cache_key(weather_data, chart_type))
    if cached == _CHART_FAILURE_SENTINEL:
        return None
    return cached if isinstance(cached, str) and cached else None


async def get_or_create_chart_file_id(bot, weather_data: WeatherData, chart_type: str) -> Optional[str]:
    cached = await get_cached_chart_file_id(weather_data, chart_type)
    if cached:
        return cached

    if not settings.super_admin_id:
        logger.warning("SUPER_ADMIN_ID is not configured; inline chart file_id cache cannot be created.")
        return None

    key = chart_cache_key(weather_data, chart_type)

    async def upload_chart() -> Optional[str]:
        # Failures are cached briefly so a persistently failing chart does not
        # re-render (CPU) and re-upload on every request.
        img_bytes = await render_chart_bytes_async(weather_data, chart_type)
        if not img_bytes:
            return _CHART_FAILURE_SENTINEL
        try:
            msg = await bot.send_photo(
                chat_id=settings.super_admin_id,
                photo=InputFile(io.BytesIO(img_bytes), filename=f"{normalize_chart_type(chart_type)}.png"),
                disable_notification=True,
            )
            file_id = msg.photo[-1].file_id
            try:
                await msg.delete()
            except Exception:
                pass
            return file_id
        except Exception as e:
            logger.error(f"Failed to upload chart for file_id cache: {e}")
            return _CHART_FAILURE_SENTINEL

    def resolve_ttl(value: str) -> int:
        return CHART_FAILURE_TTL if value == _CHART_FAILURE_SENTINEL else CHART_CACHE_TTL

    value = await cache.get_or_set(key, upload_chart, ttl=resolve_ttl)
    if value == _CHART_FAILURE_SENTINEL:
        return None
    return value if isinstance(value, str) and value else None
