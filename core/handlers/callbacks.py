import io

from loguru import logger
from telegram import InputFile, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.handlers.common import BotDependencies
from services.chart_cache import (
    get_cached_chart_file_id,
    get_chart_caption,
    get_or_create_chart_file_id,
    normalize_chart_type,
    remember_chart_file_id,
    render_chart_bytes_async,
)
from utils.formatter import format_weather_response, get_weather_keyboard


class CallbackHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    @staticmethod
    async def _safe_answer(query, text: str | None = None, show_alert: bool = False) -> bool:
        try:
            await query.answer(text=text, show_alert=show_alert)
            return True
        except Exception:
            return False

    async def _notify(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        """Alert via callback answer; fall back to a chat message once answered."""
        query = update.callback_query
        if await self._safe_answer(query, text, show_alert=True):
            return
        chat = update.effective_chat
        if chat:
            try:
                await context.bot.send_message(chat_id=chat.id, text=text)
            except Exception as e:
                logger.debug(f"Callback fallback notify failed: {e}")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline keyboard callbacks."""
        query = update.callback_query
        if not query:
            return

        data_parts = query.data.split("|")
        action = data_parts[0]

        if action == "noop":
            await query.answer()
            return

        if action == "chart":
            # Ack immediately: fetching + rendering can take seconds and the
            # button would keep spinning until the query expires.
            await self._safe_answer(query, "⏳ 正在生成图表...")
            await self._handle_chart(update, context, data_parts)
            return

        if action == "back":
            await query.answer("图表消息无法直接恢复文字，请重新查询或点击刷新。", show_alert=True)
            return

        location = data_parts[1] if len(data_parts) > 1 else None

        if action == "refresh" and location:
            await self._safe_answer(query, "⏳ 正在刷新...")
            await self._handle_refresh(update, context, location)
            return

        if action == "sub" and location:
            await self._handle_subscribe(update, context, location)
            return

        await query.answer()

    async def _handle_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data_parts: list[str]):
        query = update.callback_query
        location = data_parts[1] if len(data_parts) > 1 else "北京"
        chart_type = normalize_chart_type(data_parts[2] if len(data_parts) > 2 else "temp")
        profile = {
            "daily": "daily",
            "rain": "rain",
            "temp": "hourly",
        }[chart_type]

        try:
            weather_data = await self.deps.weather_service.get_fused_weather(location, profile=profile)
        except Exception as e:
            logger.error(f"Chart data fetch error: {e}")
            await self._notify(update, context, "图表数据获取失败")
            return

        if not weather_data:
            await self._notify(update, context, "未获取到天气数据")
            return

        caption = get_chart_caption(weather_data, chart_type)
        is_inline = query.inline_message_id is not None

        file_id = await get_cached_chart_file_id(weather_data, chart_type)
        if is_inline and not file_id:
            file_id = await get_or_create_chart_file_id(context.bot, weather_data, chart_type)

        if file_id:
            if is_inline:
                try:
                    await query.edit_message_media(
                        media=InputMediaPhoto(media=file_id, caption=caption),
                        reply_markup=get_weather_keyboard(
                            location, mode="chart", coords=weather_data.coords
                        ),
                    )
                except Exception as e:
                    logger.error(f"Inline chart edit failed: {e}")
                    await self._notify(update, context, "❌ 更新图表失败")
            else:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=file_id,
                    caption=caption,
                    reply_markup=get_weather_keyboard(
                        location, mode="chart", coords=weather_data.coords
                    ),
                )
            return

        img_bytes = await render_chart_bytes_async(weather_data, chart_type)
        if not img_bytes:
            await self._notify(update, context, "⚠️ 暂无图表数据")
            return

        if is_inline:
            await self._notify(update, context, "⚠️ Inline 图表需要 SUPER_ADMIN_ID 用于预上传 file_id 缓存。")
            return

        sent = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=InputFile(io.BytesIO(img_bytes), filename=f"{chart_type}.png"),
            caption=caption,
            reply_markup=get_weather_keyboard(location, mode="chart", coords=weather_data.coords),
        )
        await remember_chart_file_id(weather_data, chart_type, sent)

    async def _handle_refresh(self, update: Update, context: ContextTypes.DEFAULT_TYPE, location: str):
        query = update.callback_query
        try:
            weather_data = await self.deps.weather_service.get_fused_weather(
                location,
                profile="full",
                refresh_qweather=True,
            )
            if not weather_data:
                await query.answer("未获取到天气数据", show_alert=True)
                return

            is_inline = query.inline_message_id is not None
            text = format_weather_response(weather_data)
            keyboard = get_weather_keyboard(location, show_charts=True, coords=weather_data.coords)

            is_caption = bool(query.message and query.message.caption)
            try:
                if is_caption:
                    await query.edit_message_caption(
                        caption=text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=keyboard,
                    )
                else:
                    await query.edit_message_text(
                        text=text,
                        parse_mode=ParseMode.MARKDOWN_V2,
                        reply_markup=keyboard,
                    )
                await self._safe_answer(query, "✅ 数据已更新")
            except Exception as e:
                if "Message is not modified" in str(e):
                    await self._safe_answer(query, "暂无新数据")
                    return
                if is_inline:
                    try:
                        await query.edit_message_caption(
                            caption=text,
                            parse_mode=ParseMode.MARKDOWN_V2,
                            reply_markup=keyboard,
                        )
                        await self._safe_answer(query, "✅ 数据已更新")
                        return
                    except Exception:
                        pass
                logger.error(f"Refresh edit failed: {e}")
                await self._notify(update, context, "刷新失败")
        except Exception as e:
            logger.error(f"Refresh failed: {e}")
            await self._notify(update, context, "刷新出错")

    async def _handle_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE, location: str):
        query = update.callback_query
        if query.inline_message_id:
            await query.answer("⚠️ Inline模式无法订阅，请在与Bot私聊或群组中使用 /tq 后点击订阅。", show_alert=True)
            return

        await self._safe_answer(query)
        # Callback tokens may be coordinates; normalize to a display name so
        # the subscription list stays deduplicated and human-readable.
        try:
            loc_info = await self.deps.weather_service.qweather.get_geo_location(location)
        except Exception as e:
            logger.error(f"Subscribe geocode failed: {e}")
            loc_info = None
        if loc_info:
            name = loc_info.get("name") or location
            adm1 = loc_info.get("adm1")
            location = f"{name}, {adm1}" if adm1 and adm1 != name else name

        subs = context.chat_data.get("subs", [])
        if location not in subs:
            subs.append(location)
            context.chat_data["subs"] = subs
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ 已订阅降雨提醒: {location}",
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"ℹ️ 你已经订阅了 {location}。",
            )
