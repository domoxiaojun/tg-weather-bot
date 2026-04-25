from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.handlers.common import BotDependencies
from core.handlers.messages import send_text


class SubscriptionHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    async def daily_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_sub [城市] - 订阅每日早安简报"""
        if not context.args:
            await send_text(update, context, "usage: /daily_sub [城市名]")
            return

        location = context.args[0]
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

        location = context.args[0]
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

        msg = "📅 **我的早安订阅**：\n"
        for location in subs:
            msg += f"• {location}\n"
        await send_text(update, context, msg, parse_mode=ParseMode.MARKDOWN)
