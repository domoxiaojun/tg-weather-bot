import asyncio
import re
from uuid import uuid4

from loguru import logger
from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.handlers.common import BotDependencies, parse_location_and_view
from core.handlers.special_queries import (
    extract_special_query,
    resolve_tide,
    resolve_typhoon,
)
from services.telegram_rich import input_rich_message_content, rich
from utils.formatter import format_weather_response, get_weather_keyboard
from utils.rich_formatter import build_weather_blocks


def extract_guest_request(message, bot_username: str | None) -> str:
    """Extract the weather query after ``@botname`` from a guest summon."""
    text = ((getattr(message, "text", None) or getattr(message, "caption", None) or "").strip())
    if bot_username:
        username = bot_username.lstrip("@")
        text = re.sub(rf"(?<!\w)@{re.escape(username)}\b", " ", text, count=1, flags=re.IGNORECASE)
    text = text.strip(" \t\n，,:：")
    if text.lower().startswith("/tq"):
        text = text[3:].strip()
    if text:
        return text

    # Mentioning the bot while replying to "珠海 明天" is a natural Guest
    # Mode interaction. Telegram supplies only that replied message, not chat
    # history, so it is safe and bounded context rather than ambient scraping.
    replied = getattr(message, "reply_to_message", None)
    replied_text = (
        getattr(replied, "text", None) or getattr(replied, "caption", None) or ""
        if replied is not None
        else ""
    )
    return replied_text.strip() if len(replied_text.strip()) <= 80 else ""


class GuestHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    @staticmethod
    def _result(
        *,
        title: str,
        blocks: list,
        fallback_text: str,
        reply_markup=None,
        parse_mode=None,
    ) -> tuple[InlineQueryResultArticle, InlineQueryResultArticle]:
        common = {
            "id": str(uuid4()),
            "title": title,
            "reply_markup": reply_markup,
        }
        return (
            InlineQueryResultArticle(
                **common,
                input_message_content=input_rich_message_content(blocks=blocks),
            ),
            InlineQueryResultArticle(
                **common,
                input_message_content=InputTextMessageContent(
                    fallback_text,
                    parse_mode=parse_mode,
                ),
            ),
        )

    @staticmethod
    async def _answer(context: ContextTypes.DEFAULT_TYPE, message, rich_result, plain_result) -> None:
        guest_query_id = message.guest_query_id
        if await rich.answer_guest_query(context.bot, guest_query_id, rich_result):
            return
        await context.bot.answer_guest_query(guest_query_id, plain_result)

    async def _answer_notice(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        message,
        *,
        title: str,
        detail: str,
    ) -> None:
        from services.telegram_rich import heading, paragraph

        rich_result, plain_result = self._result(
            title=title,
            blocks=[heading(title, size=4), paragraph(detail)],
            fallback_text=f"{title}\n{detail}",
        )
        await self._answer(context, message, rich_result, plain_result)

    async def handle_guest_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reply once to a Guest Mode mention without joining the target chat."""
        message = update.guest_message
        if message is None or not message.guest_query_id:
            return

        request = extract_guest_request(message, getattr(context.bot, "username", None))
        if not request:
            await self._answer_notice(
                context,
                message,
                title="🌤️ DomoWeather 天气查询",
                detail=(
                    "在 @机器人名 后输入城市，例如：@机器人名 珠海 明天\n"
                    "也可：台风 / 台风 珠海 · 潮汐 青岛"
                ),
            )
            return

        try:
            parts = request.split()
            special_mode, special_location = extract_special_query(parts)

            if special_mode in {"typhoon", "tide"}:
                if special_mode == "tide" and not special_location:
                    await self._answer_notice(
                        context,
                        message,
                        title="🌊 需要沿海城市",
                        detail="请写：@机器人名 潮汐 青岛",
                    )
                    return
                if special_mode == "typhoon":
                    payload = await asyncio.wait_for(
                        resolve_typhoon(self.deps, special_location),
                        timeout=10.0,
                    )
                else:
                    payload = await asyncio.wait_for(
                        resolve_tide(self.deps, special_location),
                        timeout=10.0,
                    )
                from services.telegram_rich import heading, paragraph

                blocks = payload.blocks
                if not blocks:
                    plain_detail = (
                        payload.fallback_text.replace("<b>", "")
                        .replace("</b>", "")
                        .replace("• ", "")
                    )
                    blocks = [
                        heading(payload.title, size=3),
                        paragraph(plain_detail[:500]),
                    ]
                rich_result, plain_result = self._result(
                    title=payload.title,
                    blocks=blocks,
                    fallback_text=payload.fallback_text,
                    parse_mode=ParseMode.HTML,
                )
                await self._answer(context, message, rich_result, plain_result)
                return

            location, view_type, start_day, days = parse_location_and_view(parts)
            if not location:
                await self._answer_notice(
                    context,
                    message,
                    title="⚠️ 缺少城市",
                    detail=(
                        "请在提及后输入城市，例如：@机器人名 北京 24h\n"
                        "也可：@机器人名 台风 珠海 · @机器人名 潮汐 青岛"
                    ),
                )
                return

            profile = view_type if view_type in {"hourly", "daily", "rain", "indices"} else "full"
            data = await asyncio.wait_for(
                self.deps.weather_service.get_fused_weather(location, profile=profile),
                timeout=8.0,
            )
            if not data:
                await self._answer_notice(
                    context,
                    message,
                    title="❌ 未找到天气数据",
                    detail=f"请检查城市名称：{location}",
                )
                return

            keyboard = get_weather_keyboard(
                data.location_name,
                show_charts=True,
                coords=data.coords,
                view_type=view_type,
            )
            rich_result, plain_result = self._result(
                title=f"🌤️ {data.location_name} 天气",
                blocks=build_weather_blocks(
                    data,
                    view_type=view_type,
                    days=days,
                    start_day=start_day,
                ),
                fallback_text=format_weather_response(
                    data,
                    view_type=view_type,
                    days=days,
                    start_day=start_day,
                ),
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            await self._answer(context, message, rich_result, plain_result)
        except asyncio.TimeoutError:
            await self._answer_notice(
                context,
                message,
                title="⏱️ 查询超时",
                detail="天气服务响应较慢，请稍后重新提及我。",
            )
        except Exception as error:
            logger.error(f"Guest weather query failed: {error}")
            try:
                await self._answer_notice(
                    context,
                    message,
                    title="⚠️ 查询失败",
                    detail="系统繁忙，请稍后重新提及我。",
                )
            except Exception as reply_error:
                logger.error(f"Guest error reply failed: {reply_error}")
