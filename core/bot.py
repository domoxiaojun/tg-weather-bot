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
from utils.persistence_backup import rotate_persistence_backups

PERSISTENCE_PATH = "data/bot_data.pickle"


# Commands whose replies are personal bookkeeping (ephemeral in groups).
_EPHEMERAL_COMMANDS = {"daily_sub", "daily_my", "daily_unsub", "rain_sub", "rain_my", "rain_unsub"}

# Shown on the bot's profile page before anyone talks to it.
BOT_SHORT_DESCRIPTION = "全球天气查询 · 降雨提醒 · 官方预警推送 · AI 天气日报"
BOT_DESCRIPTION = (
    "🌤 DomoWeather —— 和风天气 + 彩云天气双源融合的天气助手。\n\n"
    "• 发送城市名或位置即可查天气，支持逐小时 / 未来 15 天 / 生活指数 / 降水趋势图\n"
    "• 订阅降雨提醒：即将下雨时通知你，并推送官方灾害预警\n"
    "• 订阅早安简报：每天固定时间收到 AI 撰写的天气日报\n\n"
    "发送 /start 开始使用。"
)


def _weather_commands():
    from telegram import BotCommand

    return [
        BotCommand("tq", "天气查询 - /tq [城市] [参数]"),
        BotCommand("chart", "趋势图 - /chart [城市] [daily|rain|minutely]"),
        BotCommand("report", "AI天气日报 - /report [城市]"),
    ]


def _subscription_commands():
    from telegram import BotCommand

    return [
        BotCommand("rain_sub", "订阅降雨提醒 - /rain_sub [城市]"),
        BotCommand("rain_my", "我的降雨提醒 - 查看并取消"),
        BotCommand("rain_unsub", "取消降雨提醒 - /rain_unsub [城市]"),
        BotCommand("daily_sub", "订阅早安简报 - /daily_sub [城市] [HH:MM]"),
        BotCommand("daily_my", "我的早安订阅 - 查看并取消"),
        BotCommand("daily_unsub", "取消早安简报 - /daily_unsub [城市]"),
    ]


async def _set_commands(application: Application, commands, scope=None) -> None:
    """Register a command list, adding the 10.2 is_ephemeral flag when possible."""
    if settings.enable_ephemeral_messages:
        payload = [
            {
                "command": command.command,
                "description": command.description,
                **({"is_ephemeral": True} if command.command in _EPHEMERAL_COMMANDS else {}),
            }
            for command in commands
        ]
        api_kwargs = {"commands": payload}
        if scope is not None:
            api_kwargs["scope"] = scope.to_dict()
        try:
            await application.bot.do_api_request("setMyCommands", api_kwargs=api_kwargs)
            return
        except Exception as error:
            logger.warning(f"设置 ephemeral 命令标记失败，回退标准注册: {error}")

    await application.bot.set_my_commands(commands, scope=scope)


async def _register_bot_commands(application: Application):
    """Register scoped command lists and the profile descriptions on startup."""
    from telegram import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

    start_command = BotCommand("start", "开始使用 - 查看帮助信息")
    private_commands = [start_command, *_weather_commands(), *_subscription_commands()]
    # Groups get a shorter menu: /start's location keyboard is private-only and
    # a long list clutters the group command popup.
    group_commands = [*_weather_commands(), *_subscription_commands()]

    try:
        await _set_commands(application, private_commands, BotCommandScopeAllPrivateChats())
        await _set_commands(application, group_commands, BotCommandScopeAllGroupChats())
        # Default scope covers channels and anything Telegram adds later.
        await _set_commands(application, private_commands)
        logger.info("✅ Bot命令已按私聊/群聊分别注册到Telegram")
    except Exception as error:
        logger.warning(f"命令注册失败: {error}")

    # Profile texts: what users see when they find the bot but have not started it.
    for setter, value, label in (
        (application.bot.set_my_short_description, BOT_SHORT_DESCRIPTION, "短描述"),
        (application.bot.set_my_description, BOT_DESCRIPTION, "描述"),
    ):
        try:
            await setter(value)
        except Exception as error:
            logger.warning(f"设置 Bot {label} 失败: {error}")


def create_app() -> Application:
    """Factory to create the PTB Application."""
    logger.info(
        "Telegram client: python-telegram-bot {} (typed Bot API support {})",
        ptb_version,
        BOT_API_VERSION,
    )
    os.makedirs("data", exist_ok=True)
    # Snapshot the previous run's subscriptions before PTB starts rewriting it.
    rotate_persistence_backups(PERSISTENCE_PATH, settings.persistence_backup_count)
    persistence = PicklePersistence(filepath=PERSISTENCE_PATH)

    deps = BotDependencies(
        weather_service=WeatherFusionService(),
        llm_service=LLMService(),
    )

    async def close_resources(application: Application):
        await cache.cancel_inflight()
        await asyncio.gather(
            deps.weather_service.aclose(),
            deps.llm_service.aclose(),
            return_exceptions=True,
        )
        await cache.close()

    builder = (
        Application.builder()
        .token(settings.bot_token)
        .persistence(persistence)
        .post_init(_register_bot_commands)
        .post_shutdown(close_resources)
    )

    # Pushes fan out to every subscriber at once; without a limiter that trips
    # Telegram's flood control. aiolimiter ships with python-telegram-bot[all].
    if settings.enable_rate_limiter:
        try:
            from telegram.ext import AIORateLimiter

            builder = builder.rate_limiter(AIORateLimiter())
            logger.info("AIORateLimiter enabled")
        except (ImportError, RuntimeError) as error:
            logger.warning(f"AIORateLimiter unavailable, continuing without it: {error}")

    app = builder.build()

    weather = WeatherHandlers(deps)
    reports = ReportHandlers(deps)
    inline = InlineHandlers(deps)
    callbacks = CallbackHandlers(deps, weather_handlers=weather, report_handlers=reports)
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
    # 私聊里直接发城市名即可查询（不影响群聊与命令）
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            weather.handle_private_text,
        )
    )
    app.add_handler(CallbackQueryHandler(callbacks.handle_callback))
    app.add_handler(InlineQueryHandler(inline.handle_inline_query))
    app.add_handler(ChosenInlineResultHandler(reports.handle_chosen_inline_result))

    from core.scheduler import setup_scheduler

    setup_scheduler(app, deps.weather_service, deps.llm_service)

    async def handle_error(update, context):
        logger.opt(exception=context.error).error("Unhandled Telegram update error")

        # Give the user minimal feedback instead of silence.
        chat = getattr(update, "effective_chat", None) if update else None
        if chat is not None:
            try:
                await context.bot.send_message(chat_id=chat.id, text="❌ 处理请求时出错，请稍后重试。")
            except Exception:
                pass

        # Forward unexpected failures to the configured admin.
        if settings.super_admin_id and chat is not None and chat.id != settings.super_admin_id:
            try:
                await context.bot.send_message(
                    chat_id=settings.super_admin_id,
                    text=f"⚠️ Bot error: {type(context.error).__name__}: {context.error}"[:1000],
                    disable_notification=True,
                )
            except Exception:
                pass

    app.add_error_handler(handle_error)

    return app
