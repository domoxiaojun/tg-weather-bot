import sys

from loguru import logger

try:
    import uvloop

    uvloop.install()
except ImportError:
    uvloop = None

from core.bot import create_app
from core.config import settings

# Configure Loguru
logger.remove()
logger.add(sys.stderr, level=settings.log_level.upper())

logger.add("logs/weather_bot.log", rotation="10 MB", retention="10 days", level="DEBUG")

def main():
    """Main Entry Point"""
    logger.info("Starting DomoWeather Bot...")

    try:
        app = create_app()

        from telegram import Update

        # Choose mode based on configuration
        if settings.bot_mode.lower() == "webhook":
            # Webhook Mode (Production)
            if not settings.webhook_url:
                logger.error("Webhook模式需要配置 WEBHOOK_URL")
                sys.exit(1)

            logger.info("Bot is starting in WEBHOOK mode...")
            logger.info(f"Webhook URL: {settings.webhook_url}{settings.webhook_path}")
            logger.info(f"Listening on port: {settings.webhook_port}")
            
            app.run_webhook(
                listen="0.0.0.0",
                port=settings.webhook_port,
                url_path=settings.webhook_path,
                webhook_url=f"{settings.webhook_url}{settings.webhook_path}",
                secret_token=settings.webhook_secret,
                allowed_updates=Update.ALL_TYPES
            )
        else:
            # Polling Mode (Development/Default)
            logger.info(f"Bot is starting in POLLING mode... (User: {settings.super_admin_id or 'Unknown'})")
            app.run_polling(allowed_updates=Update.ALL_TYPES)

    except Exception:
        # Exit non-zero so systemd/Docker restart policies can kick in.
        logger.exception("Fatal Error")
        sys.exit(1)

if __name__ == "__main__":
    main()
