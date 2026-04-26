import asyncio
from contextlib import suppress
from html import escape

from loguru import logger
from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from core.handlers.common import BotDependencies
from core.handlers.messages import send_text


class ReportHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    async def report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /report command for AI-generated summary."""
        if not context.args:
            await send_text(update, context, "请提供城市名称，例如：<code>/report 北京</code>", parse_mode=ParseMode.HTML)
            return

        location = context.args[0]

        try:
            await update.message.set_reaction("👀")
        except Exception:
            pass

        if not self.deps.llm_service.provider:
            await send_text(update, context, "⚠️ AI 天气日报功能尚未配置。")
            return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        try:
            weather_data = await self.deps.weather_service.get_fused_weather(location)
            if not weather_data:
                await send_text(update, context, f"❌ 未找到城市：{location}")
                return

            async def keep_typing():
                while True:
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
                    await asyncio.sleep(4)

            typing_task = asyncio.create_task(keep_typing())
            try:
                report_text = await self.deps.llm_service.generate_weather_report(weather_data)
            finally:
                typing_task.cancel()
                with suppress(asyncio.CancelledError):
                    await typing_task

            title = f"🤖 <b>{escape(weather_data.location_name)} 天气日报</b>"
            try:
                await send_text(
                    update,
                    context,
                    text=f"{title}\n\n{report_text}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning(f"HTML parsing failed, using plain text: {e}")
                await send_text(
                    update,
                    context,
                    text=f"🤖 {weather_data.location_name} 天气日报\n\n{report_text}",
                    parse_mode=None,
                )
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            try:
                await send_text(update, context, "❌ 生成日报失败，请稍后重试。")
            except Exception as fallback_error:
                logger.error(f"Report fallback send failed: {fallback_error}")

    async def handle_chosen_inline_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Generate AI reports after the user chooses an inline AI placeholder."""
        result_id = update.chosen_inline_result.result_id
        inline_message_id = update.chosen_inline_result.inline_message_id

        logger.info(f"Received Chosen Inline Result: {result_id}, MsgID: {inline_message_id}")

        if not result_id.startswith("ai_report:"):
            logger.debug("Not an AI report trigger.")
            return

        location = result_id.split(":", 1)[1]
        if not inline_message_id:
            logger.error("No inline_message_id found to edit.")
            return

        try:
            weather_data = await self.deps.weather_service.get_fused_weather(location)
            if not weather_data:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"❌ 获取数据失败：{location}",
                )
                return

            if not self.deps.llm_service.provider:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text="⚠️ LLM 服务未配置，无法生成日报。",
                )
                return

            report_text = await self.deps.llm_service.generate_weather_report(weather_data)
            title = f"🤖 <b>{escape(weather_data.location_name)} 天气日报</b>"
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"{title}\n\n{report_text}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning(f"HTML parsing failed in inline mode, using plain text: {e}")
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"🤖 {weather_data.location_name} 天气日报\n\n{report_text}",
                    parse_mode=None,
                )
        except Exception as e:
            logger.error(f"Async inline generation failed: {e}")
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text="❌ 生成失败，请稍后重试。",
                )
            except Exception:
                pass
