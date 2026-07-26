from html import escape
from typing import Optional

from loguru import logger
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.handlers.common import BotDependencies
from core.handlers.messages import send_text


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
        """/daily_sub [城市] - 订阅每日早安简报"""
        if not context.args:
            await send_text(update, context, "usage: /daily_sub [城市名]")
            return

        raw = self._location_from_args(context)
        location = await self._resolve_location(raw)
        if not location:
            await send_text(update, context, f"❌ 找不到城市：{raw}，请检查名称。")
            return

        subs = context.chat_data.setdefault("daily_subs", [])
        if self._find_subscribed(subs, location):
            await send_text(update, context, f"已订阅过 {location} 的日报。")
        else:
            subs.append(location)
            await send_text(update, context, f"✅ 成功订阅 {location} 的早安简报！\n每天早晨 8:00 推送。")

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
            subs.remove(matched)
            await send_text(update, context, f"✅ 已取消 {matched} 的订阅。")
        else:
            await send_text(update, context, f"你没有订阅 {location}。")

    async def daily_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_my - 查看我的订阅"""
        subs = context.chat_data.get("daily_subs", [])
        if not subs:
            await send_text(update, context, "📭 你还没有订阅任何早安简报。")
            return

        msg = "📅 <b>我的早安订阅</b>：\n"
        for location in subs:
            msg += f"• {escape(location)}\n"
        await send_text(update, context, msg, parse_mode=ParseMode.HTML)

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
            subs.remove(matched)
            await send_text(update, context, f"✅ 已取消 {matched} 的降雨提醒。")
        else:
            await send_text(update, context, f"你没有订阅 {location} 的降雨提醒。")

    async def rain_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_my - 查看我的降雨提醒"""
        subs = context.chat_data.get("subs", [])
        if not subs:
            await send_text(update, context, "📭 你还没有订阅任何降雨提醒。")
            return

        msg = "🔔 <b>我的降雨提醒</b>：\n"
        for location in subs:
            msg += f"• {escape(location)}\n"
        await send_text(update, context, msg, parse_mode=ParseMode.HTML)
