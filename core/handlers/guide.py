"""/help — a button-paged usage guide.

Three pages flipped in place via ``help|{page}`` callbacks. Private chats use
rich blocks; group replies remain ephemeral HTML because ``sendRichMessage``
does not support the Bot API 10.2 ``receiver_user_id`` privacy parameter.
"""

import re

from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from core.handlers.messages import send_personal_text
from services.telegram_rich import FEATURE_SEND, heading, rich
from utils.rich_formatter import build_report_blocks
from utils.formatter import styled_button

GUIDE_DEFAULT_PAGE = "query"

_PAGES = {
    "query": (
        "🔍 查天气",
        "<b>🔍 查天气</b>\n\n"
        "• 私聊里<b>直接发城市名</b>即可：<code>北京</code>\n"
        "• 带天数/时段也行（不用空格）：<code>北京明天</code>、<code>上海降水</code>\n"
        "• 📍 发送位置消息 → 查你所在地的天气\n"
        "• 群里用命令：<code>/tq 北京</code>\n\n"
        "查询结果卡片上的按钮：\n"
        "🔄 刷新 · 视图切换（24小时/15天/指数）\n"
        "🌡️/🌧️/📆 图表 · 🤖 AI日报 · 📤 分享给朋友\n\n"
        "其他查询：<code>/typhoon</code> 台风 · <code>/tide</code> 潮汐 · <code>/chart</code> 图表",
    ),
    "push": (
        "🔔 订阅推送",
        "<b>🔔 订阅推送</b>\n\n"
        "<b>降雨提醒</b>——快下雨时提前通知你\n"
        "查天气后点卡片上的「🔔 降雨提醒」即可订阅。\n"
        "每个城市可选提醒档位：\n"
        "· 全部降雨 —— 任何降水都提醒，适合晒衣服/骑车\n"
        "· 一般降雨 —— 忽略毛毛雨（默认）\n"
        "· 仅大雨 —— 只在雨大到影响出行时提醒\n\n"
        "<b>早安简报</b>——每天定时的 AI 天气总结\n"
        "点卡片上的「📅 早安简报」订阅，时间按钮一点就改。\n\n"
        "官方灾害预警和台风影响会随降雨订阅自动推送；\n"
        "深夜自动免打扰（红/橙色预警除外），错过的会在醒来后补发。",
    ),
    "more": (
        "⚙️ 更多",
        "<b>⚙️ 更多功能</b>\n\n"
        "• <b>🤖 AI 日报</b>：点卡片按钮或 <code>/report</code>，"
        "AI 结合实时数据、生活指数和昨天的实测写一份穿衣/出行建议\n"
        "• <b>📤 任意聊天分享</b>：在任何输入框里 @ 本 bot + 城市名，"
        "直接把天气卡片发给朋友\n"
        "• <b>群聊</b>：支持全部命令；订阅是群共享的，个人查询用后即焚不刷屏\n\n"
        "数据来源：和风天气 · 彩云天气",
    ),
}


def build_guide(page: str):
    """(HTML text, keyboard) for one guide page; unknown pages get the default."""
    if page not in _PAGES:
        page = GUIDE_DEFAULT_PAGE
    text = _PAGES[page][1]
    tabs = [
        styled_button(
            ("✅ " if key == page else "") + title,
            style="primary" if key == page else None,
            callback_data=f"help|{key}",
        )
        for key, (title, _body) in _PAGES.items()
    ]
    rows = [tabs]
    if page == "push":
        rows.append([
            styled_button("🔔 我的降雨提醒", callback_data="submy|rain"),
            styled_button("📅 我的早安简报", callback_data="submy|daily"),
        ])
    return text, InlineKeyboardMarkup(rows)


def build_guide_blocks(page: str) -> list:
    """Rich-block counterpart of one guide page."""
    if page not in _PAGES:
        page = GUIDE_DEFAULT_PAGE
    title, html = _PAGES[page]
    _first_line, _separator, body = html.partition("\n")
    # build_report_blocks understands bold/italic; code tags are presentation
    # sugar here and must not leak literally into RichText.
    body = re.sub(r"</?code>", "", body)
    return [heading(title, size=3), *build_report_blocks(body)]


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help - 使用指南"""
    text, keyboard = build_guide(GUIDE_DEFAULT_PAGE)
    chat = update.effective_chat
    if chat is not None and chat.type == ChatType.PRIVATE and rich.supports(FEATURE_SEND):
        sent = await rich.send_rich(
            context.bot,
            chat.id,
            blocks=build_guide_blocks(GUIDE_DEFAULT_PAGE),
            reply_markup=keyboard,
        )
        if sent is not None:
            return
    await send_personal_text(
        update, context, text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )
