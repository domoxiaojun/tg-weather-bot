import sys
from loguru import logger
from telegram.ext import Application

from core.bot import create_app
from core.config import settings

# Configure Loguru
logger.remove()
logger.add(sys.stderr, level=settings.log_level.upper())

logger.add("logs/weather_bot.log", rotation="10 MB", retention="10 days", level="DEBUG")

def main():
    """Main Entry Point"""
    logger.info("Starting DomoWeather Bot...")
    
    # Validation
    if "123456" in settings.bot_token:
        logger.error("Please configure your BOT_TOKEN in .env file!")
        return

    try:
        app = create_app()
        
        # Initialize Scheduler
        from core.scheduler import setup_scheduler
        setup_scheduler(app)
        
        # 设置Bot命令列表（自动注册到Telegram）
        async def post_init(application: Application):
            from telegram import BotCommand
            commands = [
                BotCommand("start", "开始使用 - 查看帮助信息"),
                BotCommand("tq", "天气查询 - /tq [城市] [参数]"),
                BotCommand("chart", "温度趋势图 - /chart [城市] [daily|hourly]"),
                BotCommand("report", "AI天气日报 - /report [城市]"),
                BotCommand("daily_sub", "订阅早安简报 - /daily_sub [城市]"),
                BotCommand("daily_my", "我的订阅 - 查看已订阅城市"),
                BotCommand("daily_unsub", "取消订阅 - /daily_unsub [城市]"),
            ]
            await application.bot.set_my_commands(commands)
            logger.info("✅ Bot命令已注册到Telegram")
        
        app.post_init = post_init
        
        from telegram import Update
        logger.info(f"Bot is polling... (User: {settings.super_admin_id or 'Unknown'})")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.critical(f"Fatal Error: {e}")

if __name__ == "__main__":
    main()
