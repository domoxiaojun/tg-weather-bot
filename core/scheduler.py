from telegram.ext import Application, ContextTypes
from telegram.constants import ParseMode
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_fixed
from datetime import datetime, timedelta, time
from html import escape

from services.fusion import WeatherFusionService
from services.llm import LLMService
from core.config import settings
from utils.formatter import format_weather_response

# Global Service Instance for Jobs
weather_service = WeatherFusionService()
llm_service = LLMService()

def _naive_dt(value: datetime) -> datetime:
    return value.astimezone().replace(tzinfo=None) if value.tzinfo else value

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
        if item_time < stale_before:
            continue
        if item_time > deadline:
            continue
        has_usable_minutely = True
        if item.precip > 0:
            return True
        if item.probability is not None and item.probability > 0.5:
            return True

    if not has_usable_minutely:
        for hour in weather.hourly[:6]:
            if hour.precip > 0:
                return True
            if hour.pop is not None and hour.pop >= 50:
                return True

    return False

async def job_error_handler(context: ContextTypes.DEFAULT_TYPE):
    """Specific error handler for Background Jobs"""
    logger.error(f"Job failed: {context.error}")

async def send_daily_brief(context: ContextTypes.DEFAULT_TYPE):
    """Morning Daily Brief Job (8:00 AM)"""
    app = context.application
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    logger.debug(f"Running Daily Brief check for {len(app.chat_data)} chats...")
    for chat_id, data in app.chat_data.items():
        daily_subs = data.get("daily_subs", [])
        if not daily_subs: continue
            
        for location in daily_subs:
            try:
                weather = await weather_service.get_fused_weather(location)
                if not weather: continue

                report_text = await llm_service.generate_weather_report(weather)
                header = f"☀️ <b>早安！{escape(location)}</b>\n------------------\n"
                await context.bot.send_message(chat_id=chat_id, text=header + report_text, parse_mode=ParseMode.HTML)
                logger.info(f"Sent Daily Brief to {chat_id} for {location}")
            except Exception as e:
                logger.error(f"Daily Brief failed for {chat_id}/{location}: {e}")

@retry(stop=stop_after_attempt(2), wait=wait_fixed(5))
async def check_rain_alerts(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodic job to check rain for all subscribed users.
    """
    app = context.application
    # Iterate over all chats in persistence
    if not hasattr(app, "chat_data") or not app.chat_data:
        return

    logger.debug(f"Running rain check for {len(app.chat_data)} chats...")

    for chat_id, data in app.chat_data.items():
        subs = data.get("subs", [])
        if not subs:
            continue
            
        last_alert = data.get("last_rain_alert", {}) # {location: timestamp}
        
        for location in subs:
            try:
                # 1. Fetch Data (Fusion)
                weather = await weather_service.get_fused_weather(location)
                if not weather:
                    continue
                    
                if will_rain_soon(weather):
                    # 3. Check Cooldown (don't spam, alert once per 4 hours)
                    last_time = last_alert.get(location)
                    if last_time and (datetime.now() - last_time) < timedelta(hours=4):
                        continue
                        
                    # 4. Send Alert
                    logger.info(f"Sending Rain Alert to {chat_id} for {location}")
                    
                    # Use formatted response for safety and consistency
                    alert_text = format_weather_response(weather, view_type="rain")
                    alert_text = "🚨 *自动降雨提醒*\n\n" + alert_text
                    
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=alert_text,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    
                    # Update state
                    last_alert[location] = datetime.now()
                    data["last_rain_alert"] = last_alert
                    
            except Exception as e:
                logger.error(f"Error checking rain for {chat_id}/{location}: {e}")

def setup_scheduler(app: Application):
    """Initialize the JobQueue with robust settings"""
    jq = app.job_queue
    if not jq:
        logger.warning("JobQueue is not available!")
        return

    rain_status = "OFF"
    daily_status = "OFF"

    # 1. Rain Alerts (Every 5 mins)
    if settings.enable_rain_alerts:
        jq.run_repeating(check_rain_alerts, interval=300, first=10)
        rain_status = "ON"
    
    # 2. Daily Brief (8:00 AM)
    # Assumes server timezone (or UTC if Docker).
    if settings.enable_daily_brief:
        jq.run_daily(send_daily_brief, time=time(8, 0))
        daily_status = "ON"
    
    logger.info(f"Scheduler initialized: Rain Alerts [{rain_status}], Daily Brief [{daily_status}]")
