import io
from typing import Any, Optional

from loguru import logger
from telegram import InputFile, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from services.chart_cache import PreparedChart, remember_chart_file_id
from services.telegram_rich import FEATURE_EPHEMERAL, FEATURE_SEND, photo_block, rich
from utils.formatter import format_weather_response
from utils.rich_formatter import build_weather_blocks


_WEATHER_CHART_ATTACHMENT = "weather_chart"


def _embed_weather_chart(
    blocks: list,
    chart: Optional[PreparedChart],
) -> tuple[list, Optional[dict[str, InputFile]]]:
    """Insert one chart block and prepare an optional multipart attachment."""
    if chart is None:
        return blocks, None

    attachments = None
    if chart.file_id:
        media = chart.file_id
    elif chart.png_bytes:
        media = f"attach://{_WEATHER_CHART_ATTACHMENT}"
        attachments = {
            _WEATHER_CHART_ATTACHMENT: InputFile(
                io.BytesIO(chart.png_bytes),
                filename=f"{chart.chart_type}.png",
            )
        }
    else:
        return blocks, None

    # Keep the location/date heading first, then show the visual overview
    # before the denser text and table sections.
    insert_at = 1 if blocks else 0
    return [
        *blocks[:insert_at],
        photo_block(media, chart.caption),
        *blocks[insert_at:],
    ], attachments


def _chat_id(update: Update):
    return update.effective_chat.id if update.effective_chat else None


async def send_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, **kwargs: Any):
    """Send a message to the chat without replying to the original message."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return None
    return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)


async def send_photo(update: Update, context: ContextTypes.DEFAULT_TYPE, photo: bytes | InputFile | str, **kwargs: Any):
    """Send a photo to the chat without replying to the original message."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return None

    if isinstance(photo, bytes):
        photo = InputFile(io.BytesIO(photo), filename="weather.png")

    return await context.bot.send_photo(chat_id=chat_id, photo=photo, **kwargs)


async def send_personal_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    **kwargs: Any,
):
    """Send a reply only the requesting user needs to see.

    In groups this becomes a Bot API 10.2 ephemeral message so personal
    bookkeeping (subscription lists, limits, confirmations) does not spam
    everyone. Private chats and unsupported servers fall back to a normal send.
    """
    chat = update.effective_chat
    user = getattr(update, "effective_user", None)
    if chat is None:
        return None

    if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) and user is not None:
        callback_query = getattr(update, "callback_query", None)
        callback_query_id = callback_query.id if callback_query else None
        sent = await rich.send_ephemeral(
            context.bot,
            chat.id,
            text,
            user.id,
            parse_mode=kwargs.get("parse_mode"),
            reply_markup=kwargs.get("reply_markup"),
            callback_query_id=callback_query_id,
        )
        if sent is not None:
            return sent

    return await context.bot.send_message(chat_id=chat.id, text=text, **kwargs)


async def send_weather_view(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id,
    data,
    *,
    view_type: str = "default",
    days: Optional[int] = None,
    start_day: int = 0,
    reply_markup=None,
    message_thread_id: Optional[int] = None,
    chart: Optional[PreparedChart] = None,
):
    """Send a weather view as rich blocks, falling back to MarkdownV2 text."""
    if rich.supports(FEATURE_SEND):
        try:
            blocks = build_weather_blocks(data, view_type=view_type, days=days, start_day=start_day)
            blocks, attachments = _embed_weather_chart(blocks, chart)
        except Exception as error:  # noqa: BLE001 - never let rendering break delivery
            logger.warning(f"Rich block build failed, using text view: {error}")
        else:
            sent = await rich.send_rich(
                context.bot,
                chat_id,
                blocks=blocks,
                reply_markup=reply_markup,
                message_thread_id=message_thread_id,
                attachments=attachments,
            )
            if sent is not None:
                if chart is not None:
                    await remember_chart_file_id(data, chart.chart_type, sent)
                return sent

    text = format_weather_response(data, view_type=view_type, days=days, start_day=start_day)
    return await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=reply_markup,
        message_thread_id=message_thread_id,
    )


async def edit_weather_view(
    context: ContextTypes.DEFAULT_TYPE,
    data,
    *,
    chat_id=None,
    message_id: Optional[int] = None,
    inline_message_id: Optional[str] = None,
    view_type: str = "default",
    days: Optional[int] = None,
    start_day: int = 0,
    reply_markup=None,
    chart: Optional[PreparedChart] = None,
) -> bool:
    """Edit a message to a weather view. Returns False if the caller must retry
    with the plain-text path (the rich attempt already fell back internally)."""
    if rich.supports(FEATURE_SEND):
        try:
            blocks = build_weather_blocks(data, view_type=view_type, days=days, start_day=start_day)
            blocks, attachments = _embed_weather_chart(blocks, chart)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"Rich block build failed, using text view: {error}")
        else:
            if await rich.edit_rich(
                context.bot,
                chat_id=chat_id,
                message_id=message_id,
                inline_message_id=inline_message_id,
                blocks=blocks,
                reply_markup=reply_markup,
                attachments=attachments,
            ):
                return True
    return False


__all__ = [
    "FEATURE_EPHEMERAL",
    "edit_weather_view",
    "send_personal_text",
    "send_photo",
    "send_text",
    "send_weather_view",
]
