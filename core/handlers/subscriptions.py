from html import escape
from typing import Optional

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.config import settings
from core.handlers.common import BotDependencies
from core.handlers.messages import send_personal_text
from core.scheduler import DEFAULT_DAILY_BRIEF_TIME, parse_brief_time
from utils.schedule_times import parse_quiet_hours


def display_timezone(tz_name: Optional[str] = None) -> str:
    """Human label for the timezone a push will actually use."""
    effective = tz_name or settings.timezone
    return "北京时间" if effective == "Asia/Shanghai" else str(effective)


def rain_alert_expectation(tz_name: Optional[str] = None) -> str:
    """One-line expectation for what a rain subscription actually does."""
    quiet = parse_quiet_hours(settings.rain_alert_quiet_hours)
    text = "即将下雨时会提醒你一次；雨过之后再次降雨才会重新提醒"
    if quiet:
        (start_h, start_m), (end_h, end_m) = quiet
        text += f"（{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d} 当地时间免打扰）"
    if settings.enable_alert_push:
        exempt = "、".join(sorted(settings.alert_exempt_levels))
        text += f"；同时会推送官方灾害预警{f'（{exempt}预警不受免打扰限制）' if exempt else ''}"
    return f"{text}。管理订阅：/rain_my"


def subscription_limit_reached(subs: list) -> bool:
    return len(subs) >= settings.max_subscriptions_per_chat


def subscription_limit_message(manage_command: str) -> str:
    return (
        f"❌ 每个聊天最多订阅 {settings.max_subscriptions_per_chat} 个城市。\n"
        f"先用 {manage_command} 取消一个，再重新订阅。"
    )


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
        for key in ("daily_sub_times", "daily_sub_tz", "daily_brief_last_sent"):
            chat_data.get(key, {}).pop(removed, None)
    else:
        for key in ("last_rain_alert", "rain_episode", "alert_seen", "sub_tz"):
            chat_data.get(key, {}).pop(removed, None)
    return removed


class SubscriptionHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    @staticmethod
    def _location_from_args(context: ContextTypes.DEFAULT_TYPE) -> str:
        return " ".join(context.args).strip()

    async def _resolve_location(self, raw: str) -> Optional[str]:
        """Geocode user input so subscriptions store one validated, canonical name."""
        resolved = await self._resolve_location_info(raw)
        return resolved[0] if resolved else None

    async def _resolve_location_info(self, raw: str):
        """Return (canonical name, timezone) — the tz drives per-location scheduling."""
        try:
            loc_info = await self.deps.weather_service.qweather.get_geo_location(raw)
        except Exception as e:
            logger.error(f"Subscription geocode failed for '{raw}': {e}")
            return None
        if not loc_info:
            return None
        name = loc_info.get("name") or raw
        adm1 = loc_info.get("adm1")
        display = f"{name}, {adm1}" if adm1 and adm1 != name else name
        return display, loc_info.get("tz")

    @staticmethod
    def _remember_push_target(context: ContextTypes.DEFAULT_TYPE, update: Update, location: str, tz) -> None:
        """Record where and in which timezone pushes for this chat should go."""
        if tz:
            context.chat_data.setdefault("sub_tz", {})[location] = tz
            context.chat_data.setdefault("daily_sub_tz", {})[location] = tz
        message = update.effective_message
        thread_id = getattr(message, "message_thread_id", None) if message else None
        if thread_id:
            # Forum topics: keep pushing into the topic the user subscribed from.
            context.chat_data["push_thread_id"] = thread_id

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
            await send_personal_text(
                update,
                context,
                "用法：/daily_sub 城市 时间（时间可省略）\n"
                "例：/daily_sub 北京 07:30\n"
                "不填时间则默认每天 08:00 推送。",
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
        resolved = await self._resolve_location_info(raw)
        location, location_tz = resolved if resolved else (None, None)
        if not location:
            await send_personal_text(update, context, f"❌ 找不到城市：{raw}，请检查名称。")
            return

        subs = context.chat_data.setdefault("daily_subs", [])
        matched = self._find_subscribed(subs, location)
        if matched and brief_time is None:
            await send_personal_text(update, context, f"已订阅过 {matched} 的日报，改时间可用 /daily_sub {matched.split(',')[0]} HH:MM。")
            return

        if not matched:
            if subscription_limit_reached(subs):
                await send_personal_text(update, context, subscription_limit_message("/daily_my"))
                return
            subs.append(location)
            matched = location
        self._remember_push_target(context, update, matched, location_tz)
        if brief_time:
            context.chat_data.setdefault("daily_sub_times", {})[matched] = brief_time
        effective_time = context.chat_data.get("daily_sub_times", {}).get(
            matched, DEFAULT_DAILY_BRIEF_TIME
        )
        await send_personal_text(
            update,
            context,
            f"✅ 已订阅 {matched} 的早安简报！\n"
            f"每天 {effective_time}（{display_timezone(location_tz)}）推送。\n"
            f"改时间：/daily_sub 城市 HH:MM，管理订阅：/daily_my",
        )

    async def daily_unsub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_unsub [城市] - 取消订阅"""
        if not context.args:
            await send_personal_text(update, context, "用法：/daily_unsub 城市\n也可以在 /daily_my 里点按钮取消。")
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
            await send_personal_text(update, context, f"✅ 已取消 {matched} 的订阅。")
        else:
            await send_personal_text(update, context, f"你没有订阅 {location}。")

    async def daily_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_my - 查看我的订阅"""
        text, keyboard = render_subscription_list(context.chat_data, "daily")
        if text is None:
            await send_personal_text(update, context, "📭 你还没有订阅任何早安简报。")
            return
        await send_personal_text(update, context, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    async def rain_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_sub [城市] - 订阅降雨提醒"""
        if not context.args:
            await send_personal_text(update, context, "用法：/rain_sub 城市\n例：/rain_sub 北京")
            return

        raw = self._location_from_args(context)
        resolved = await self._resolve_location_info(raw)
        location, location_tz = resolved if resolved else (None, None)
        if not location:
            await send_personal_text(update, context, f"❌ 找不到城市：{raw}，请检查名称。")
            return

        subs = context.chat_data.setdefault("subs", [])
        if self._find_subscribed(subs, location):
            await send_personal_text(update, context, f"已订阅过 {location} 的降雨提醒。管理订阅：/rain_my")
            return
        if subscription_limit_reached(subs):
            await send_personal_text(update, context, subscription_limit_message("/rain_my"))
            return

        subs.append(location)
        self._remember_push_target(context, update, location, location_tz)
        await send_personal_text(
            update, context, f"✅ 已订阅 {location} 的降雨提醒。\n{rain_alert_expectation(location_tz)}"
        )

    async def rain_unsub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_unsub [城市] - 取消降雨提醒"""
        if not context.args:
            await send_personal_text(update, context, "用法：/rain_unsub 城市\n也可以在 /rain_my 里点按钮取消。")
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
            await send_personal_text(update, context, f"✅ 已取消 {matched} 的降雨提醒。")
        else:
            await send_personal_text(update, context, f"你没有订阅 {location} 的降雨提醒。")

    async def rain_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_my - 查看我的降雨提醒"""
        text, keyboard = render_subscription_list(context.chat_data, "rain")
        if text is None:
            await send_personal_text(update, context, "📭 你还没有订阅任何降雨提醒。")
            return
        await send_personal_text(update, context, text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
