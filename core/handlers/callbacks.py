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
    def __init__(self, deps: BotDependencies, weather_handlers=None, report_handlers=None):
        self.deps = deps
        self.weather_handlers = weather_handlers
        self.report_handlers = report_handlers

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
            # The only noop button is the inline AI-report placeholder.
            await self._safe_answer(query, "⏳ 日报生成中，请稍候…")
            return

        if action == "chart":
            # Ack immediately: fetching + rendering can take seconds and the
            # button would keep spinning until the query expires.
            await self._safe_answer(query, "⏳ 正在生成图表...")
            await self._handle_chart(update, context, data_parts)
            return

        if action == "back":
            await query.answer("图表消息无法直接恢复文字，请点「📝 文字天气」按钮或重新发送 /tq 城市。", show_alert=True)
            return

        location = data_parts[1] if len(data_parts) > 1 else None

        if action == "refresh" and location:
            await self._safe_answer(query, "⏳ 正在刷新...")
            await self._handle_refresh(update, context, data_parts)
            return

        if action == "view" and location:
            await self._handle_view_switch(update, context, data_parts)
            return

        if action == "sub" and location:
            await self._handle_subscribe(update, context, location)
            return

        if action == "tq" and location:
            await self._safe_answer(query, "⏳ 查询中...")
            await self._handle_weather_choice(update, context, data_parts)
            return

        if action == "report" and location:
            await self._handle_report(update, context, location)
            return

        if action == "unsub" and len(data_parts) >= 3:
            await self._handle_unsubscribe(update, context, data_parts)
            return

        await query.answer()

    async def _handle_weather_choice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        data_parts: list[str],
    ):
        """City choice / text-weather button: tq|{coords}|{view}|{start_day}|{limit}"""
        query = update.callback_query
        if query.inline_message_id:
            await self._safe_answer(
                query,
                "⚠️ 这条消息里无法发送文字天气，请在聊天中发送 /tq 城市。",
                show_alert=True,
            )
            return
        if self.weather_handlers is None:
            await self._notify(update, context, "❌ 功能暂不可用")
            return
        coords = data_parts[1]
        view_type = data_parts[2] if len(data_parts) > 2 else "default"
        try:
            start_day = int(data_parts[3]) if len(data_parts) > 3 else 0
        except ValueError:
            start_day = 0
        try:
            limit = int(data_parts[4]) if len(data_parts) > 4 else 0
        except ValueError:
            limit = 0
        await self.weather_handlers._send_weather(
            update,
            context,
            coords,
            view_type=view_type,
            start_day=start_day,
            limit=limit or None,
        )
        # Retire the chooser buttons so the list cannot be tapped repeatedly.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

    async def _handle_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE, location: str):
        query = update.callback_query
        if query.inline_message_id:
            await query.answer(
                "⚠️ Inline 消息不支持此按钮，请在 @bot 的结果里直接选择「AI 天气日报」。",
                show_alert=True,
            )
            return
        if self.report_handlers is None or not self.deps.llm_service.provider:
            await query.answer("⚠️ AI 日报功能未配置。", show_alert=True)
            return

        await self._safe_answer(query, "🤖 正在生成 AI 日报...")
        try:
            weather_data = await self.deps.weather_service.get_fused_weather(location, profile="full")
            if not weather_data:
                await self._notify(update, context, "未获取到天气数据")
                return
            await self.report_handlers.send_report_for_weather(update, context, weather_data)
        except Exception as e:
            logger.error(f"Report button failed: {e}")
            await self._notify(update, context, "❌ 生成日报失败，请稍后重试。")

    async def _handle_unsubscribe(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        data_parts: list[str],
    ):
        """One-tap unsubscribe from the /rain_my and /daily_my lists."""
        from core.handlers.subscriptions import (
            remove_subscription_entry,
            render_subscription_list,
        )

        query = update.callback_query
        kind = data_parts[1]
        try:
            index = int(data_parts[2])
        except ValueError:
            await query.answer()
            return
        if kind not in {"daily", "rain"}:
            await query.answer()
            return

        removed = remove_subscription_entry(context.chat_data, kind, index)
        if removed is None:
            # List changed since the message was rendered; refresh it below.
            await self._safe_answer(query, "列表已变化，已刷新")
        else:
            await self._safe_answer(query, f"✅ 已取消 {removed}")

        text, keyboard = render_subscription_list(context.chat_data, kind)
        try:
            if text is None:
                empty_text = (
                    "📭 你还没有订阅任何早安简报。" if kind == "daily" else "📭 你还没有订阅任何降雨提醒。"
                )
                await query.edit_message_text(empty_text)
            else:
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"Subscription list refresh failed: {e}")

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
            await self._notify(update, context, "⚠️ 这条消息里暂时无法生成图表，请私聊 Bot 发送「/chart 城市」查看。")
            return

        sent = await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=InputFile(io.BytesIO(img_bytes), filename=f"{chart_type}.png"),
            caption=caption,
            reply_markup=get_weather_keyboard(location, mode="chart", coords=weather_data.coords),
        )
        await remember_chart_file_id(weather_data, chart_type, sent)

    @staticmethod
    def _parse_view_parts(data_parts: list[str]) -> tuple[str, int, int]:
        view = data_parts[2] if len(data_parts) > 2 else "default"
        try:
            start_day = int(data_parts[3]) if len(data_parts) > 3 else 0
        except ValueError:
            start_day = 0
        try:
            limit = int(data_parts[4]) if len(data_parts) > 4 else 0
        except ValueError:
            limit = 0
        return view, start_day, limit

    _VIEW_PROFILES = {
        "hourly": "hourly",
        "daily": "daily",
        "rain": "rain",
        "indices": "indices",
    }

    async def _handle_view_switch(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        data_parts: list[str],
    ):
        """Edit the weather message in place to another view — no re-send spam."""
        query = update.callback_query
        location = data_parts[1]
        view, start_day, limit = self._parse_view_parts(data_parts)

        if query.message is not None and query.message.caption is not None:
            # Photo captions cannot hold the longer views reliably.
            await self._safe_answer(query, "图片消息无法切换视图，请点「📝 文字天气」或重新 /tq。", show_alert=True)
            return

        await self._safe_answer(query, "⏳ 切换中...")
        try:
            weather_data = await self.deps.weather_service.get_fused_weather(
                location,
                profile=self._VIEW_PROFILES.get(view, "full"),
            )
            if not weather_data:
                await self._notify(update, context, "未获取到天气数据")
                return

            text = format_weather_response(
                weather_data, view_type=view, days=limit or None, start_day=start_day
            )
            keyboard = get_weather_keyboard(
                location, show_charts=True, coords=weather_data.coords, view_type=view
            )
            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
            except Exception as e:
                if "Message is not modified" in str(e):
                    await self._safe_answer(query, "已是当前内容")
                    return
                raise
        except Exception as e:
            logger.error(f"View switch failed: {e}")
            await self._notify(update, context, "切换失败，请稍后重试")

    async def _handle_refresh(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data_parts: list[str]):
        query = update.callback_query
        location = data_parts[1]
        view, start_day, limit = self._parse_view_parts(data_parts)
        try:
            weather_data = await self.deps.weather_service.get_fused_weather(
                location,
                profile=self._VIEW_PROFILES.get(view, "full"),
                refresh_qweather=True,
            )
            if not weather_data:
                await query.answer("未获取到天气数据", show_alert=True)
                return

            is_inline = query.inline_message_id is not None
            text = format_weather_response(
                weather_data, view_type=view, days=limit or None, start_day=start_day
            )
            keyboard = get_weather_keyboard(
                location, show_charts=True, coords=weather_data.coords, view_type=view
            )

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
        from core.handlers.subscriptions import (
            rain_alert_expectation,
            subscription_limit_message,
            subscription_limit_reached,
        )

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

        chat = update.effective_chat
        group_note = "（本群成员都会收到）" if chat is not None and chat.type != "private" else ""

        subs = context.chat_data.get("subs", [])
        if location in subs:
            await context.bot.send_message(
                chat_id=chat.id,
                text=f"ℹ️ 已经订阅过 {location} 了。管理订阅：/rain_my",
            )
            return
        if subscription_limit_reached(subs):
            await context.bot.send_message(chat_id=chat.id, text=subscription_limit_message("/rain_my"))
            return

        subs.append(location)
        context.chat_data["subs"] = subs
        await context.bot.send_message(
            chat_id=chat.id,
            text=f"✅ 已订阅 {location} 的降雨提醒{group_note}。\n{rain_alert_expectation()}",
        )
