import os

from loguru import logger
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
    app.add_handler(MessageHandler(filters.LOCATION, weather.handle_weather_request))
    app.add_handler(CallbackQueryHandler(callbacks.handle_callback))
    app.add_handler(InlineQueryHandler(inline.handle_inline_query))
    app.add_handler(ChosenInlineResultHandler(reports.handle_chosen_inline_result))

    async def log_error(update, context):
        logger.opt(exception=context.error).error("Unhandled Telegram update error")

    app.add_error_handler(log_error)

    async def close_cache(application: Application):
        await cache.close()

    app.post_shutdown = close_cache

    return app
