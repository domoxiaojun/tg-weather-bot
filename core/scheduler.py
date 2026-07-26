import asyncio
import io
from datetime import datetime, timedelta
from functools import partial
from html import escape
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import InputFile
from telegram.constants import ParseMode
from telegram.error import Forbidden
from telegram.ext import Application, ContextTypes

from core.config import settings
from services.chart_cache import (
    get_cached_chart_file_id,
    remember_chart_file_id,
    render_chart_bytes_async,
)
from services.fusion import WeatherFusionService
from services.llm import LLMService
from services.telegram_rich import FEATURE_SEND, photo_block, rich
from utils.formatter import format_weather_response
from utils.rich_formatter import build_rain_alert_blocks
from utils.schedule_times import is_within_quiet_hours, parse_brief_time, parse_quiet_hours

__all__ = [
    "DEFAULT_DAILY_BRIEF_TIME",
    "check_rain_alerts",
    "dispatch_daily_briefs",
    "parse_brief_time",
    "setup_scheduler",
    "will_rain_soon",
]


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
# Locations are processed concurrently but bounded, so one job round is
# paced by the slowest location instead of the sum of all of them.
LOCATION_CONCURRENCY = 5

DEFAULT_DAILY_BRIEF_TIME = "08:00"

# In-memory watermark per application for the minute-level brief dispatcher.
# A restart resets it; the per-chat "last sent date" guard prevents duplicates.
_daily_brief_last_check: dict[int, datetime] = {}


def _rain_lookahead_minutes() -> int:
    """Look slightly past the next check so rain cannot start inside the gap."""
    return max(30, settings.rain_check_interval_minutes + 10)


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


async def _bounded_gather(entries, worker):
    semaphore = asyncio.Semaphore(LOCATION_CONCURRENCY)

    async def run(entry):
        async with semaphore:
            await worker(entry)

    if entries:
        await asyncio.gather(*(run(entry) for entry in entries))


async def dispatch_daily_briefs(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    weather_service: WeatherFusionService,
    llm_service: LLMService,
):
    """Minute-level dispatcher supporting a custom HH:MM per subscription."""
    app = context.application
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    now = datetime.now(ZoneInfo(settings.timezone))
    last_check = _daily_brief_last_check.get(id(app), now - timedelta(seconds=90))
    _daily_brief_last_check[id(app)] = now
    today_str = now.strftime("%Y-%m-%d")

    grouped: dict[str, dict] = {}
    for chat_id, data in app.chat_data.items():
        sub_times = data.get("daily_sub_times", {})
        last_sent = data.get("daily_brief_last_sent", {})
        for location in data.get("daily_subs", []):
            parsed = parse_brief_time(sub_times.get(location, DEFAULT_DAILY_BRIEF_TIME))
            hour, minute = parsed if parsed else parse_brief_time(DEFAULT_DAILY_BRIEF_TIME)
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if not (last_check < target <= now):
                continue
            if last_sent.get(location) == today_str:
                continue
            key = _location_key(location)
            entry = grouped.setdefault(key, {"location": location, "subscribers": []})
            entry["subscribers"].append((chat_id, data, location))

    if not grouped:
        return

    dirty_chats: set = set()
    logger.info(f"Dispatching Daily Brief for {len(grouped)} unique locations")

    async def process(entry):
        location = entry["location"]
        try:
            weather = await asyncio.wait_for(
                weather_service.get_fused_weather(location, profile="full"),
                timeout=LOCATION_WEATHER_TIMEOUT,
            )
            if not weather:
                return
            report_text = await asyncio.wait_for(
                llm_service.generate_weather_report(weather),
                timeout=LOCATION_REPORT_TIMEOUT,
            )
            brief_date = datetime.now(ZoneInfo(settings.timezone))
            weekday = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[brief_date.weekday()]
            header = (
                f"☀️ <b>早安！{escape(location)}</b> · "
                f"{brief_date.strftime('%m月%d日')} {weekday}\n\n"
            )
            for chat_id, chat_data, subscribed_location in entry["subscribers"]:
                try:
                    if await rich.send_rich(context.bot, chat_id, html=header + report_text) is None:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=header + report_text,
                            parse_mode=ParseMode.HTML,
                        )
                    chat_data.setdefault("daily_brief_last_sent", {})[subscribed_location] = today_str
                    dirty_chats.add(chat_id)
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

    await _bounded_gather(list(grouped.values()), process)
    await _persist_chat_data(app, dirty_chats)


async def check_rain_alerts(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    weather_service: WeatherFusionService,
):
    """Fetch each unique location once, then fan out rain notifications.

    Alerts are episode-based: a location alerts once when it transitions to
    "rain expected" and stays silent until the episode ends. Quiet hours pause
    the whole check (state is untouched, so post-quiet checks catch up).
    """
    app = context.application
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    quiet_window = parse_quiet_hours(settings.rain_alert_quiet_hours)
    if quiet_window is not None:
        local_now = datetime.now(ZoneInfo(settings.timezone)).time()
        if is_within_quiet_hours(local_now, quiet_window):
            logger.debug("Rain check skipped during quiet hours")
            return

    grouped: dict[str, dict] = {}
    for chat_id, data in app.chat_data.items():
        for location in data.get("subs", []):
            key = _location_key(location)
            entry = grouped.setdefault(key, {"location": location, "key": key, "subscribers": []})
            entry["subscribers"].append((chat_id, data, location))

    bot_data = getattr(app, "bot_data", None)
    rain_state = bot_data.setdefault("rain_state", {}) if isinstance(bot_data, dict) else {}
    state_changed = False

    dirty_chats: set = set()
    cooldown = timedelta(hours=settings.rain_alert_cooldown_hours)
    logger.debug(f"Running rain check for {len(grouped)} unique locations")

    async def process(entry):
        nonlocal state_changed
        location = entry["location"]
        state_key = entry["key"]
        try:
            weather = await asyncio.wait_for(
                weather_service.get_fused_weather(location, profile="rain"),
                timeout=LOCATION_WEATHER_TIMEOUT,
            )
            if not weather:
                # Fetch failure: keep the previous state rather than guessing.
                return

            raining = will_rain_soon(weather, minutes=_rain_lookahead_minutes())
            previously_raining = bool(rain_state.get(state_key, False))
            if raining != previously_raining:
                rain_state[state_key] = raining
                state_changed = True
            if not raining:
                return
            if previously_raining:
                # Ongoing episode — subscribers were already notified.
                return

            alert_text = "🚨 *自动降雨提醒*\n\n" + format_weather_response(
                weather,
                view_type="rain",
            )
            chart_file_id = None
            chart_bytes = None
            if settings.enable_weather_plots:
                chart_file_id = await get_cached_chart_file_id(weather, "rain")
                if not chart_file_id:
                    chart_bytes = await render_chart_bytes_async(weather, "rain")

            async def deliver(chat_id):
                nonlocal chart_file_id, chart_bytes
                # A rich message carries the chart AND the full text together,
                # sidestepping the 1024-char photo caption limit.
                if chart_file_id and rich.supports(FEATURE_SEND):
                    blocks = build_rain_alert_blocks(weather)
                    blocks.insert(2, photo_block(chart_file_id, "逐小时降水"))
                    if await rich.send_rich(context.bot, chat_id, blocks=blocks) is not None:
                        return

                caption_fits = len(alert_text) <= 1000
                if chart_file_id or chart_bytes:
                    photo = chart_file_id or InputFile(io.BytesIO(chart_bytes), filename="rain.png")
                    message = await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=alert_text if caption_fits else None,
                        parse_mode=ParseMode.MARKDOWN_V2 if caption_fits else None,
                    )
                    if chart_file_id is None:
                        # Reuse Telegram's upload for the remaining subscribers.
                        await remember_chart_file_id(weather, "rain", message)
                        photos = getattr(message, "photo", None)
                        if photos:
                            chart_file_id = photos[-1].file_id
                            chart_bytes = None
                    if not caption_fits:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=alert_text,
                            parse_mode=ParseMode.MARKDOWN_V2,
                        )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=alert_text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )

            for chat_id, chat_data, subscribed_location in entry["subscribers"]:
                last_alert = chat_data.get("last_rain_alert", {})
                last_time = last_alert.get(subscribed_location)
                if last_time and (datetime.now() - last_time) < cooldown:
                    continue
                try:
                    await deliver(chat_id)
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

    await _bounded_gather(list(grouped.values()), process)

    # Drop state for locations nobody subscribes to anymore.
    stale_keys = set(rain_state) - set(grouped)
    if stale_keys:
        for stale in stale_keys:
            rain_state.pop(stale, None)
        state_changed = True

    await _persist_chat_data(app, dirty_chats)
    if state_changed and not dirty_chats and hasattr(app, "update_persistence"):
        try:
            await app.update_persistence()
        except Exception as error:
            logger.debug(f"Rain state persistence failed: {error}")


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
            interval=settings.rain_check_interval_minutes * 60,
            first=10,
            name="rain-alerts",
        )
        rain_status = f"ON ({settings.rain_check_interval_minutes}min)"

    if settings.enable_daily_brief:
        # Minute-level dispatcher so each subscription can pick its own HH:MM.
        job_queue.run_repeating(
            partial(
                dispatch_daily_briefs,
                weather_service=weather_service,
                llm_service=llm_service,
            ),
            interval=60,
            first=15,
            name="daily-brief",
        )
        daily_status = "ON"

    logger.info(f"Scheduler initialized: Rain Alerts [{rain_status}], Daily Brief [{daily_status}]")
