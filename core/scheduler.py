import asyncio
from datetime import datetime, timedelta, time
from functools import partial
from html import escape
from zoneinfo import ZoneInfo

from loguru import logger
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import Application, ContextTypes

from core.config import settings
from services.fusion import WeatherFusionService
from services.llm import LLMService
from utils.formatter import format_weather_response


def _naive_dt(value: datetime) -> datetime:
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def _location_key(location: str) -> str:
    return " ".join(location.split()).casefold()


def will_rain_soon(weather, minutes: int = 30) -> bool:
    """Check current/minutely/hourly rain signals with provider-neutral rules."""
    if weather.is_raining:
        return True

    now = datetime.now()
    deadline = now + timedelta(minutes=minutes)
    stale_before = now - timedelta(minutes=2)

    has_usable_minutely = False
    for item in weather.minutely:
        item_time = _naive_dt(item.time)
        if item_time < stale_before or item_time > deadline:
            continue
        has_usable_minutely = True
        if item.precip > 0:
            return True
        if item.probability is not None and item.probability > 0.5:
            return True

    if not has_usable_minutely:
        for hour in weather.hourly[:6]:
            if (hour.precip or 0) > 0:
                return True
            if hour.pop is not None and hour.pop >= 50:
                return True

    return False


# Per-location ceilings so one stuck upstream call cannot stall a whole job
# round (max_instances=1 would then skip the following runs entirely).
LOCATION_WEATHER_TIMEOUT = 30.0
LOCATION_REPORT_TIMEOUT = 90.0


def _remove_subscription(chat_data: dict, list_key: str, location: str) -> None:
    subs = chat_data.get(list_key)
    if isinstance(subs, list) and location in subs:
        subs.remove(location)


async def _persist_chat_data(app: Application, chat_ids: set) -> None:
    """JobQueue callbacks bypass update processing, so flush chat_data manually."""
    if not chat_ids:
        return
    try:
        for chat_id in chat_ids:
            app.mark_data_for_update_persistence(chat_ids=chat_id)
        await app.update_persistence()
    except Exception as error:
        logger.error(f"Failed to persist chat_data from job: {error}")


async def send_daily_brief(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    weather_service: WeatherFusionService,
    llm_service: LLMService,
):
    """Generate one weather report per unique subscription location."""
    app = context.application
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    grouped: dict[str, dict] = {}
    for chat_id, data in app.chat_data.items():
        for location in data.get("daily_subs", []):
            key = _location_key(location)
            entry = grouped.setdefault(key, {"location": location, "subscribers": []})
            entry["subscribers"].append((chat_id, data, location))

    dirty_chats: set = set()
    logger.debug(f"Running Daily Brief for {len(grouped)} unique locations")
    for entry in grouped.values():
        location = entry["location"]
        try:
            weather = await asyncio.wait_for(
                weather_service.get_fused_weather(location, profile="full"),
                timeout=LOCATION_WEATHER_TIMEOUT,
            )
            if not weather:
                continue
            report_text = await asyncio.wait_for(
                llm_service.generate_weather_report(weather),
                timeout=LOCATION_REPORT_TIMEOUT,
            )
            header = f"☀️ <b>早安！{escape(location)}</b>\n------------------\n"
            for chat_id, chat_data, subscribed_location in entry["subscribers"]:
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=header + report_text,
                        parse_mode=ParseMode.HTML,
                    )
                    logger.info(f"Sent Daily Brief to {chat_id} for {location}")
                except Forbidden:
                    _remove_subscription(chat_data, "daily_subs", subscribed_location)
                    dirty_chats.add(chat_id)
                    logger.info(
                        f"Removed daily subscription for blocked chat {chat_id}/{subscribed_location}"
                    )
                except Exception as error:
                    logger.error(
                        f"Daily Brief delivery failed for {chat_id}/{location}: {error}"
                    )
        except asyncio.TimeoutError:
            logger.error(f"Daily Brief timed out for {location}")
        except Exception as error:
            logger.error(f"Daily Brief generation failed for {location}: {error}")

    await _persist_chat_data(app, dirty_chats)


async def check_rain_alerts(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    weather_service: WeatherFusionService,
):
    """Fetch each unique location once, then fan out rain notifications."""
    app = context.application
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    grouped: dict[str, dict] = {}
    for chat_id, data in app.chat_data.items():
        for location in data.get("subs", []):
            key = _location_key(location)
            entry = grouped.setdefault(key, {"location": location, "subscribers": []})
            entry["subscribers"].append((chat_id, data, location))

    dirty_chats: set = set()
    logger.debug(f"Running rain check for {len(grouped)} unique locations")
    for entry in grouped.values():
        location = entry["location"]
        try:
            weather = await asyncio.wait_for(
                weather_service.get_fused_weather(location, profile="rain"),
                timeout=LOCATION_WEATHER_TIMEOUT,
            )
            if not weather or not will_rain_soon(weather):
                continue

            alert_text = "🚨 *自动降雨提醒*\n\n" + format_weather_response(
                weather,
                view_type="rain",
            )
            for chat_id, chat_data, subscribed_location in entry["subscribers"]:
                last_alert = chat_data.get("last_rain_alert", {})
                last_time = last_alert.get(subscribed_location)
                if last_time and (datetime.now() - last_time) < timedelta(hours=4):
                    continue
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=alert_text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                    last_alert[subscribed_location] = datetime.now()
                    chat_data["last_rain_alert"] = last_alert
                    dirty_chats.add(chat_id)
                    logger.info(f"Sent rain alert to {chat_id} for {subscribed_location}")
                except Forbidden:
                    _remove_subscription(chat_data, "subs", subscribed_location)
                    dirty_chats.add(chat_id)
                    logger.info(
                        f"Removed rain subscription for blocked chat {chat_id}/{subscribed_location}"
                    )
                except Exception as error:
                    logger.error(
                        f"Rain alert delivery failed for {chat_id}/{subscribed_location}: {error}"
                    )
        except asyncio.TimeoutError:
            logger.error(f"Rain check timed out for {location}")
        except Exception as error:
            logger.error(f"Rain check failed for {location}: {error}")

    await _persist_chat_data(app, dirty_chats)


def setup_scheduler(
    app: Application,
    weather_service: WeatherFusionService,
    llm_service: LLMService,
):
    """Initialize scheduled jobs with the app's shared service instances."""
    job_queue = app.job_queue
    if not job_queue:
        logger.warning("JobQueue is not available!")
        return

    rain_status = "OFF"
    daily_status = "OFF"

    if settings.enable_rain_alerts:
        job_queue.run_repeating(
            partial(check_rain_alerts, weather_service=weather_service),
            interval=300,
            first=10,
            name="rain-alerts",
        )
        rain_status = "ON"

    if settings.enable_daily_brief:
        job_queue.run_daily(
            partial(
                send_daily_brief,
                weather_service=weather_service,
                llm_service=llm_service,
            ),
            time=time(8, 0, tzinfo=ZoneInfo(settings.timezone)),
            name="daily-brief",
        )
        daily_status = "ON"

    logger.info(f"Scheduler initialized: Rain Alerts [{rain_status}], Daily Brief [{daily_status}]")
