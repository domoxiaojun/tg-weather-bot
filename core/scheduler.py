import asyncio
import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from html import escape
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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
from domain.models import normalize_warning_level
from services.telegram_rich import FEATURE_SEND, photo_block, rich
from services.typhoon import assess_storms, format_threat_summary
from utils.formatter import callback_location_token, format_weather_response
from utils.rich_formatter import (
    build_report_blocks,
    build_alert_push_blocks,
    build_event_push_blocks,
    build_rain_alert_blocks,
    build_typhoon_push_blocks,
)
from utils.schedule_times import is_within_quiet_hours, parse_brief_time, parse_quiet_hours

__all__ = [
    "DEFAULT_DAILY_BRIEF_TIME",
    "RainSignal",
    "evaluate_rain",
    "RAIN_LEVELS",
    "DEFAULT_RAIN_LEVEL",
    "parse_rain_level",
    "rain_level_label",
    "rain_level_hint",
    "rain_level_thresholds",
    "rate_mm_per_hour",
    "check_rain_alerts",
    "check_weather_alerts",
    "dispatch_daily_briefs",
    "parse_brief_time",
    "setup_scheduler",
    "will_rain_soon",
]


def _naive_dt(value: datetime) -> datetime:
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value


def _location_key(location: str) -> str:
    return " ".join(location.split()).casefold()


# Chinese short-duration rain classes, in mm/h. Used only for labelling.
RAIN_RATE_LABELS = ((16.0, "暴雨"), (8.0, "大雨"), (2.5, "中雨"), (0.0, "小雨"))

# Per-subscription sensitivity. Named by intent rather than by mm/h: the owner
# of this bot could not read the raw rates either, so the numbers stay an
# implementation detail and only surface in help text.
#   key -> (label, min rate mm/h or None to use the global setting,
#           whether a high probability alone may trigger, one-line explanation)
RAIN_LEVELS = {
    "all": ("全部降雨", 0.0, True, "任何降水都提醒，适合晒衣服/骑车"),
    "normal": ("一般降雨", None, True, "忽略毛毛雨（默认）"),
    "heavy": ("仅大雨", 8.0, False, "只在雨大到影响出行时提醒"),
}
DEFAULT_RAIN_LEVEL = "normal"

# What users may type. Kept generous because nobody remembers exact wording.
RAIN_LEVEL_ALIASES = {
    "all": "all", "全部": "all", "所有": "all", "全部降雨": "all", "任何": "all",
    "小雨": "all", "毛毛雨": "all", "灵敏": "all", "1": "all",
    "normal": "normal", "一般": "normal", "标准": "normal", "默认": "normal",
    "一般降雨": "normal", "中雨": "normal", "2": "normal",
    "heavy": "heavy", "大雨": "heavy", "仅大雨": "heavy", "只看大雨": "heavy",
    "暴雨": "heavy", "3": "heavy",
}


def parse_rain_level(raw):
    """Map user input to a level key, or None when it is not a level word."""
    if not raw:
        return None
    return RAIN_LEVEL_ALIASES.get(str(raw).strip().lower())


def rain_level_label(level) -> str:
    return RAIN_LEVELS.get(level or DEFAULT_RAIN_LEVEL, RAIN_LEVELS[DEFAULT_RAIN_LEVEL])[0]


def rain_level_hint(level) -> str:
    return RAIN_LEVELS.get(level or DEFAULT_RAIN_LEVEL, RAIN_LEVELS[DEFAULT_RAIN_LEVEL])[3]


def rain_level_thresholds(level):
    """(min rate mm/h, allow probability-only trigger) for a level."""
    label, rate, allow_pop, _hint = RAIN_LEVELS.get(
        level or DEFAULT_RAIN_LEVEL, RAIN_LEVELS[DEFAULT_RAIN_LEVEL]
    )
    return (settings.rain_alert_min_rate_mm_h if rate is None else rate), allow_pop


def rate_mm_per_hour(precip, kind=None, interval_minutes=None):
    """Normalise a precipitation value to mm/h.

    The three sources use three different units and mixing them up is a silent
    12x error: minutely values are millimetres accumulated over
    ``interval_minutes`` (5 by default), hourly ``amount`` is millimetres over
    that hour, and ``intensity`` (Caiyun) is already mm/h.
    """
    if precip is None:
        return None
    if kind == "intensity":
        return float(precip)
    if interval_minutes:
        return float(precip) * 60.0 / float(interval_minutes)
    return float(precip)


def rain_rate_label(rate) -> str:
    if rate is None:
        return ""
    for threshold, label in RAIN_RATE_LABELS:
        if rate >= threshold:
            return label
    return ""


@dataclass
class RainSignal:
    """Whether rain is imminent, and how hard, for one location."""
    will_rain: bool
    rate_mm_h: Optional[float] = None
    pop: Optional[float] = None
    at: Optional[datetime] = None
    reason: str = ""

    @property
    def label(self) -> str:
        return rain_rate_label(self.rate_mm_h)


def evaluate_rain(weather, minutes: int = 30, min_rate=None, min_pop=None, allow_pop=True) -> RainSignal:
    """Rain signal for the next ``minutes``, honouring the alert thresholds.

    Trace precipitation used to fire an alert exactly like a downpour; the rate
    floor filters that out. ``is_raining`` no longer short-circuits, otherwise
    the floor could never apply.
    """
    min_rate = settings.rain_alert_min_rate_mm_h if min_rate is None else min_rate
    min_pop = settings.rain_alert_min_pop_pct if min_pop is None else min_pop

    now = datetime.now()
    deadline = now + timedelta(minutes=minutes)
    stale_before = now - timedelta(minutes=2)

    peak_rate = None
    peak_at = None
    peak_pop = None
    reason = ""

    def consider_rate(value, moment, source):
        nonlocal peak_rate, peak_at, reason
        if value is None:
            return
        if peak_rate is None or value > peak_rate:
            peak_rate, peak_at, reason = value, moment, source

    # Observed rain right now counts as being in the window.
    consider_rate(
        rate_mm_per_hour(weather.now_precip, weather.now_precip_kind), now, "实况"
    )

    has_usable_minutely = False
    for item in weather.minutely:
        item_time = _naive_dt(item.time)
        if item_time < stale_before or item_time > deadline:
            continue
        has_usable_minutely = True
        consider_rate(
            rate_mm_per_hour(item.precip, item.precip_kind, item.interval_minutes),
            item_time,
            "分钟级降水",
        )
        if item.probability is not None:
            probability = item.probability * 100 if item.probability <= 1 else item.probability
            peak_pop = probability if peak_pop is None else max(peak_pop, probability)

    if not has_usable_minutely:
        for hour in weather.hourly[:6]:
            consider_rate(
                rate_mm_per_hour(hour.precip, hour.precip_kind), _naive_dt(hour.time), "逐小时预报"
            )
            if hour.pop is not None:
                peak_pop = hour.pop if peak_pop is None else max(peak_pop, hour.pop)

    if peak_rate is not None and peak_rate >= min_rate:
        return RainSignal(True, peak_rate, peak_pop, peak_at, reason)
    # "仅大雨" must not fire on a high chance of light rain, so that level
    # switches the probability-only channel off entirely.
    if allow_pop and peak_pop is not None and peak_pop >= min_pop:
        return RainSignal(True, peak_rate, peak_pop, peak_at, "降水概率")
    # No measurable rate anywhere but the provider still reports rain: trust it
    # rather than stay silent on unmeasurable data.
    if weather.is_raining and peak_rate is None:
        return RainSignal(True, None, peak_pop, now, "实况")
    return RainSignal(False, peak_rate, peak_pop, peak_at, "")


def will_rain_soon(weather, minutes: int = 30) -> bool:
    """Boolean form of :func:`evaluate_rain`."""
    return evaluate_rain(weather, minutes=minutes).will_rain


# Per-location ceilings so one stuck upstream call cannot stall a whole job
# round (max_instances=1 would then skip the following runs entirely).
LOCATION_WEATHER_TIMEOUT = 30.0
LOCATION_REPORT_TIMEOUT = 90.0
# Locations are processed concurrently but bounded, so one job round is
# paced by the slowest location instead of the sum of all of them.
LOCATION_CONCURRENCY = 5

DEFAULT_DAILY_BRIEF_TIME = "08:00"
WEEKDAYS_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# In-memory watermark per application for the minute-level brief dispatcher.
# A restart resets it (see the catch-up window); the per-chat "last sent date"
# guard prevents duplicates.
_daily_brief_last_check: dict[int, datetime] = {}


def push_keyboard(location: str, coords, kind: str) -> InlineKeyboardMarkup:
    """Buttons on push messages — a push must be actionable, not a dead end.

    kind: 'rain' (rain alerts, warnings, typhoon — they share the rain
    subscription list) or 'daily'. ``submy|kind`` opens the subscription card.
    """
    token = callback_location_token(location, coords)
    manage_label = "⚙️ 管理提醒" if kind == "rain" else "⚙️ 管理简报"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📊 查看完整天气", callback_data=f"tq|{token}|default|0|0"),
        InlineKeyboardButton(manage_label, callback_data=f"submy|{kind}"),
    ]])


def zone_for(tz_name):
    """Subscription timezone with a graceful fall back to the global default."""
    for candidate in (tz_name, settings.timezone):
        if not candidate:
            continue
        try:
            return ZoneInfo(str(candidate))
        except Exception:
            logger.debug(f"Unknown subscription timezone: {candidate}")
    return timezone.utc


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

    # Watermark is kept in UTC: each subscription's target time is evaluated in
    # its own location timezone and converted back, so one global clock cannot
    # skew subscriptions for other regions.
    now_utc = datetime.now(timezone.utc)
    last_check_utc = _daily_brief_last_check.get(id(app))
    fresh_start = last_check_utc is None
    if fresh_start:
        last_check_utc = now_utc - timedelta(seconds=90)
    _daily_brief_last_check[id(app)] = now_utc

    # After a restart, look back far enough to deliver a brief the downtime ate.
    catchup = timedelta(hours=settings.daily_brief_catchup_hours) if fresh_start else timedelta()
    window_start = min(last_check_utc, now_utc - catchup) if catchup else last_check_utc

    grouped: dict[str, dict] = {}
    for chat_id, data in app.chat_data.items():
        sub_times = data.get("daily_sub_times", {})
        sub_zones = data.get("daily_sub_tz", {})
        last_sent = data.get("daily_brief_last_sent", {})
        for location in data.get("daily_subs", []):
            parsed = parse_brief_time(sub_times.get(location, DEFAULT_DAILY_BRIEF_TIME))
            hour, minute = parsed if parsed else parse_brief_time(DEFAULT_DAILY_BRIEF_TIME)
            zone = zone_for(sub_zones.get(location))
            local_now = now_utc.astimezone(zone)
            base = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Check today's occurrence AND yesterday's: a 23:50 brief missed
            # just before midnight must still be caught up at 00:30, when
            # "today's" occurrence is in the future.
            target_local = next(
                (
                    candidate
                    for candidate in (base, base - timedelta(days=1))
                    if window_start < candidate.astimezone(timezone.utc) <= now_utc
                ),
                None,
            )
            if target_local is None:
                continue
            # "Already sent" is judged by the calendar day the brief belongs
            # to (the target's day, not today's) so a cross-midnight catch-up
            # does not swallow the next evening's brief.
            target_day = target_local.strftime("%Y-%m-%d")
            if last_sent.get(location) == target_day:
                continue
            key = _location_key(location)
            entry = grouped.setdefault(key, {"location": location, "subscribers": []})
            entry["subscribers"].append((chat_id, data, location, zone, target_day))

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
            for chat_id, chat_data, subscribed_location, zone, target_day in entry["subscribers"]:
                brief_date = datetime.now(zone)
                weekday = WEEKDAYS_CN[brief_date.weekday()]
                header = (
                    f"☀️ <b>早安！{escape(location)}</b> · "
                    f"{brief_date.strftime('%m月%d日')} {weekday}\n\n"
                )
                plain_header = (
                    f"☀️ 早安！{location} · {brief_date.strftime('%m月%d日')} {weekday}"
                )
                thread_id = chat_data.get("push_thread_id")
                keyboard = push_keyboard(location, weather.coords, "daily")
                try:
                    # Rich BLOCKS, never rich html= (HTML semantics collapse
                    # newlines and squash the brief into one blob).
                    brief_blocks = build_report_blocks(
                        report_text,
                        title=plain_header,
                        weather=weather,
                        collapse_tail=True,
                    )
                    if await rich.send_rich(
                        context.bot,
                        chat_id,
                        blocks=brief_blocks,
                        message_thread_id=thread_id,
                        reply_markup=keyboard,
                    ) is None:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=header + report_text,
                            parse_mode=ParseMode.HTML,
                            message_thread_id=thread_id,
                            reply_markup=keyboard,
                        )
                    chat_data.setdefault("daily_brief_last_sent", {})[subscribed_location] = target_day
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

    Alerts are episode-based per (chat, location): a subscriber is notified once
    when rain becomes imminent and stays quiet until that episode ends. Quiet
    hours are evaluated in each subscription's own timezone and suppress the
    send *without* recording the episode, so the alert still lands once the
    window closes.
    """
    app = context.application
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    quiet_window = parse_quiet_hours(settings.rain_alert_quiet_hours)

    grouped: dict[str, dict] = {}
    for chat_id, data in app.chat_data.items():
        zones = data.get("sub_tz", {})
        levels = data.get("rain_level", {})
        for location in data.get("subs", []):
            key = _location_key(location)
            entry = grouped.setdefault(key, {"location": location, "key": key, "subscribers": []})
            entry["subscribers"].append((
                chat_id, data, location, zone_for(zones.get(location)),
                levels.get(location, DEFAULT_RAIN_LEVEL),
            ))

    dirty_chats: set = set()
    cooldown = timedelta(hours=settings.rain_alert_cooldown_hours)
    logger.debug(f"Running rain check for {len(grouped)} unique locations")

    async def process(entry):
        location = entry["location"]
        try:
            weather = await asyncio.wait_for(
                weather_service.get_fused_weather(location, profile="rain"),
                timeout=LOCATION_WEATHER_TIMEOUT,
            )
            if not weather:
                # Fetch failure: keep the previous state rather than guessing.
                return

            # Sensitivity is per subscription, so evaluate once per distinct
            # level instead of once per location.
            lookahead = _rain_lookahead_minutes()
            signals = {}
            for level in {sub[4] for sub in entry["subscribers"]}:
                min_rate, allow_pop = rain_level_thresholds(level)
                signals[level] = evaluate_rain(
                    weather, minutes=lookahead, min_rate=min_rate, allow_pop=allow_pop
                )
            if not any(sig.will_rain for sig in signals.values()):
                # Episode over for everyone: reset so the next rain alerts.
                for chat_id, chat_data, subscribed_location, _zone, _level in entry["subscribers"]:
                    episodes = chat_data.get("rain_episode", {})
                    if episodes.get(subscribed_location):
                        episodes[subscribed_location] = False
                        chat_data["rain_episode"] = episodes
                        dirty_chats.add(chat_id)
                return

            signal = max(signals.values(), key=lambda sig: sig.rate_mm_h or 0.0)
            headline = "🚨 *自动降雨提醒*"
            if signal.rate_mm_h is not None:
                headline += f" · {signal.label} 约 {signal.rate_mm_h:.1f}mm/h"
            elif signal.pop is not None:
                headline += f" · 降水概率 {signal.pop:.0f}%"
            alert_text = headline + "\n\n" + format_weather_response(
                weather,
                view_type="rain",
            )
            chart_type = "minutely" if weather.minutely else "rain"
            chart_file_id = None
            chart_bytes = None
            if settings.enable_weather_plots:
                chart_file_id = await get_cached_chart_file_id(weather, chart_type)
                if not chart_file_id:
                    chart_bytes = await render_chart_bytes_async(weather, chart_type)

            keyboard = push_keyboard(location, weather.coords, "rain")

            async def deliver(chat_id, thread_id=None):
                nonlocal chart_file_id, chart_bytes
                chart_sent = False
                # Prefer one rich alert. Cached Telegram media can be embedded
                # directly; a first-time byte upload is sent as a visual lead,
                # followed by the same rich alert card instead of degrading the
                # whole notification to a photo caption.
                if rich.supports(FEATURE_SEND):
                    embed_file_id = chart_file_id
                    if embed_file_id is None and chart_bytes:
                        try:
                            chart_message = await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=InputFile(io.BytesIO(chart_bytes), filename="rain.png"),
                                message_thread_id=thread_id,
                            )
                            await remember_chart_file_id(weather, chart_type, chart_message)
                            photos = getattr(chart_message, "photo", None)
                            if photos:
                                chart_file_id = photos[-1].file_id
                                chart_bytes = None
                            chart_sent = True
                        except Exception as error:
                            logger.warning(f"Rich rain chart lead failed; sending alert card only: {error}")

                    blocks = build_rain_alert_blocks(weather)
                    if embed_file_id:
                        caption = "未来 2 小时分钟级降水" if chart_type == "minutely" else "逐小时降水"
                        blocks.insert(2, photo_block(embed_file_id, caption))
                    if await rich.send_rich(
                        context.bot,
                        chat_id,
                        blocks=blocks,
                        message_thread_id=thread_id,
                        reply_markup=keyboard,
                    ) is not None:
                        return
                    if chart_sent:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=alert_text,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            message_thread_id=thread_id,
                            reply_markup=keyboard,
                        )
                        return

                caption_fits = len(alert_text) <= 1000
                if chart_file_id or chart_bytes:
                    photo = chart_file_id or InputFile(io.BytesIO(chart_bytes), filename="rain.png")
                    message = await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=alert_text if caption_fits else None,
                        parse_mode=ParseMode.MARKDOWN_V2 if caption_fits else None,
                        message_thread_id=thread_id,
                        # Overflow text follows below; buttons ride on the last message.
                        reply_markup=keyboard if caption_fits else None,
                    )
                    if chart_file_id is None:
                        # Reuse Telegram's upload for the remaining subscribers.
                        await remember_chart_file_id(weather, chart_type, message)
                        photos = getattr(message, "photo", None)
                        if photos:
                            chart_file_id = photos[-1].file_id
                            chart_bytes = None
                    if not caption_fits:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=alert_text,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            message_thread_id=thread_id,
                            reply_markup=keyboard,
                        )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=alert_text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        message_thread_id=thread_id,
                        reply_markup=keyboard,
                    )

            for chat_id, chat_data, subscribed_location, zone, level in entry["subscribers"]:
                episodes = chat_data.get("rain_episode", {})
                if not signals[level].will_rain:
                    # Below this subscriber's threshold: clear their episode so a
                    # heavier burst later still reaches them.
                    if episodes.get(subscribed_location):
                        episodes[subscribed_location] = False
                        chat_data["rain_episode"] = episodes
                        dirty_chats.add(chat_id)
                    continue
                if episodes.get(subscribed_location):
                    # Ongoing episode — this subscriber was already notified.
                    continue
                if quiet_window is not None and is_within_quiet_hours(
                    datetime.now(zone).time(), quiet_window
                ):
                    # Suppress without recording, so it fires after the window.
                    logger.debug(f"Rain alert held for quiet hours: {chat_id}/{subscribed_location}")
                    continue

                last_alert = chat_data.get("last_rain_alert", {})
                last_time = last_alert.get(subscribed_location)
                if last_time and (datetime.now() - last_time) < cooldown:
                    continue
                try:
                    await deliver(chat_id, chat_data.get("push_thread_id"))
                    last_alert[subscribed_location] = datetime.now()
                    chat_data["last_rain_alert"] = last_alert
                    episodes[subscribed_location] = True
                    chat_data["rain_episode"] = episodes
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
    await _persist_chat_data(app, dirty_chats)


def _alert_episode_key(alert) -> str:
    """Identify one warning revision: a re-issued warning must alert again."""
    identity = alert.alert_id or alert.title
    issued = alert.pub_time.isoformat() if alert.pub_time else ""
    return f"{identity}@{issued}"


def _derived_events(weather) -> list:
    """Threshold events the official feed does not cover.

    Returns (key, title, detail) tuples; the key must be stable for the day so
    an ongoing condition alerts once rather than every check.
    """
    if not settings.enable_derived_event_alerts:
        return []

    events = []
    day_stamp = weather.update_time.strftime("%Y-%m-%d")

    air = weather.air_quality
    if air is not None and air.aqi is not None and air.aqi >= settings.aqi_alert_threshold:
        events.append((
            f"aqi:{day_stamp}",
            f"🌫️ 空气质量转差（AQI {air.aqi}{'·' + air.category if air.category else ''}）",
            f"主要污染物 {air.primary}" if air.primary else "建议减少户外活动、关窗并佩戴口罩",
        ))

    today = weather.get_current_daily_forecast()
    if today is not None:
        if today.temp_max is not None and today.temp_max >= settings.high_temp_alert_threshold:
            events.append((
                f"heat:{day_stamp}",
                f"🥵 高温提示（最高 {today.temp_max:.0f}°C）",
                "注意防暑降温、及时补水，避免正午户外活动",
            ))
        if today.temp_min is not None and today.temp_min <= settings.low_temp_alert_threshold:
            events.append((
                f"cold:{day_stamp}",
                f"🥶 低温提示（最低 {today.temp_min:.0f}°C）",
                "注意保暖防寒，留意道路结冰",
            ))

    scales = []
    for hour in weather.hourly[:12]:
        try:
            scales.append((int(str(hour.wind_scale).split("-")[-1]), hour))
        except (TypeError, ValueError):
            continue
    if scales:
        peak_scale, peak_hour = max(scales, key=lambda item: item[0])
        if peak_scale >= settings.wind_alert_scale_threshold:
            events.append((
                f"wind:{day_stamp}",
                f"💨 大风提示（{peak_hour.time.strftime('%H:%M')} 起约 {peak_scale} 级）",
                "注意高空坠物与出行安全",
            ))
    return events


async def check_weather_alerts(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    weather_service: WeatherFusionService,
):
    """Push official warnings (and threshold events) to rain-alert subscribers.

    Official warnings are the only life-safety data this bot has, so they get
    their own faster cadence and configurable levels bypass quiet hours. Reuses
    the rain subscription list — "watching this city" is one intent, not two.
    """
    app = context.application
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    quiet_window = parse_quiet_hours(settings.rain_alert_quiet_hours)
    exempt_levels = settings.alert_exempt_levels

    grouped: dict[str, dict] = {}
    for chat_id, data in app.chat_data.items():
        zones = data.get("sub_tz", {})
        for location in data.get("subs", []):
            key = _location_key(location)
            entry = grouped.setdefault(key, {"location": location, "subscribers": []})
            entry["subscribers"].append((chat_id, data, location, zone_for(zones.get(location))))

    if not grouped:
        return

    # Active storms are basin-wide, so fetch once per round and reuse for every
    # location instead of per-subscriber.
    storms = []
    if settings.enable_typhoon_alerts:
        try:
            storms = await asyncio.wait_for(
                weather_service.qweather.get_active_storms(settings.typhoon_basin),
                timeout=LOCATION_WEATHER_TIMEOUT,
            )
            if storms:
                logger.info(f"Active tropical cyclones: {[s.display_name for s in storms]}")
        except asyncio.TimeoutError:
            logger.error("Tropical cyclone lookup timed out")
        except Exception as error:
            logger.error(f"Tropical cyclone lookup failed: {error}")

    dirty_chats: set = set()
    logger.debug(f"Running alert check for {len(grouped)} unique locations")

    async def process(entry):
        location = entry["location"]
        try:
            weather = await asyncio.wait_for(
                weather_service.get_fused_weather(location, profile="full"),
                timeout=LOCATION_WEATHER_TIMEOUT,
            )
            if not weather:
                return

            # (key, level, blocks-or-None, html) per pending notification.
            pending = []
            for alert in weather.alerts[:3]:
                level = normalize_warning_level(alert.level)
                pending.append((
                    f"warn:{_alert_episode_key(alert)}",
                    level,
                    build_alert_push_blocks(weather, alert),
                    None,
                ))
            for key, title, detail in _derived_events(weather):
                pending.append((
                    key,
                    "",
                    build_event_push_blocks(weather, title, detail),
                    f"<b>{escape(title)}</b>\n{escape(detail)}\n\n{escape(weather.location_name)}",
                ))

            # Tropical cyclones: wind-circle membership decides severity, so a
            # storm that actually reaches the user bypasses quiet hours.
            if storms:
                coords = weather_service.qweather._parse_coords(weather.coords)
                if coords is not None:
                    for threat in assess_storms(storms, coords[0], coords[1]):
                        pending.append((
                            threat.key,
                            threat.level,
                            build_typhoon_push_blocks(threat, weather.location_name),
                            f"🌀 <b>{escape(format_threat_summary(threat))}</b>\n"
                            f"{escape(weather.location_name)} · 距中心约 {threat.distance_km:.0f}km",
                        ))

            if not pending:
                # Nothing active: forget history so a re-issue alerts again.
                for chat_id, chat_data, subscribed_location, _zone in entry["subscribers"]:
                    seen = chat_data.get("alert_seen", {})
                    if seen.pop(subscribed_location, None) is not None:
                        chat_data["alert_seen"] = seen
                        dirty_chats.add(chat_id)
                return

            active_keys = {key for key, _level, _blocks, _html in pending}
            alert_keyboard = push_keyboard(location, weather.coords, "rain")
            for chat_id, chat_data, subscribed_location, zone in entry["subscribers"]:
                seen_map = chat_data.setdefault("alert_seen", {})
                seen = set(seen_map.get(subscribed_location, []))
                in_quiet = quiet_window is not None and is_within_quiet_hours(
                    datetime.now(zone).time(), quiet_window
                )
                thread_id = chat_data.get("push_thread_id")
                delivered = set()

                for key, level, blocks, html in pending:
                    if key in seen:
                        continue
                    if in_quiet and level not in exempt_levels:
                        # Held, not dropped: it fires when the window closes.
                        continue
                    try:
                        sent = await rich.send_rich(
                            context.bot,
                            chat_id,
                            blocks=blocks,
                            message_thread_id=thread_id,
                            reply_markup=alert_keyboard,
                        )
                        if sent is None:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=html or _alert_fallback_text(weather, blocks),
                                parse_mode=ParseMode.HTML,
                                message_thread_id=thread_id,
                                reply_markup=alert_keyboard,
                            )
                        delivered.add(key)
                        logger.info(f"Sent weather alert to {chat_id} for {subscribed_location}: {key}")
                    except Forbidden:
                        _remove_subscription(chat_data, "subs", subscribed_location)
                        dirty_chats.add(chat_id)
                        logger.info(f"Removed subscription for blocked chat {chat_id}")
                        break
                    except Exception as error:
                        logger.error(f"Alert delivery failed for {chat_id}/{subscribed_location}: {error}")

                # Keep only still-active keys so expired warnings do not pile up.
                updated = (seen | delivered) & active_keys
                if updated != seen:
                    seen_map[subscribed_location] = sorted(updated)
                    dirty_chats.add(chat_id)
        except asyncio.TimeoutError:
            logger.error(f"Alert check timed out for {location}")
        except Exception as error:
            logger.error(f"Alert check failed for {location}: {error}")

    await _bounded_gather(list(grouped.values()), process)
    await _persist_chat_data(app, dirty_chats)


def _alert_fallback_text(weather, blocks) -> str:
    """Plain-HTML rendering used when rich messages are unavailable."""
    lines = [f"⚠️ <b>{escape(weather.location_name)} 天气预警</b>"]
    for alert in weather.alerts[:3]:
        level = normalize_warning_level(alert.level)
        title = alert.title if not level or level in alert.title else f"{alert.title}（{level}）"
        lines.append(f"\n<b>{escape(title)}</b>")
        text = (alert.text or "").strip()
        if text:
            lines.append(escape(text[:400]))
    return "\n".join(lines)


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
    alert_status = "OFF"

    if settings.enable_alert_push:
        job_queue.run_repeating(
            partial(check_weather_alerts, weather_service=weather_service),
            interval=settings.alert_check_interval_minutes * 60,
            first=20,
            name="weather-alerts",
        )
        alert_status = f"ON ({settings.alert_check_interval_minutes}min)"

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

    logger.info(
        "Scheduler initialized: Rain Alerts [{}], Daily Brief [{}], Warning Push [{}]",
        rain_status,
        daily_status,
        alert_status,
    )
