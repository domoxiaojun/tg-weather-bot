"""Bot API 10.1/10.2 rich + ephemeral message support.

python-telegram-bot 22.8 only carries typed support through Bot API 10.0, so
these newer methods are issued through :meth:`telegram.Bot.do_api_request` —
PTB's documented path for API methods it does not yet wrap. Nested dicts/lists
passed via ``api_kwargs`` are JSON-serialized by PTB's request layer, so the
payload builders below emit plain dicts.

Every call degrades to the caller's existing MarkdownV2/HTML path: helpers
return ``None``/``False`` instead of raising. Capabilities the server rejects
outright are remembered, so an unsupported deployment is probed once rather
than on every message.

Wire format verified against the Bot API 10.2 specification (2026-07-14):
``InputRichMessage`` carries ``blocks``/``html``/``markdown`` (not
``text``/``parse_mode``), table cells require ``align`` and ``valign``, and
``sendRichMessageDraft`` is a private-chat-only 30-second preview that must be
followed by ``sendRichMessage`` to persist the result.
"""

import itertools
import warnings
from typing import Any, Optional, Union

from loguru import logger
from telegram import Message, SentGuestMessage
from telegram.error import BadRequest, EndPointNotFound, Forbidden
from telegram.warnings import PTBUserWarning

from core.config import settings

# A RichText is a plain string, a list of RichText, or a typed dict.
RichText = Union[str, list, dict]

# Capability keys tracked independently: one broken surface must not silently
# disable the others.
FEATURE_SEND = "send_rich_message"
FEATURE_EDIT = "edit_rich_message"
FEATURE_DRAFT = "send_rich_message_draft"
FEATURE_INLINE = "inline_rich_message"
FEATURE_GUEST = "guest_rich_message"
FEATURE_EPHEMERAL = "ephemeral_message"

# A wrong payload is deterministic, so a couple of 400s mean "stop trying".
_BAD_REQUEST_LIMIT = 3

_draft_ids = itertools.count(1)


def next_draft_id() -> int:
    """Draft ids must be non-zero; reusing one animates the change."""
    return next(_draft_ids)


# --------------------------------------------------------------------------- #
# RichText helpers
# --------------------------------------------------------------------------- #

def bold(text: RichText) -> dict:
    return {"type": "bold", "text": text}


def italic(text: RichText) -> dict:
    return {"type": "italic", "text": text}


def code(text: RichText) -> dict:
    return {"type": "code", "text": text}


def marked(text: RichText) -> dict:
    """Highlighted text — useful for warnings and peaks."""
    return {"type": "marked", "text": text}


def link(text: RichText, url: str) -> dict:
    return {"type": "url", "text": text, "url": url}


def custom_emoji(custom_emoji_id: str, alternative_text: str = "▪️") -> dict:
    """Bot API 10.1 ``RichTextCustomEmoji``.

    ``alternative_text`` is shown on clients that cannot render the pack.
    Requires the bot owner to have Telegram Premium (private/group/supergroup)
    or a Fragment username upgrade for broader surfaces.
    """
    return {
        "type": "custom_emoji",
        "custom_emoji_id": str(custom_emoji_id),
        "alternative_text": alternative_text or "▪️",
    }


# --------------------------------------------------------------------------- #
# Block builders (pure — unit-testable without network)
# --------------------------------------------------------------------------- #

def paragraph(text: RichText) -> dict:
    return {"type": "paragraph", "text": text}


def heading(text: RichText, size: int = 3) -> dict:
    """Section heading; size 1 is largest, 6 smallest."""
    return {"type": "heading", "text": text, "size": max(1, min(6, int(size)))}


def divider() -> dict:
    return {"type": "divider"}


def footer(text: RichText) -> dict:
    return {"type": "footer", "text": text}


def thinking(text: RichText) -> dict:
    """Block designed for in-progress AI output."""
    return {"type": "thinking", "text": text}




def blockquote(blocks: list, credit: Optional[RichText] = None) -> dict:
    block = {"type": "blockquote", "blocks": blocks}
    if credit is not None:
        block["credit"] = credit
    return block


def details(summary: RichText, blocks: list, *, is_open: bool = False) -> dict:
    """Collapsible section — keeps long views scrollable."""
    block = {"type": "details", "summary": summary, "blocks": blocks}
    if is_open:
        block["is_open"] = True
    return block


def _as_blocks(item: Any) -> list:
    """Accept a block, a list of blocks, or bare RichText for list items."""
    if isinstance(item, dict) and "type" in item and item["type"] in _BLOCK_TYPES:
        return [item]
    if isinstance(item, list) and item and all(
        isinstance(entry, dict) and entry.get("type") in _BLOCK_TYPES for entry in item
    ):
        return item
    return [paragraph(item)]


_BLOCK_TYPES = {
    "paragraph",
    "heading",
    "pre",
    "footer",
    "divider",
    "math",
    "anchor",
    "list",
    "blockquote",
    "pullquote",
    "collage",
    "slideshow",
    "table",
    "details",
    "map",
    "animation",
    "audio",
    "photo",
    "video",
    "voice_note",
    "thinking",
}


def bullet_list(items: list) -> dict:
    return {"type": "list", "items": [{"blocks": _as_blocks(item)} for item in items]}






def cell(
    text: Optional[RichText] = None,
    *,
    is_header: bool = False,
    align: str = "left",
    valign: str = "middle",
    colspan: Optional[int] = None,
    rowspan: Optional[int] = None,
) -> dict:
    """Table cell. ``align``/``valign`` are required by the API, so they default."""
    entry: dict = {"align": align, "valign": valign}
    if text is not None:
        entry["text"] = text
    if is_header:
        entry["is_header"] = True
    if colspan and colspan > 1:
        entry["colspan"] = colspan
    if rowspan and rowspan > 1:
        entry["rowspan"] = rowspan
    return entry


def table(
    rows: list,
    *,
    headers: Optional[list] = None,
    aligns: Optional[list] = None,
    bordered: bool = False,
    striped: bool = True,
    caption: Optional[RichText] = None,
) -> dict:
    """Build a table from raw text rows.

    ``rows`` is a list of rows, each a list of RichText (or pre-built cells).
    ``aligns`` optionally gives a per-column alignment.
    """
    def build_row(values: list, is_header: bool) -> list:
        built = []
        for index, value in enumerate(values):
            if isinstance(value, dict) and "align" in value:
                built.append(value)
                continue
            align = "left"
            if aligns and index < len(aligns):
                align = aligns[index]
            elif index:
                align = "right"
            built.append(cell(value, is_header=is_header, align=align))
        return built

    cells = []
    if headers:
        cells.append(build_row(headers, True))
    cells.extend(build_row(row, False) for row in rows)

    block: dict = {"type": "table", "cells": cells}
    if bordered:
        block["is_bordered"] = True
    if striped:
        block["is_striped"] = True
    if caption is not None:
        block["caption"] = caption
    return block


def photo_block(
    media: str,
    caption: Optional[RichText] = None,
    credit: Optional[RichText] = None,
) -> dict:
    """Embed an already-uploaded photo (file_id) inside a rich message.

    This is what lets a chart and a long text body live in one message instead
    of being split by Telegram's 1024-character caption limit.
    """
    block: dict = {"type": "photo", "photo": {"type": "photo", "media": media}}
    if caption is not None:
        block_caption: dict = {"text": caption}
        if credit is not None:
            block_caption["credit"] = credit
        block["caption"] = block_caption
    return block


def rich_message(
    *,
    blocks: Optional[list] = None,
    html: Optional[str] = None,
    markdown: Optional[str] = None,
    media: Optional[list] = None,
    skip_entity_detection: bool = True,
) -> dict:
    """Assemble an InputRichMessage payload.

    Exactly one content source must be given: ``blocks``, ``html`` or
    ``markdown``.
    """
    provided = [name for name, value in (("blocks", blocks), ("html", html), ("markdown", markdown)) if value]
    if len(provided) != 1:
        raise ValueError(f"rich_message needs exactly one of blocks/html/markdown, got {provided}")

    payload: dict = {}
    if blocks:
        payload["blocks"] = blocks
    elif html:
        payload["html"] = html
    else:
        payload["markdown"] = markdown
    if media:
        payload["media"] = media
    if skip_entity_detection:
        # Weather text is full of numbers and dates; suppress auto-linkification.
        payload["skip_entity_detection"] = True
    return payload


def input_rich_message_content(
    *,
    blocks: Optional[list] = None,
    html: Optional[str] = None,
    markdown: Optional[str] = None,
) -> dict:
    """Build Bot API 10.1 ``InputRichMessageContent`` for query results.

    PTB 22.8 does not type this object yet, but its TelegramObject serializer
    safely carries this raw dictionary inside ``InlineQueryResultArticle``.
    The same shape is valid for inline, guest and Web App query results.
    """
    return {"rich_message": rich_message(blocks=blocks, html=html, markdown=markdown)}


def _api_dict(value: Any) -> Any:
    """Convert PTB TelegramObjects while leaving already-raw payloads intact."""
    converter = getattr(value, "to_dict", None)
    return converter() if callable(converter) else value


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #

class RichMessenger:
    """Issues rich/ephemeral calls and remembers what the server rejects."""

    def __init__(self) -> None:
        self._unsupported: set[str] = set()
        self._strikes: dict[str, int] = {}

    def reset(self) -> None:
        self._unsupported.clear()
        self._strikes.clear()

    @property
    def rich_enabled(self) -> bool:
        return bool(settings.enable_rich_messages)

    def supports(self, feature: str) -> bool:
        if feature == FEATURE_EPHEMERAL:
            if not settings.enable_ephemeral_messages:
                return False
        elif feature == FEATURE_GUEST:
            if not settings.enable_guest_mode or not self.rich_enabled:
                return False
        elif not self.rich_enabled:
            return False
        return feature not in self._unsupported

    def _disable(self, feature: str, reason: str) -> None:
        if feature in self._unsupported:
            return
        self._unsupported.add(feature)
        logger.warning(
            "Telegram capability '{}' unavailable, falling back permanently: {}",
            feature,
            reason,
        )

    def _handle_error(self, feature: str, error: BaseException) -> None:
        """Classify a failure: disable only when retrying cannot help."""
        if isinstance(error, EndPointNotFound):
            self._disable(feature, "endpoint not found in Bot API")
            return
        if isinstance(error, Forbidden):
            # Chat-level problem (blocked/kicked), not a capability issue.
            logger.debug(f"Rich call forbidden for {feature}: {error}")
            return
        message = str(error)
        if isinstance(error, BadRequest):
            # A rejected payload is deterministic: stop after a few strikes so
            # we do not pay a failed round trip on every single message.
            strikes = self._strikes.get(feature, 0) + 1
            self._strikes[feature] = strikes
            logger.warning(f"Rich call rejected ({feature}, strike {strikes}/{_BAD_REQUEST_LIMIT}): {message}")
            if strikes >= _BAD_REQUEST_LIMIT:
                self._disable(feature, f"repeated Bad Request: {message}")
            return
        logger.warning(f"Rich call failed transiently ({feature}): {type(error).__name__}: {message}")

    async def _call(self, bot, endpoint: str, payload: dict, feature: str, *, return_type=None):
        try:
            # PTB warns when do_api_request is used for an endpoint it wraps
            # (editMessageText); the wrapped signature cannot express
            # rich_message without a text, so the warning is expected here.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", PTBUserWarning)
                return await bot.do_api_request(endpoint, api_kwargs=payload, return_type=return_type)
        except Exception as error:  # noqa: BLE001 - classified below, never re-raised
            self._handle_error(feature, error)
            return None

    async def send_rich(
        self,
        bot,
        chat_id,
        *,
        blocks: Optional[list] = None,
        html: Optional[str] = None,
        reply_markup=None,
        disable_notification: Optional[bool] = None,
        message_thread_id: Optional[int] = None,
        reply_parameters: Optional[dict] = None,
    ) -> Optional[Message]:
        """Send a rich message; returns None when the caller should fall back."""
        if not self.supports(FEATURE_SEND):
            return None
        payload: dict = {
            "chat_id": chat_id,
            "rich_message": rich_message(blocks=blocks, html=html),
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if disable_notification:
            payload["disable_notification"] = True
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if reply_parameters is not None:
            payload["reply_parameters"] = reply_parameters
        result = await self._call(bot, "sendRichMessage", payload, FEATURE_SEND, return_type=Message)
        return result if isinstance(result, Message) else None

    async def edit_rich(
        self,
        bot,
        *,
        chat_id=None,
        message_id: Optional[int] = None,
        inline_message_id: Optional[str] = None,
        blocks: Optional[list] = None,
        html: Optional[str] = None,
        reply_markup=None,
    ) -> bool:
        """Replace a message's content with rich content. False → fall back."""
        if not self.supports(FEATURE_EDIT):
            return False
        payload: dict = {"rich_message": rich_message(blocks=blocks, html=html)}
        if inline_message_id is not None:
            payload["inline_message_id"] = inline_message_id
        else:
            payload["chat_id"] = chat_id
            payload["message_id"] = message_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._call(bot, "editMessageText", payload, FEATURE_EDIT)
        return result is not None

    async def stream_draft(
        self,
        bot,
        chat_id: int,
        draft_id: int,
        *,
        blocks: Optional[list] = None,
        html: Optional[str] = None,
    ) -> bool:
        """Stream a partial rich message (private chats only, 30s preview).

        The draft is never persisted — the caller must send the final message
        with :meth:`send_rich` once generation finishes.
        """
        if not self.supports(FEATURE_DRAFT):
            return False
        payload = {
            "chat_id": chat_id,
            "draft_id": draft_id,
            "rich_message": rich_message(blocks=blocks, html=html),
        }
        result = await self._call(bot, "sendRichMessageDraft", payload, FEATURE_DRAFT)
        return result is not None

    async def answer_inline_query(
        self,
        bot,
        inline_query_id: str,
        results: list,
        *,
        cache_time: int = 300,
        is_personal: bool = False,
        next_offset: Optional[str] = None,
        button=None,
    ) -> bool:
        """Answer an inline query containing ``InputRichMessageContent``.

        ``answerInlineQuery`` itself is old, but PTB's typed result union does
        not include the 10.1 rich content variant. Calling through the public
        raw endpoint keeps the new payload intact and gives it an independent
        capability/fallback circuit breaker.
        """
        if not self.supports(FEATURE_INLINE):
            return False
        payload: dict = {
            "inline_query_id": inline_query_id,
            "results": [_api_dict(result) for result in results],
            "cache_time": cache_time,
            "is_personal": is_personal,
        }
        if next_offset is not None:
            payload["next_offset"] = next_offset
        if button is not None:
            payload["button"] = _api_dict(button)
        result = await self._call(bot, "answerInlineQuery", payload, FEATURE_INLINE)
        return result is True

    async def answer_guest_query(self, bot, guest_query_id: str, result) -> bool:
        """Answer one Guest Mode summon with a rich inline-query result."""
        if not self.supports(FEATURE_GUEST):
            return False
        payload = {
            "guest_query_id": guest_query_id,
            "result": _api_dict(result),
        }
        sent = await self._call(
            bot,
            "answerGuestQuery",
            payload,
            FEATURE_GUEST,
            return_type=SentGuestMessage,
        )
        return isinstance(sent, SentGuestMessage)

    async def send_ephemeral(
        self,
        bot,
        chat_id,
        text: str,
        receiver_user_id: int,
        *,
        parse_mode: Optional[str] = None,
        reply_markup=None,
        callback_query_id: Optional[str] = None,
    ) -> Optional[Message]:
        """Send a group message only one user can see. None → fall back.

        Telegram restricts this to group/supergroup chats and does not
        guarantee delivery to offline users, so callers must treat a ``None``
        result as "send normally instead".
        """
        if not self.supports(FEATURE_EPHEMERAL):
            return None
        # sendMessage is already typed by PTB, so the 10.2-only parameters ride
        # along via api_kwargs — that keeps rate limiting and Defaults intact.
        extra: dict = {"receiver_user_id": receiver_user_id}
        if callback_query_id:
            extra["callback_query_id"] = callback_query_id
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                api_kwargs=extra,
            )
        except Exception as error:  # noqa: BLE001 - classified, never re-raised
            self._handle_error(FEATURE_EPHEMERAL, error)
            return None


# Module-level singleton; capability state is per process.
rich = RichMessenger()
