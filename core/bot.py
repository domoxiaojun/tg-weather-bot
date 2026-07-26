import asyncio
import os

from loguru import logger
from telegram import __version__ as ptb_version
from telegram.constants import BOT_API_VERSION
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChosenInlineResultHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    PicklePersistence,
    filters,
)

from core.config import settings
from core.handlers.callbacks import CallbackHandlers
from core.handlers.common import BotDependencies
from core.handlers.inline import InlineHandlers
from core.handlers.report import ReportHandlers
from core.handlers.subscriptions import SubscriptionHandlers
from core.handlers.weather import WeatherHandlers
from services.fusion import WeatherFusionService
from services.llm import LLMService
from utils.cache import cache


def create_app() -> Application:
    """Factory to create the PTB Application."""
    logger.info(
        "Telegram client: python-telegram-bot {} (typed Bot API support {})",
        ptb_version,
        BOT_API_VERSION,
    )
    os.makedirs("data", exist_ok=True)
    persistence = PicklePersistence(filepath="data/bot_data.pickle")

    builder = Application.builder()
    builder.token(settings.bot_token)
    builder.persistence(persistence)

    app = builder.build()

    deps = BotDependencies(
        weather_service=WeatherFusionService(),
        llm_service=LLMService(),
    )
    weather = WeatherHandlers(deps)
    reports = ReportHandlers(deps)
    inline = InlineHandlers(deps)
    callbacks = CallbackHandlers(deps)
    subscriptions = SubscriptionHandlers(deps)

    app.add_handler(CommandHandler("start", weather.start))
    app.add_handler(CommandHandler("tq", weather.handle_weather_request))
    app.add_handler(CommandHandler("chart", weather.chart))
    app.add_handler(CommandHandler("report", reports.report))
    app.add_handler(CommandHandler("daily_sub", subscriptions.daily_sub))
    app.add_handler(CommandHandler("daily_unsub", subscriptions.daily_unsub))
    app.add_handler(CommandHandler("daily_my", subscriptions.daily_my))
    app.add_handler(CommandHandler("rain_sub", subscriptions.rain_sub))
    app.add_handler(CommandHandler("rain_unsub", subscriptions.rain_unsub))
    app.add_handler(CommandHandler("rain_my", subscriptions.rain_my))
    app.add_handler(MessageHandler(filters.LOCATION, weather.handle_weather_request))
    app.add_handler(CallbackQueryHandler(callbacks.handle_callback))
    app.add_handler(InlineQueryHandler(inline.handle_inline_query))
    app.add_handler(ChosenInlineResultHandler(reports.handle_chosen_inline_result))

    from core.scheduler import setup_scheduler

    setup_scheduler(app, deps.weather_service, deps.llm_service)

    async def log_error(update, context):
        logger.opt(exception=context.error).error("Unhandled Telegram update error")

    app.add_error_handler(log_error)

    async def close_resources(application: Application):
        await cache.cancel_inflight()
        await asyncio.gather(
            deps.weather_service.aclose(),
            deps.llm_service.aclose(),
            return_exceptions=True,
        )
        await cache.close()

    app.post_shutdown = close_resources

    return app
