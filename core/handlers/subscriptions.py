from datetime import datetime
from html import escape
from typing import Optional

from loguru import logger
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from telegram.constants import ChatType

from core.config import settings
from core.handlers.common import BotDependencies
from core.handlers.messages import send_personal_text
from services.telegram_rich import FEATURE_SEND, bold, bullet_list, heading, paragraph, rich
from services.telegram_rich import footer as footer_block
from core.scheduler import (
    DEFAULT_DAILY_BRIEF_TIME,
    DEFAULT_RAIN_LEVEL,
    RAIN_LEVELS,
    parse_brief_time,
    parse_rain_level,
    rain_level_hint,
    rain_level_label,
    zone_for,
)
from utils.formatter import callback_location_token, styled_button
from utils.schedule_times import is_within_quiet_hours, parse_quiet_hours


def display_timezone(tz_name: Optional[str] = None) -> str:
    """Human label for the timezone a push will actually use."""
    effective = tz_name or settings.timezone
    return "北京时间" if effective == "Asia/Shanghai" else str(effective)


def rain_alert_expectation(tz_name: Optional[str] = None, include_manage: bool = True) -> str:
    """One-line expectation for what a rain subscription actually does.

    ``include_manage=False`` drops the trailing command hint — used when the
    text sits on a card that already carries the management buttons.
    """
    quiet = parse_quiet_hours(settings.rain_alert_quiet_hours)
    text = "即将下雨时会提醒你一次；雨过之后再次降雨才会重新提醒"
    if quiet:
        (start_h, start_m), (end_h, end_m) = quiet
        text += f"（{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d} 当地时间免打扰）"
    if settings.enable_alert_push:
        exempt = "、".join(sorted(settings.alert_exempt_levels))
        text += f"；同时会推送官方灾害预警{f'（{exempt}预警不受免打扰限制）' if exempt else ''}"
    return f"{text}。管理订阅：/rain_my" if include_manage else f"{text}。"


def subscription_limit_reached(subs: list) -> bool:
    return len(subs) >= settings.max_subscriptions_per_chat


def subscription_limit_message(manage_command: str) -> str:
    return (
        f"❌ 每个聊天最多订阅 {settings.max_subscriptions_per_chat} 个城市。\n"
        f"先取消一个，再重新订阅。"
    )


def subscription_limit_keyboard(kind: str) -> InlineKeyboardMarkup:
    """Limit refusals still hand over the management card in one tap."""
    return InlineKeyboardMarkup([[
        styled_button("🗂 管理订阅", callback_data=f"submy|{kind}")
    ]])


DEFAULT_DAILY_BRIEF_TIME_HINT = f"默认每天 {DEFAULT_DAILY_BRIEF_TIME} 推送，点下方时间按钮可改。"

# Quick-pick times for the daily-brief card. Anything else still works via
# /daily_sub 城市 HH:MM — the buttons cover the common cases, not all of them.
DAILY_TIME_PRESETS = ("06:30", "07:00", "07:30", "08:00")

_CARD_TITLES = {"daily": "📅 我的早安订阅", "rain": "🔔 我的降雨提醒"}
_CARD_HINTS = {
    "daily": "点时间即改推送时间；其他时间：/daily_sub 城市 HH:MM",
    "rain": " ｜ ".join(f"{label}＝{hint}" for label, _r, _p, hint in RAIN_LEVELS.values()),
}


def _subscription_rows(chat_data: dict, kind: str):
    """Per-city (location, plain-text detail) pairs shared by HTML and rich views."""
    list_key = "daily_subs" if kind == "daily" else "subs"
    zones = chat_data.get("sub_tz" if kind == "rain" else "daily_sub_tz", {})
    rows = []
    for location in chat_data.get(list_key, []):
        if kind == "daily":
            brief_time = chat_data.get("daily_sub_times", {}).get(location, DEFAULT_DAILY_BRIEF_TIME)
            detail = f"每天 {brief_time}（{display_timezone(zones.get(location))}）"
        else:
            level = chat_data.get("rain_level", {}).get(location, DEFAULT_RAIN_LEVEL)
            parts = [rain_level_label(level)]
            last = chat_data.get("last_rain_alert", {}).get(location)
            parts.append(f"上次提醒 {last.strftime('%m-%d %H:%M')}" if last else "尚未提醒过")
            if _in_quiet_hours(zones.get(location)):
                parts.append("当前免打扰中")
            detail = " · ".join(parts)
        rows.append((location, detail))
    return rows


def _subscription_keyboard_rows(chat_data: dict, kind: str) -> list:
    """Per-city control rows. Index-based callback data keeps every button
    inside Telegram's 64-byte budget no matter how long the place name is."""
    list_key = "daily_subs" if kind == "daily" else "subs"
    keyboard_rows = []
    for index, location in enumerate(chat_data.get(list_key, [])):
        if kind == "daily":
            current = chat_data.get("daily_sub_times", {}).get(location, DEFAULT_DAILY_BRIEF_TIME)
            keyboard_rows.append([
                styled_button(
                    ("✅ " if preset == current else "") + preset,
                    style="success" if preset == current else None,
                    callback_data=f"dtime|{index}|{preset}",
                )
                for preset in DAILY_TIME_PRESETS
            ])
        else:
            current = chat_data.get("rain_level", {}).get(location, DEFAULT_RAIN_LEVEL)
            keyboard_rows.append([
                styled_button(
                    ("✅ " if current == key else "") + label,
                    style="success" if current == key else None,
                    callback_data=f"lvl|{index}|{key}",
                )
                for key, (label, _rate, _pop, _hint) in RAIN_LEVELS.items()
            ])
        keyboard_rows.append([
            styled_button(f"❌ 取消 {location}", style="danger", callback_data=f"unsub|{kind}|{index}")
        ])
    keyboard_rows.append(_card_footer_row(chat_data, kind))
    return keyboard_rows


def _card_footer_row(chat_data: dict, kind: str) -> list:
    """Flip to the sibling card, plus one-tap subscribe for the last queried city."""
    other = "daily" if kind == "rain" else "rain"
    row = [
        styled_button(
            "📅 早安订阅" if other == "daily" else "🔔 降雨订阅",
            callback_data=f"subview|{other}",
        )
    ]
    add = _add_city_button(chat_data, kind)
    if add is not None:
        row.append(add)
    return row


def _add_city_button(chat_data: dict, kind: str):
    """➕ button for the city the user queried last, if it is not covered yet."""
    last = chat_data.get("last_location") or {}
    name = last.get("name")
    if not name:
        return None
    list_key = "daily_subs" if kind == "daily" else "subs"
    subs = chat_data.get(list_key, [])
    if subscription_limit_reached(subs):
        return None
    target = name.casefold()
    for existing in subs:
        head = existing.casefold()
        if head == target or head.split(",")[0].strip() == target:
            return None
    token = callback_location_token(name, last.get("coords"))
    action = "dsub" if kind == "daily" else "sub"
    return styled_button(f"➕ 订阅{name}", callback_data=f"{action}|{token}")


def render_subscription_list(chat_data: dict, kind: str):
    """Build (HTML text, keyboard) for a subscription card.

    kind: 'daily' or 'rain'. Returns (None, None) when the list is empty.
    """
    rows = _subscription_rows(chat_data, kind)
    if not rows:
        return None, None

    icon, _, title = _CARD_TITLES[kind].partition(" ")
    lines = [f"{icon} <b>{title}</b>："]
    for location, detail in rows:
        lines.append(f"• <b>{escape(location)}</b>\n  {escape(detail)}")
    lines.append(f"\n{escape(_CARD_HINTS[kind])}")
    return "\n".join(lines), InlineKeyboardMarkup(_subscription_keyboard_rows(chat_data, kind))


def build_subscription_blocks(chat_data: dict, kind: str, prefix: Optional[str] = None) -> list:
    """Rich-card version of the same list; keyboard comes from the HTML path."""
    rows = _subscription_rows(chat_data, kind)
    blocks = []
    if prefix:
        blocks.append(paragraph(prefix))
    blocks.append(heading(_CARD_TITLES[kind], size=4))
    blocks.append(bullet_list([
        [paragraph([bold(location)]), paragraph(detail)] for location, detail in rows
    ]))
    blocks.append(footer_block(_CARD_HINTS[kind]))
    return blocks


def render_empty_card(chat_data: dict, kind: str):
    """Empty state that still offers a way forward instead of a dead end."""
    button = "📅 早安简报" if kind == "daily" else "🔔 降雨提醒"
    what = "早安简报" if kind == "daily" else "降雨提醒"
    text = (
        f"📭 还没有{what}订阅。\n"
        f"先查一次天气（直接发城市名，如「北京」），然后点卡片上的「{button}」即可。"
    )
    rows = []
    add = _add_city_button(chat_data, kind)
    if add is not None:
        rows.append([add])
    rows.append([
        *_card_footer_row(chat_data, kind)[:1],  # switch button only
        styled_button("📖 怎么订阅", callback_data="help|push"),
    ])
    return text, InlineKeyboardMarkup(rows)


async def send_subscription_card(update, context, kind: str, *, prefix: Optional[str] = None):
    """Send the subscription card: rich blocks in private chats, HTML fallback.

    Group chats keep the ephemeral text path so personal bookkeeping does not
    spam everyone. ``prefix`` is plain text (confirmation line above the card).
    """
    text, keyboard = render_subscription_list(context.chat_data, kind)
    if text is None:
        empty_text, empty_keyboard = render_empty_card(context.chat_data, kind)
        combined = f"{prefix}\n\n{empty_text}" if prefix else empty_text
        return await send_personal_text(update, context, combined, reply_markup=empty_keyboard)

    chat = update.effective_chat
    if chat is not None and chat.type == ChatType.PRIVATE and rich.supports(FEATURE_SEND):
        blocks = build_subscription_blocks(context.chat_data, kind, prefix=prefix)
        sent = await rich.send_rich(context.bot, chat.id, blocks=blocks, reply_markup=keyboard)
        if sent is not None:
            return sent

    combined = f"{escape(prefix)}\n\n{text}" if prefix else text
    return await send_personal_text(
        update, context, combined, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


def _in_quiet_hours(tz_name) -> bool:
    """Whether this subscription's own timezone is inside the quiet window now.

    Shown in the list so a quiet night is not read as a broken bot.
    """
    window = parse_quiet_hours(settings.rain_alert_quiet_hours)
    if window is None:
        return False
    return is_within_quiet_hours(datetime.now(zone_for(tz_name)).time(), window)


def set_rain_level(chat_data: dict, index: int, level: str):
    """Change one rain subscription's sensitivity; returns the location or None."""
    subs = chat_data.get("subs", [])
    if not (0 <= index < len(subs)) or level not in RAIN_LEVELS:
        return None
    location = subs[index]
    chat_data.setdefault("rain_level", {})[location] = level
    return location


def set_daily_time(chat_data: dict, index: int, value: str):
    """Change one daily subscription's push time; returns the location or None."""
    subs = chat_data.get("daily_subs", [])
    parsed = parse_brief_time(value)
    if not (0 <= index < len(subs)) or parsed is None:
        return None
    location = subs[index]
    chat_data.setdefault("daily_sub_times", {})[location] = f"{parsed[0]:02d}:{parsed[1]:02d}"
    return location


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
        for key in ("last_rain_alert", "rain_episode", "alert_seen", "sub_tz", "rain_level"):
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
            await send_subscription_card(
                update,
                context,
                "daily",
                prefix="订阅新城市：/daily_sub 城市 [HH:MM]（如 /daily_sub 北京 07:30）",
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
            await send_subscription_card(
                update, context, "daily", prefix=f"ℹ️ 已订阅过 {matched}，可直接在下方改时间。"
            )
            return

        if not matched:
            if subscription_limit_reached(subs):
                await send_personal_text(
                    update,
                    context,
                    subscription_limit_message("/daily_my"),
                    reply_markup=subscription_limit_keyboard("daily"),
                )
                return
            subs.append(location)
            matched = location
        self._remember_push_target(context, update, matched, location_tz)
        if brief_time:
            context.chat_data.setdefault("daily_sub_times", {})[matched] = brief_time
        effective_time = context.chat_data.get("daily_sub_times", {}).get(
            matched, DEFAULT_DAILY_BRIEF_TIME
        )
        await send_subscription_card(
            update,
            context,
            "daily",
            prefix=(
                f"✅ 已订阅 {matched} 的早安简报，"
                f"每天 {effective_time}（{display_timezone(location_tz)}）推送。"
            ),
        )

    async def daily_unsub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_unsub [城市] - 取消订阅"""
        if not context.args:
            await send_subscription_card(
                update, context, "daily", prefix="要取消哪个？点下面的 ❌ 即可。"
            )
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
            await send_subscription_card(update, context, "daily", prefix=f"✅ 已取消 {matched} 的订阅。")
        else:
            await send_subscription_card(
                update, context, "daily", prefix=f"你没有订阅 {location}，当前订阅如下："
            )

    async def daily_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_my - 查看我的订阅"""
        await send_subscription_card(update, context, "daily")

    async def rain_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_sub [城市] - 订阅降雨提醒"""
        if not context.args:
            await send_subscription_card(
                update,
                context,
                "rain",
                prefix="订阅新城市：/rain_sub 城市 [档位]（如 /rain_sub 北京 仅大雨）",
            )
            return

        args = list(context.args)
        # A trailing level word is optional: /rain_sub 北京 仅大雨
        level = parse_rain_level(args[-1]) if len(args) >= 2 else None
        if level:
            args = args[:-1]
        raw = " ".join(args).strip()
        resolved = await self._resolve_location_info(raw)
        location, location_tz = resolved if resolved else (None, None)
        if not location:
            await send_personal_text(update, context, f"❌ 找不到城市：{raw}，请检查名称。")
            return

        subs = context.chat_data.setdefault("subs", [])
        existing = self._find_subscribed(subs, location)
        if existing and level:
            # Re-subscribing with a level is how you change it from the command.
            context.chat_data.setdefault("rain_level", {})[existing] = level
            await send_subscription_card(
                update,
                context,
                "rain",
                prefix=f"✅ 已把 {existing} 的提醒档位改为「{rain_level_label(level)}」（{rain_level_hint(level)}）。",
            )
            return
        if existing:
            await send_subscription_card(
                update, context, "rain", prefix=f"ℹ️ 已订阅过 {existing}，可直接在下方管理。"
            )
            return
        if subscription_limit_reached(subs):
            await send_personal_text(
                update,
                context,
                subscription_limit_message("/rain_my"),
                reply_markup=subscription_limit_keyboard("rain"),
            )
            return

        subs.append(location)
        self._remember_push_target(context, update, location, location_tz)
        effective = level or DEFAULT_RAIN_LEVEL
        if level:
            context.chat_data.setdefault("rain_level", {})[location] = level
        await send_subscription_card(
            update,
            context,
            "rain",
            prefix=(
                f"✅ 已订阅 {location} 的降雨提醒（{rain_level_label(effective)}——{rain_level_hint(effective)}）。\n"
                f"{rain_alert_expectation(location_tz, include_manage=False)}"
            ),
        )

    async def rain_unsub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_unsub [城市] - 取消降雨提醒"""
        if not context.args:
            await send_subscription_card(
                update, context, "rain", prefix="要取消哪个？点下面的 ❌ 即可。"
            )
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
            await send_subscription_card(update, context, "rain", prefix=f"✅ 已取消 {matched} 的降雨提醒。")
        else:
            await send_subscription_card(
                update, context, "rain", prefix=f"你没有订阅 {location}，当前订阅如下："
            )

    async def rain_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/rain_my - 查看我的降雨提醒"""
        await send_subscription_card(update, context, "rain")
