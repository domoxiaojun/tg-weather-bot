import io
from typing import Literal, Optional

from loguru import logger
from telegram import InputFile

from core.config import settings
from domain.models import WeatherData
from services.visualizer import Visualizer
from utils.cache import cache

ChartType = Literal["temp", "rain", "daily"]
CHART_CACHE_TTL = 1800


def normalize_chart_type(chart_type: str) -> ChartType:
    if chart_type == "rain":
        return "rain"
    if chart_type == "daily":
        return "daily"
    return "temp"


def get_chart_caption(weather_data: WeatherData, chart_type: str) -> str:
    normalized = normalize_chart_type(chart_type)
    if normalized == "rain":
        return f"🌧️ {weather_data.location_name} 逐小时降水"
    if normalized == "daily":
        return f"📈 {weather_data.location_name} 逐日温度"
    return f"📈 {weather_data.location_name} 逐小时温度"


def render_chart_bytes(weather_data: WeatherData, chart_type: str) -> Optional[bytes]:
    normalized = normalize_chart_type(chart_type)
    if normalized == "rain":
        return Visualizer.draw_hourly_rain_chart(weather_data)
    if normalized == "daily":
        return Visualizer.draw_daily_temp_chart(weather_data)
    return Visualizer.draw_hourly_temp_chart(weather_data)


def chart_cache_key(weather_data: WeatherData, chart_type: str) -> str:
    return f"chart:v3:{weather_data.location_name}:{normalize_chart_type(chart_type)}"


async def get_cached_chart_file_id(weather_data: WeatherData, chart_type: str) -> Optional[str]:
    cached = await cache.get(chart_cache_key(weather_data, chart_type))
    return cached if isinstance(cached, str) and cached else None


async def get_or_create_chart_file_id(bot, weather_data: WeatherData, chart_type: str) -> Optional[str]:
    cached = await get_cached_chart_file_id(weather_data, chart_type)
    if cached:
        return cached

    if not settings.super_admin_id:
        logger.warning("SUPER_ADMIN_ID is not configured; inline chart file_id cache cannot be created.")
        return None

    img_bytes = render_chart_bytes(weather_data, chart_type)
    if not img_bytes:
        return None

    try:
        msg = await bot.send_photo(
            chat_id=settings.super_admin_id,
            photo=InputFile(io.BytesIO(img_bytes), filename=f"{normalize_chart_type(chart_type)}.png"),
            disable_notification=True,
        )
        file_id = msg.photo[-1].file_id
        await cache.set(chart_cache_key(weather_data, chart_type), file_id, ttl=CHART_CACHE_TTL)

        try:
            await msg.delete()
        except Exception:
            pass

        return file_id
    except Exception as e:
        logger.error(f"Failed to upload chart for file_id cache: {e}")
        return None
