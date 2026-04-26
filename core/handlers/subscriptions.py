from html import escape

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

    async def daily_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_sub [城市] - 订阅每日早安简报"""
        if not context.args:
            await send_text(update, context, "usage: /daily_sub [城市名]")
            return

        location = self._location_from_args(context)
        subs = context.chat_data.setdefault("daily_subs", [])

        if location in subs:
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

        if location in subs:
            subs.remove(location)
            await send_text(update, context, f"✅ 已取消 {location} 的订阅。")
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

        location = self._location_from_args(context)
        subs = context.chat_data.setdefault("subs", [])

        if location in subs:
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

        if location in subs:
            subs.remove(location)
            await send_text(update, context, f"✅ 已取消 {location} 的降雨提醒。")
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
