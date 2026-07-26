import io
from html import escape

from loguru import logger
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ChatType, ParseMode
from telegram.ext import ContextTypes

from core.config import settings
from core.handlers.common import (
    BotDependencies,
    fire_and_forget,
    join_location_args,
    parse_chart_request,
    parse_location_and_view,
)
from core.handlers.messages import send_photo, send_text, send_weather_view
from services.chart_cache import (
    CHART_PROFILES,
    get_cached_chart_file_id,
    get_chart_caption,
    normalize_chart_type,
    remember_chart_file_id,
    render_chart_bytes_async,
    run_chart_render,
)
from services.visualizer import Visualizer
from core.handlers.guide import GUIDE_DEFAULT_PAGE
from utils.formatter import (
    callback_location_token,
    format_weather_response,
    get_weather_keyboard,
    styled_button,
)


def _looks_like_coords(text: str) -> bool:
    parts = text.split(",")
    if len(parts) != 2:
        return False
    try:
        float(parts[0])
        float(parts[1])
    except ValueError:
        return False
    return True


def should_attach_rain_chart(data, view_type: str) -> bool:
    """Rain chart rides along whenever it is actually rainy.

    is_raining is measurement-based (now_precip / minutely); a "小雨" sky with
    a dry next hour must still ship the chart, so the text is checked too.
    """
    if not settings.enable_weather_plots:
        return False
    if view_type == "rain":
        return True
    return bool(
        data.is_raining or any(marker in (data.now_text or "") for marker in ("雨", "雪"))
    )


class WeatherHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/start - 三行欢迎 + 快速开始按钮卡，不再是命令墙。"""
        chat = update.effective_chat
        last = (context.chat_data or {}).get("last_location") or {}

        # Quick-start card: the things a new user actually does next.
        if last.get("name"):
            token = callback_location_token(last["name"], last.get("coords"))
            subscribe_row = [
                styled_button(
                    f"🔔 订阅{last['name']}降雨", style="success", callback_data=f"sub|{token}"
                ),
                styled_button(f"📅 订阅{last['name']}简报", callback_data=f"dsub|{token}"),
            ]
        else:
            subscribe_row = [
                styled_button("🔔 降雨提醒", style="success", callback_data="submy|rain"),
                styled_button("📅 早安简报", callback_data="submy|daily"),
            ]
        quick_keyboard = InlineKeyboardMarkup([
            subscribe_row,
            [styled_button("📖 使用指南", callback_data=f"help|{GUIDE_DEFAULT_PAGE}")],
        ])

        if chat is not None and chat.type == ChatType.PRIVATE:
            # Message 1: how to query. (A reply keyboard cannot share a message
            # with inline buttons, hence the two-message split.)
            await send_text(
                update,
                context,
                "👋 你好，我是 <b>DomoWeather</b>。\n\n"
                "查天气：<b>直接发城市名</b>就行，比如 <code>北京</code> 或 <code>北京明天</code>；\n"
                "也可以点下面的按钮发送位置📍",
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📍 发送我的位置", request_location=True)]],
                    resize_keyboard=True,
                    one_time_keyboard=True,
                    input_field_placeholder="直接发城市名，如：北京 明天",
                ),
            )
            await send_text(
                update,
                context,
                "下雨提前叫我、每天早上一份 AI 简报——点这里开启：",
                reply_markup=quick_keyboard,
            )
            return

        await send_text(
            update,
            context,
            "👋 大家好，我是 <b>DomoWeather</b>。\n"
            "查天气：<code>/tq 城市名</code>；订阅降雨提醒/早安简报点下面的按钮（群共享）。",
            parse_mode=ParseMode.HTML,
            reply_markup=quick_keyboard,
        )

    async def handle_unknown_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """私聊里打错的命令给出一条活路，而不是已读不回。"""
        message = update.effective_message
        text = (message.text or "").strip() if message else ""
        # /tq北京 —— 命令和参数黏在一起的常见手误
        if text.startswith("/tq") and len(text) > 3 and not text[3].isspace():
            context.args = text[3:].split()
            await self.handle_weather_request(update, context)
            return
        await send_text(
            update,
            context,
            "🤔 不认识这个命令。查天气可以<b>直接发城市名</b>（如 北京）；全部玩法见 /help",
            parse_mode=ParseMode.HTML,
        )

    async def chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/chart [城市] [daily|hourly|rain] -> 发送趋势图"""
        args = context.args
        if not args:
            # Same convention as bare /tq: fall back to the last queried city.
            last = context.chat_data.get("last_location") if context.chat_data else None
            if last and last.get("coords"):
                args = [last["coords"]]
            else:
                await send_text(
                    update,
                    context,
                    "⚠️ 用法：/chart 城市 [daily|hourly|rain]\n\n"
                    "示例：\n"
                    "• <code>/chart 北京</code> - 逐小时温度图\n"
                    "• <code>/chart 上海 daily</code> - 逐日温度图\n"
                    "• <code>/chart 广州 rain</code> - 逐小时降水概率",
                    parse_mode=ParseMode.HTML,
                )
                return

        location, requested_chart_type = parse_chart_request(list(args))
        chart_type = normalize_chart_type(requested_chart_type)
        if update.effective_chat is not None:
            # Cold path takes seconds (geo + fetch + render + upload) with zero
            # feedback otherwise; every other command already shows an action.
            fire_and_forget(
                context,
                context.bot.send_chat_action(
                    chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO
                ),
            )

        profile = CHART_PROFILES[chart_type]

        try:
            data = await self.deps.weather_service.get_fused_weather(location, profile=profile)
        except Exception as e:
            logger.error(f"获取天气数据失败: {e}")
            await send_text(update, context, "❌ 系统繁忙，请稍后再试")
            return

        if not data:
            await send_text(update, context, "⚠️ 未获取到天气数据，请检查城市名称")
            return

        caption = get_chart_caption(data, chart_type)
        file_id = await get_cached_chart_file_id(data, chart_type)
        if file_id:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=file_id,
                caption=caption,
                reply_markup=get_weather_keyboard(location, mode="chart", coords=data.coords),
            )
            return

        img_bytes = await render_chart_bytes_async(data, chart_type)
        if not img_bytes:
            await send_text(
                update,
                context,
                f"⚠️ 暂无{caption.split(' ', 1)[-1]}数据。试试其他图表：",
                reply_markup=get_weather_keyboard(location, mode="chart", coords=data.coords),
            )
            return

        sent = await send_photo(
            update,
            context,
            photo=InputFile(io.BytesIO(img_bytes), filename=f"{location}_{chart_type}.png"),
            caption=caption,
            reply_markup=get_weather_keyboard(location, mode="chart", coords=data.coords),
        )
        if sent is not None:
            await remember_chart_file_id(data, chart_type, sent)

    async def handle_weather_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /tq and location weather requests."""
        message = update.effective_message
        if message is None:
            return

        location_query = None
        view_type = "default"
        start_day = 0
        limit = None

        if message.location:
            location_query = f"{message.location.longitude},{message.location.latitude}"
        elif context.args:
            location_query, view_type, start_day, limit = parse_location_and_view(list(context.args))
        else:
            # 无参数时回落到上次查询的城市，新用户才给用法提示。
            last = context.chat_data.get("last_location") if context.chat_data else None
            if isinstance(last, dict) and last.get("coords"):
                location_query = last["coords"]
            else:
                location_keyboard = None
                chat = update.effective_chat
                if chat is not None and chat.type == ChatType.PRIVATE:
                    location_keyboard = ReplyKeyboardMarkup(
                        [[KeyboardButton("📍 发送我的位置", request_location=True)]],
                        resize_keyboard=True,
                        one_time_keyboard=True,
                        input_field_placeholder="直接发城市名，如：北京",
                    )
                await send_text(
                    update,
                    context,
                    "想查哪里？直接发城市名（如 北京），或点按钮发送位置📍",
                    reply_markup=location_keyboard,
                )
                return

        if not location_query:
            await send_text(update, context, "请提供城市名称或定位，例如：/tq 北京（也可以直接发送城市名）")
            return

        # Feedback rides in the background — it must not delay the fetch.
        fire_and_forget(context, message.set_reaction("👀"))

        # 同名城市（如"朝阳"）静默取第一个匹配容易查错地方；
        # 有多个精确同名候选时让用户点选。
        if message.location is None and not _looks_like_coords(location_query):
            if await self._offer_geo_choices(update, context, location_query, view_type, start_day, limit):
                return

        await self._send_weather(update, context, location_query, view_type, start_day, limit)

    async def typhoon(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/typhoon [城市] - 当前活跃台风，并判断对该地点的影响"""
        from services.telegram_rich import FEATURE_SEND, rich
        from services.typhoon import assess_storms, format_threat_summary
        from utils.rich_formatter import build_typhoon_push_blocks

        if not settings.enable_typhoon_alerts:
            await send_text(update, context, "⚠️ 台风功能已关闭（ENABLE_TYPHOON_ALERTS=false）。")
            return

        fire_and_forget(
            context,
            context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING),
        )
        try:
            storms = await self.deps.weather_service.qweather.get_active_storms(settings.typhoon_basin)
        except Exception as e:
            logger.error(f"Typhoon lookup failed: {e}")
            await send_text(update, context, "❌ 台风数据获取失败，请稍后再试。")
            return

        if not storms:
            await send_text(
                update,
                context,
                f"🌀 当前 {settings.typhoon_basin} 洋盆没有活跃台风。\n"
                "（和风天气目前仅提供西北太平洋 NP 洋盆数据）",
            )
            return

        # With a location we can say whether it actually reaches the user.
        location = join_location_args(list(context.args)) if context.args else None
        if not location and context.chat_data:
            last = context.chat_data.get("last_location")
            if isinstance(last, dict):
                location = last.get("coords")

        threats = []
        location_name = ""
        if location:
            try:
                data = await self.deps.weather_service.get_fused_weather(location, profile="rain")
            except Exception as e:
                logger.debug(f"Typhoon location lookup failed: {e}")
                data = None
            if data is not None:
                location_name = data.location_name
                coords = self.deps.weather_service.qweather._parse_coords(data.coords)
                if coords is not None:
                    threats = assess_storms(storms, coords[0], coords[1])

        if threats:
            for threat in threats[:2]:
                blocks = build_typhoon_push_blocks(threat, location_name)
                if rich.supports(FEATURE_SEND):
                    sent = await rich.send_rich(
                        context.bot, update.effective_chat.id, blocks=blocks
                    )
                    if sent is not None:
                        continue
                await send_text(
                    update,
                    context,
                    f"🌀 <b>{escape(format_threat_summary(threat))}</b>\n"
                    f"{escape(location_name)} · 距中心约 {threat.distance_km:.0f}km",
                    parse_mode=ParseMode.HTML,
                )
            return

        lines = ["🌀 <b>当前活跃台风</b>"]
        for storm in storms:
            bits = [f"• <b>{escape(storm.display_name)}</b>"]
            if storm.now is not None:
                from services.typhoon import storm_type_label

                bits.append(storm_type_label(storm.now.type))
                bits.append(f"{storm.now.lat:.1f}°N {storm.now.lon:.1f}°E")
            lines.append(" · ".join(bits))
        if location_name:
            lines.append(
                f"\n对 {escape(location_name)} 暂无明显影响。"
                f"订阅了降雨提醒的城市，台风逼近时会自动推送，无需手动查。"
            )
        else:
            lines.append("\n发送 /typhoon 城市 可判断对该地点的影响。")
        await send_text(update, context, "\n".join(lines), parse_mode=ParseMode.HTML)

    async def tide(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/tide [城市] - 最近潮汐站的当日高低潮"""
        from services.chart_cache import run_chart_render
        from services.telegram_rich import FEATURE_SEND, photo_block, rich
        from utils.rich_formatter import build_tide_blocks

        if not settings.enable_tide:
            await send_text(update, context, "⚠️ 潮汐功能已关闭（ENABLE_TIDE=false）。")
            return

        location = join_location_args(list(context.args)) if context.args else None
        if not location and context.chat_data:
            last = context.chat_data.get("last_location")
            if isinstance(last, dict):
                location = last.get("coords")
        if not location:
            await send_text(update, context, "请提供城市或位置，例如：/tide 青岛")
            return

        fire_and_forget(
            context,
            context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING),
        )
        qweather = self.deps.weather_service.qweather
        try:
            loc_info = await qweather.get_geo_location(location)
            if not loc_info:
                await send_text(update, context, f"❌ 找不到地点：{location}")
                return
            lon = float(loc_info["lon"])
            lat = float(loc_info["lat"])
            stations = await qweather.get_tide_stations(lon, lat)
        except Exception as e:
            logger.error(f"Tide station lookup failed: {e}")
            await send_text(update, context, "❌ 潮汐站查询失败，请稍后再试。")
            return

        if not stations:
            name = loc_info.get("name", location)
            token = callback_location_token(name, f"{lon},{lat}")
            await send_text(
                update,
                context,
                f"🌊 {name} 附近没有可用的潮汐站（数据只覆盖沿海主要港口）。",
                reply_markup=InlineKeyboardMarkup([[
                    styled_button("🌤 查看该地天气", callback_data=f"tq|{token}|default|0|0")
                ]]),
            )
            return

        forecast = None
        for station in stations:
            try:
                forecast = await qweather.get_tide(station)
            except Exception as e:
                logger.debug(f"Tide fetch failed for {station.id}: {e}")
                continue
            if forecast is not None:
                break

        if forecast is None:
            await send_text(update, context, "🌊 最近的潮汐站暂无当日数据，请稍后再试。")
            return

        blocks = build_tide_blocks(forecast)
        chart = None
        if settings.enable_weather_plots:
            chart = await run_chart_render(Visualizer.draw_tide_chart, forecast)

        if rich.supports(FEATURE_SEND):
            if chart:
                # Upload once so the chart can ride inside the rich message.
                sent_photo = await context.bot.send_photo(
                    chat_id=settings.super_admin_id or update.effective_chat.id,
                    photo=InputFile(io.BytesIO(chart), filename="tide.png"),
                    disable_notification=True,
                )
                photos = getattr(sent_photo, "photo", None)
                if photos and settings.super_admin_id:
                    try:
                        await sent_photo.delete()
                    except Exception:
                        pass
                    blocks.insert(2, photo_block(photos[-1].file_id, "潮位曲线"))
                    chart = None
            if await rich.send_rich(context.bot, update.effective_chat.id, blocks=blocks) is not None:
                return

        lines = [f"🌊 <b>{escape(forecast.station.name)} 潮汐</b> · {forecast.date.strftime('%m-%d')}"]
        for item in forecast.extremes:
            label = "🔺 高潮" if item.is_high else "🔻 低潮"
            lines.append(f"{item.time.strftime('%H:%M')} {label} {item.height:.2f} m")
        if not forecast.extremes:
            lines.append("该站当日没有高低潮数据")
        await send_text(update, context, "\n".join(lines), parse_mode=ParseMode.HTML)
        if chart:
            await send_photo(
                update,
                context,
                photo=InputFile(io.BytesIO(chart), filename="tide.png"),
                caption=f"🌊 {forecast.station.name} 潮位曲线",
            )

    async def handle_private_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """私聊里直接发城市名即可查天气（支持"北京 明天"等参数写法）。"""
        message = update.effective_message
        if message is None or not message.text:
            return
        text = message.text.strip()
        # 太长/多行的内容基本不是城市查询，安静忽略避免误伤闲聊。
        if not text or len(text) > 20 or "\n" in text:
            return
        context.args = text.split()
        await self.handle_weather_request(update, context)

    async def _offer_geo_choices(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        location_query: str,
        view_type: str,
        start_day: int,
        limit,
    ) -> bool:
        try:
            candidates = await self.deps.weather_service.qweather.get_geo_candidates(location_query)
        except Exception as e:
            logger.debug(f"Geo candidates lookup failed: {e}")
            return False

        query_text = location_query.strip()
        exact = [c for c in candidates if (c.get("name") or "") == query_text]
        if len(exact) < 2:
            return False

        buttons = []
        for candidate in exact[:4]:
            name = candidate.get("name") or query_text
            adm2 = candidate.get("adm2") or ""
            adm1 = candidate.get("adm1") or ""
            region = "·".join(part for part in (adm2, adm1) if part and part != name)
            label = f"{name}（{region}）" if region else name
            try:
                coords = f"{float(candidate['lon']):.2f},{float(candidate['lat']):.2f}"
            except (KeyError, TypeError, ValueError):
                continue
            buttons.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"tq|{coords}|{view_type}|{start_day}|{limit or 0}",
                    )
                ]
            )
        if len(buttons) < 2:
            return False

        await send_text(
            update,
            context,
            f"🔎 找到多个「{query_text}」，请选择：",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return True

    async def _send_weather(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        location_query: str,
        view_type: str = "default",
        start_day: int = 0,
        limit=None,
    ):
        fire_and_forget(
            context,
            context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING),
        )

        try:
            profile = view_type if view_type in {"hourly", "daily", "rain", "indices"} else "full"
            data = await self.deps.weather_service.get_fused_weather(
                location_query,
                profile=profile,
            )
        except Exception as e:
            logger.error(f"Error fetching weather: {e}")
            await send_text(update, context, "❌ 系统繁忙，请稍后再试。")
            return

        if not data:
            last = (context.chat_data or {}).get("last_location") or {}
            not_found_keyboard = None
            if last.get("name"):
                token = callback_location_token(last["name"], last.get("coords"))
                not_found_keyboard = InlineKeyboardMarkup([[
                    styled_button(
                        f"🌤 查{last['name']}", callback_data=f"tq|{token}|default|0|0"
                    )
                ]])
            await send_text(
                update,
                context,
                f"❌ 未找到「{location_query}」。\n试试完整城市名或加上省份，如：辽宁 朝阳",
                reply_markup=not_found_keyboard,
            )
            return

        if context.chat_data is not None:
            context.chat_data["last_location"] = {
                "coords": data.coords,
                "name": data.location_name,
            }

        text = format_weather_response(data, view_type=view_type, days=limit, start_day=start_day)
        keyboard = get_weather_keyboard(location_query, coords=data.coords, view_type=view_type)

        chart_bytes = None
        chart_file_id = None
        if should_attach_rain_chart(data, view_type):
            # file_id first: re-rendering + re-uploading an identical chart cost
            # 0.5-2s per rainy query, exactly at the usage peak.
            chart_file_id = await get_cached_chart_file_id(data, "rain")
            if not chart_file_id:
                chart_bytes = await run_chart_render(Visualizer.draw_hourly_rain_chart, data)

        chart_photo = chart_file_id or (
            InputFile(io.BytesIO(chart_bytes), filename="rain.png") if chart_bytes else None
        )
        try:
            # Telegram caption limit is 1024 chars; fall back to photo + text.
            if chart_photo and len(text) <= 1000:
                sent = await send_photo(
                    update,
                    context,
                    photo=chart_photo,
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
                if sent is not None and not chart_file_id:
                    await remember_chart_file_id(data, "rain", sent)
            elif chart_photo:
                sent = await send_photo(
                    update,
                    context,
                    photo=chart_photo,
                )
                if sent is not None and not chart_file_id:
                    await remember_chart_file_id(data, "rain", sent)
                await send_text(
                    update,
                    context,
                    text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
            else:
                # Rich blocks when the server supports them; MarkdownV2 otherwise.
                await send_weather_view(
                    context,
                    update.effective_chat.id,
                    data,
                    view_type=view_type,
                    days=limit,
                    start_day=start_day,
                    reply_markup=keyboard,
                )
        except Exception as e:
            logger.error(f"Reply failed: {e}")
            try:
                token = callback_location_token(location_query, data.coords if data else None)
                await send_text(
                    update,
                    context,
                    "❌ 发送失败。",
                    reply_markup=InlineKeyboardMarkup([[
                        styled_button(
                            "🔄 重试",
                            callback_data=f"tq|{token}|{view_type}|{start_day}|{limit or 0}",
                        )
                    ]]),
                )
            except Exception as fallback_error:
                logger.error(f"Fallback send failed: {fallback_error}")
