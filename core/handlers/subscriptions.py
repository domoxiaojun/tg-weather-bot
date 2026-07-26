from html import escape
from typing import Optional

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.config import settings
from core.handlers.common import BotDependencies
from core.handlers.messages import send_text
from core.scheduler import DEFAULT_DAILY_BRIEF_TIME, parse_brief_time


def render_subscription_list(chat_data: dict, kind: str):
    """Build (text, keyboard) for a subscription list with one-tap unsubscribe.

    kind: 'daily' or 'rain'. Returns (None, None) when the list is empty.
    """
    list_key = "daily_subs" if kind == "daily" else "subs"
    subs = chat_data.get(list_key, [])
    if not subs:
        return None, None

    sub_times = chat_data.get("daily_sub_times", {}) if kind == "daily" else {}
    title = "📅 <b>我的早安订阅</b>" if kind == "daily" else "🔔 <b>我的降雨提醒</b>"
    lines = [f"{title}："]
    buttons = []
    for index, location in enumerate(subs):
        if kind == "daily":
            brief_time = sub_times.get(location, DEFAULT_DAILY_BRIEF_TIME)
            lines.append(f"• {escape(location)}（{brief_time}）")
        else:
            lines.append(f"• {escape(location)}")
        buttons.append(
            InlineKeyboardButton(f"❌ {location}", callback_data=f"unsub|{kind}|{index}")
        )
    lines.append("\n点按钮即可取消订阅。")

    keyboard_rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


def remove_subscription_entry(chat_data: dict, kind: str, index: int) -> Optional[str]:
    """Remove subs[index] plus its auxiliary records; returns the removed name."""
    list_key = "daily_subs" if kind == "daily" else "subs"
    subs = chat_data.get(list_key, [])
    if not (0 <= index < len(subs)):
        return None
    removed = subs.pop(index)
    if kind == "daily":
        chat_data.get("daily_sub_times", {}).pop(removed, None)
        chat_data.get("daily_brief_last_sent", {}).pop(removed, None)
    else:
        chat_data.get("last_rain_alert", {}).pop(removed, None)
    return removed


class SubscriptionHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    @staticmethod
    def _location_from_args(context: ContextTypes.DEFAULT_TYPE) -> str:
        return " ".join(context.args).strip()

    async def _resolve_location(self, raw: str) -> Optional[str]:
        """Geocode user input so subscriptions store one validated, canonical name."""
        try:
            loc_info = await self.deps.weather_service.qweather.get_geo_location(raw)
        except Exception as e:
            logger.error(f"Subscription geocode failed for '{raw}': {e}")
            return None
        if not loc_info:
            return None
        name = loc_info.get("name") or raw
        adm1 = loc_info.get("adm1")
        return f"{name}, {adm1}" if adm1 and adm1 != name else name

    @staticmethod
    def _find_subscribed(subs: list, location: str) -> Optional[str]:
        target = location.casefold()
        for existing in subs:
            if existing.casefold() == target:
                return existing
        return None

    async def daily_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_sub [城市] [HH:MM] - 订阅每日早安简报，可自定义推送时间"""
        if not context.args:
            await send_text(
                update,
                context,
                "usage: /daily_sub [城市名] [HH:MM]\n例如：/daily_sub 北京 07:30（时间可省略，默认 08:00）",
            )
            return

        args = list(context.args)
        brief_time = None
        if len(args) >= 2:
            parsed = parse_brief_time(args[-1])
            if parsed is not None:
                brief_time = f"{parsed[0]:02d}:{parsed[1]:02d}"
                args = args[:-1]

        raw = " ".join(args).strip()
        location = await self._resolve_location(raw)
        if not location:
            await send_text(update, context, f"❌ 找不到城市：{raw}，请检查名称。")
            return

        subs = context.chat_data.setdefault("daily_subs", [])
        matched = self._find_subscribed(subs, location)
        if matched and brief_time is None:
            await send_text(update, context, f"已订阅过 {matched} 的日报。")
            return

        if not matched:
            subs.append(location)
            matched = location
        if brief_time:
            context.chat_data.setdefault("daily_sub_times", {})[matched] = brief_time
        effective_time = context.chat_data.get("daily_sub_times", {}).get(
            matched, DEFAULT_DAILY_BRIEF_TIME
        )
        await send_text(
            update,
            context,
            f"✅ 已订阅 {matched} 的早安简报！\n每天 {effective_time} 推送（{settings.timezone}）。",
        )

    async def daily_unsub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_unsub [城市] - 取消订阅"""
        if not context.args:
            await send_text(update, context, "usage: /daily_unsub [城市名]")
            return

        location = self._location_from_args(context)
        subs = context.chat_data.get("daily_subs", [])

        matched = self._find_subscribed(subs, location)
        if not matched:
            resolved = await self._resolve_location(location)
            if resolved:
                matched = self._find_subscribed(subs, resolved)
        if matched:
            remove_subscription_entry(context.chat_data, "daily", subs.index(matched))
            await send_text(update, context, f"✅ 已取消 {matched} 的订阅。")
        else:
            await send_text(update, context, f"你没有订阅 {location}。")

    async def daily_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_my - 查看我的订阅"""
        text, keyboard = render_subscription_list(context.chat_data, "daily")
        if text is None:
            await send_text(update, context, "📭 你还没有订阅任何早安简报。")
            return
        await send_text(update, context, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def rain_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_sub [城市] - 订阅降雨提醒"""
        if not context.args:
            await send_text(update, context, "usage: /rain_sub [城市名]")
            return

        raw = self._location_from_args(context)
        location = await self._resolve_location(raw)
        if not location:
            await send_text(update, context, f"❌ 找不到城市：{raw}，请检查名称。")
            return

        subs = context.chat_data.setdefault("subs", [])
        if self._find_subscribed(subs, location):
            await send_text(update, context, f"已订阅过 {location} 的降雨提醒。")
            return

        subs.append(location)
        await send_text(update, context, f"✅ 已订阅 {location} 的降雨提醒。")

    async def rain_unsub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_unsub [城市] - 取消降雨提醒"""
        if not context.args:
            await send_text(update, context, "usage: /rain_unsub [城市名]")
            return

        location = self._location_from_args(context)
        subs = context.chat_data.get("subs", [])

        matched = self._find_subscribed(subs, location)
        if not matched:
            resolved = await self._resolve_location(location)
            if resolved:
                matched = self._find_subscribed(subs, resolved)
        if matched:
            remove_subscription_entry(context.chat_data, "rain", subs.index(matched))
            await send_text(update, context, f"✅ 已取消 {matched} 的降雨提醒。")
        else:
            await send_text(update, context, f"你没有订阅 {location} 的降雨提醒。")

    async def rain_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_my - 查看我的降雨提醒"""
        text, keyboard = render_subscription_list(context.chat_data, "rain")
        if text is None:
            await send_text(update, context, "📭 你还没有订阅任何降雨提醒。")
            return
        await send_text(update, context, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
