import time
from html import escape

from loguru import logger
from telegram import Update
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.ext import ContextTypes

from core.config import settings
from core.handlers.common import BotDependencies, join_location_args
from core.handlers.messages import send_text
from services.telegram_rich import FEATURE_DRAFT, FEATURE_SEND, next_draft_id, rich, thinking


class ReportHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    def _make_stream_editor(self, edit_func, title: str, min_interval: float = 2.0):
        """Build a throttled on_progress callback that live-edits one message.

        Telegram rate-limits message edits, so partial output is flushed at
        most every min_interval seconds; chunks are HTML-sanitized first.
        """
        state = {"last_time": 0.0, "last_text": ""}
        svc = self.deps.llm_service

        async def on_progress(partial: str):
            now = time.monotonic()
            if now - state["last_time"] < min_interval:
                return
            preview = svc.preview_stream_text(partial)
            if not preview or preview == state["last_text"]:
                return
            state["last_time"] = now
            state["last_text"] = preview
            try:
                await edit_func(f"{title}\n\n{preview} ⏳")
            except Exception as e:
                logger.debug(f"Stream edit failed: {e}")

        return on_progress

    async def report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /report command for AI-generated summary."""
        if not context.args:
            await send_text(update, context, "请提供城市名称，例如：<code>/report 北京</code>", parse_mode=ParseMode.HTML)
            return

        location = join_location_args(list(context.args))

        message = update.effective_message
        try:
            if message:
                await message.set_reaction("👀")
        except Exception:
            pass

        if not self.deps.llm_service.provider:
            await send_text(update, context, "⚠️ AI 天气日报功能尚未配置。")
            return

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        try:
            weather_data = await self.deps.weather_service.get_fused_weather(location, profile="full")
            if not weather_data:
                await send_text(update, context, f"❌ 未找到城市：{location}")
                return
            await self.send_report_for_weather(update, context, weather_data)
        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            try:
                await send_text(update, context, "❌ 生成日报失败，请稍后重试。")
            except Exception as fallback_error:
                logger.error(f"Report fallback send failed: {fallback_error}")

    async def _stream_via_rich_draft(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        weather_data,
        title_html: str,
    ) -> bool:
        """Stream with sendRichMessageDraft, then persist with sendRichMessage.

        Drafts are a private-chat-only 30-second animated preview that is never
        stored, so the final message must still be sent explicitly. Returns
        False before anything is shown if the capability is unavailable, so the
        caller can use the placeholder+edit path instead.
        """
        chat = update.effective_chat
        if (
            not settings.enable_rich_report_streaming
            or chat is None
            or chat.type != ChatType.PRIVATE
            or not rich.supports(FEATURE_DRAFT)
            or not rich.supports(FEATURE_SEND)
        ):
            return False

        draft_id = next_draft_id()
        state = {"last_time": 0.0, "alive": True}

        # Probe once with an empty-ish draft: if the server rejects it we can
        # still fall back before the user has seen anything.
        if not await rich.stream_draft(
            context.bot, chat.id, draft_id, blocks=[thinking("正在读取天气数据…")]
        ):
            return False

        async def on_progress(partial: str):
            if not state["alive"]:
                return
            now = time.monotonic()
            if now - state["last_time"] < 1.2:
                return
            state["last_time"] = now
            preview = self.deps.llm_service.preview_stream_text(partial)
            if not preview:
                return
            if not await rich.stream_draft(
                context.bot, chat.id, draft_id, blocks=[thinking(preview)]
            ):
                state["alive"] = False

        report_text = await self.deps.llm_service.generate_weather_report(
            weather_data, on_progress=on_progress
        )

        sent = await rich.send_rich(
            context.bot, chat.id, html=f"{title_html}\n\n{report_text}"
        )
        if sent is not None:
            return True

        # The draft expires on its own; deliver the finished report normally.
        await send_text(
            update,
            context,
            f"{title_html}\n\n{report_text}",
            parse_mode=ParseMode.HTML,
        )
        return True

    async def send_report_for_weather(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        weather_data,
    ):
        """Send a streaming AI report into the chat (shared by /report and buttons)."""
        title = f"🤖 <b>{escape(weather_data.location_name)} 天气日报</b>"

        if await self._stream_via_rich_draft(update, context, weather_data, title):
            return
        placeholder = await send_text(
            update,
            context,
            f"{title}\n\n⏳ 正在生成 AI 天气日报...",
            parse_mode=ParseMode.HTML,
        )

        on_progress = None
        if placeholder is not None:
            async def edit_placeholder(text_html: str):
                await placeholder.edit_text(text_html, parse_mode=ParseMode.HTML)

            on_progress = self._make_stream_editor(edit_placeholder, title)

        report_text = await self.deps.llm_service.generate_weather_report(
            weather_data,
            on_progress=on_progress,
        )

        final_html = f"{title}\n\n{report_text}"
        final_plain = f"🤖 {weather_data.location_name} 天气日报\n\n{report_text}"
        if placeholder is not None:
            # Prefer a rich edit so the finished report renders with rich
            # formatting even where draft streaming is unavailable (groups).
            if await rich.edit_rich(
                context.bot,
                chat_id=placeholder.chat_id,
                message_id=placeholder.message_id,
                html=final_html,
            ):
                return
            try:
                await placeholder.edit_text(final_html, parse_mode=ParseMode.HTML)
            except Exception as e:
                if "Message is not modified" in str(e):
                    return
                logger.warning(f"HTML edit failed, using plain text: {e}")
                try:
                    await placeholder.edit_text(final_plain)
                except Exception as edit_error:
                    logger.error(f"Report final edit failed: {edit_error}")
                    await send_text(update, context, final_plain, parse_mode=None)
        else:
            try:
                await send_text(update, context, final_html, parse_mode=ParseMode.HTML)
            except Exception as e:
                logger.warning(f"HTML parsing failed, using plain text: {e}")
                await send_text(update, context, final_plain, parse_mode=None)

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
            weather_data = await self.deps.weather_service.get_fused_weather(location, profile="full")
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

            title = f"🤖 <b>{escape(weather_data.location_name)} 天气日报</b>"

            async def edit_inline(text_html: str):
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=text_html,
                    parse_mode=ParseMode.HTML,
                )

            # Inline edits share stricter rate limits; throttle harder.
            on_progress = self._make_stream_editor(edit_inline, title, min_interval=2.5)

            report_text = await self.deps.llm_service.generate_weather_report(
                weather_data,
                on_progress=on_progress,
            )
            if await rich.edit_rich(
                context.bot,
                inline_message_id=inline_message_id,
                html=f"{title}\n\n{report_text}",
            ):
                return
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"{title}\n\n{report_text}",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                if "Message is not modified" in str(e):
                    return
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
