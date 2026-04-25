import io
from typing import Any

from telegram import InputFile, Update
from telegram.ext import ContextTypes


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
