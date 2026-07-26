import io

from loguru import logger
from telegram import InputFile, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.handlers.common import BotDependencies
from core.handlers.messages import edit_weather_view
from services.chart_cache import (
    CHART_PROFILES,
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

        if action == "lvl" and len(data_parts) >= 3:
            await self._handle_rain_level(update, context, data_parts)
            return

        if action == "dsub" and location:
            await self._handle_subscribe(update, context, location, kind="daily")
            return

        if action == "dtime" and len(data_parts) >= 3:
            await self._handle_daily_time(update, context, data_parts)
            return

        if action == "subview" and len(data_parts) >= 2 and data_parts[1] in {"daily", "rain"}:
            await self._safe_answer(query)
            await self._refresh_subscription_list(query, context, data_parts[1])
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

    async def _handle_rain_level(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        data_parts: list[str],
    ):
        """Change a rain subscription's sensitivity from /rain_my: lvl|{index}|{level}"""
        from core.handlers.subscriptions import set_rain_level
        from core.scheduler import rain_level_hint, rain_level_label

        query = update.callback_query
        try:
            index = int(data_parts[1])
        except ValueError:
            await query.answer()
            return
        level = data_parts[2]

        location = set_rain_level(context.chat_data, index, level)
        if location is None:
            await self._safe_answer(query, "列表已变化，已刷新")
        else:
            await self._safe_answer(
                query, f"✅ {location}：{rain_level_label(level)}——{rain_level_hint(level)}"
            )
        await self._refresh_subscription_list(query, context, "rain")

    async def _handle_daily_time(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        data_parts: list[str],
    ):
        """Change a daily brief's push time from /daily_my: dtime|{index}|{HH:MM}"""
        from core.handlers.subscriptions import set_daily_time

        query = update.callback_query
        try:
            index = int(data_parts[1])
        except ValueError:
            await query.answer()
            return

        location = set_daily_time(context.chat_data, index, data_parts[2])
        if location is None:
            await self._safe_answer(query, "列表已变化，已刷新")
        else:
            await self._safe_answer(query, f"✅ {location}：每天 {data_parts[2]} 推送")
        await self._refresh_subscription_list(query, context, "daily")

    async def _refresh_subscription_list(self, query, context, kind: str):
        """Re-render the card in place so the message always matches stored state."""
        from core.handlers.subscriptions import (
            build_subscription_blocks,
            render_empty_card,
            render_subscription_list,
        )
        from services.telegram_rich import FEATURE_EDIT, rich

        text, keyboard = render_subscription_list(context.chat_data, kind)
        if text is None:
            # Empty state still offers a way forward (➕ last city / flip card).
            text, keyboard = render_empty_card(context.chat_data, kind)
            try:
                await query.edit_message_text(text, reply_markup=keyboard)
            except Exception as e:
                if "Message is not modified" not in str(e):
                    logger.debug(f"Subscription list refresh failed: {e}")
            return

        # Rich edit only in private chats: group cards are ephemeral fallbacks,
        # and a failing edit there must not strike out FEATURE_EDIT globally.
        message = getattr(query, "message", None)
        chat = getattr(message, "chat", None)
        if (
            message is not None
            and getattr(chat, "type", None) == "private"
            and rich.supports(FEATURE_EDIT)
        ):
            edited = await rich.edit_rich(
                context.bot,
                chat_id=chat.id,
                message_id=message.message_id,
                blocks=build_subscription_blocks(context.chat_data, kind),
                reply_markup=keyboard,
            )
            if edited:
                return
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.debug(f"Subscription list refresh failed: {e}")

    async def _handle_unsubscribe(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        data_parts: list[str],
    ):
        """One-tap unsubscribe from the /rain_my and /daily_my lists."""
        from core.handlers.subscriptions import remove_subscription_entry

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

        await self._refresh_subscription_list(query, context, kind)

    async def _handle_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, data_parts: list[str]):
        query = update.callback_query
        location = data_parts[1] if len(data_parts) > 1 else "北京"
        chart_type = normalize_chart_type(data_parts[2] if len(data_parts) > 2 else "temp")
        profile = CHART_PROFILES[chart_type]

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

            keyboard = get_weather_keyboard(
                location, show_charts=True, coords=weather_data.coords, view_type=view
            )
            if await edit_weather_view(
                context,
                weather_data,
                chat_id=query.message.chat_id if query.message else None,
                message_id=query.message.message_id if query.message else None,
                inline_message_id=query.inline_message_id,
                view_type=view,
                days=limit or None,
                start_day=start_day,
                reply_markup=keyboard,
            ):
                return

            text = format_weather_response(
                weather_data, view_type=view, days=limit or None, start_day=start_day
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
            keyboard = get_weather_keyboard(
                location, show_charts=True, coords=weather_data.coords, view_type=view
            )

            is_caption = bool(query.message and query.message.caption)
            if not is_caption and await edit_weather_view(
                context,
                weather_data,
                chat_id=query.message.chat_id if query.message else None,
                message_id=query.message.message_id if query.message else None,
                inline_message_id=query.inline_message_id,
                view_type=view,
                days=limit or None,
                start_day=start_day,
                reply_markup=keyboard,
            ):
                await self._safe_answer(query, "✅ 数据已更新")
                return

            text = format_weather_response(
                weather_data, view_type=view, days=limit or None, start_day=start_day
            )
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

    async def _handle_subscribe(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        location: str,
        kind: str = "rain",
    ):
        """🔔/📅/➕ buttons: subscribe to rain alerts or the daily brief."""
        query = update.callback_query
        if query.inline_message_id:
            await query.answer("⚠️ Inline模式无法订阅，请在与Bot私聊或群组中使用 /tq 后点击订阅。", show_alert=True)
            return

        from core.handlers.subscriptions import (
            DEFAULT_DAILY_BRIEF_TIME_HINT,
            SubscriptionHandlers,
            rain_alert_expectation,
            send_subscription_card,
            subscription_limit_message,
            subscription_limit_reached,
        )
        from core.scheduler import DEFAULT_DAILY_BRIEF_TIME, DEFAULT_RAIN_LEVEL, rain_level_label

        # Callback tokens may be coordinates; normalize to a display name so
        # the subscription list stays deduplicated and human-readable.
        location_tz = None
        try:
            loc_info = await self.deps.weather_service.qweather.get_geo_location(location)
        except Exception as e:
            logger.error(f"Subscribe geocode failed: {e}")
            loc_info = None
        if loc_info:
            name = loc_info.get("name") or location
            adm1 = loc_info.get("adm1")
            location = f"{name}, {adm1}" if adm1 and adm1 != name else name
            location_tz = loc_info.get("tz")

        chat = update.effective_chat
        list_key = "subs" if kind == "rain" else "daily_subs"
        manage_command = "/rain_my" if kind == "rain" else "/daily_my"
        subs = context.chat_data.setdefault(list_key, [])

        existing = SubscriptionHandlers._find_subscribed(subs, location)
        if existing:
            await self._safe_answer(query, f"ℹ️ 已订阅过 {existing}，管理：{manage_command}")
            return
        if subscription_limit_reached(subs):
            await self._safe_answer(query, subscription_limit_message(manage_command), show_alert=True)
            return

        subs.append(location)
        SubscriptionHandlers._remember_push_target(context, update, location, location_tz)
        await self._safe_answer(query, f"✅ 已订阅 {location}")

        if chat is not None and chat.type != "private":
            # Group subscription affects everyone: announce publicly instead of
            # an ephemeral card only the tapper would see. No buttons here, so
            # point at the command instead of "the buttons below".
            if kind == "rain":
                text = (
                    f"✅ 已订阅 {location} 的降雨提醒（本群成员都会收到）。\n"
                    f"{rain_alert_expectation(location_tz)}"
                )
            else:
                text = (
                    f"✅ 已订阅 {location} 的早安简报，每天 {DEFAULT_DAILY_BRIEF_TIME} 推送"
                    f"（本群成员都会收到）。改时间：/daily_sub 城市 HH:MM"
                )
            await context.bot.send_message(chat_id=chat.id, text=text)
            return

        if kind == "rain":
            prefix = (
                f"✅ 已订阅 {location} 的降雨提醒（{rain_level_label(DEFAULT_RAIN_LEVEL)}）。\n"
                f"{rain_alert_expectation(location_tz, include_manage=False)}"
            )
        else:
            prefix = f"✅ 已订阅 {location} 的早安简报，{DEFAULT_DAILY_BRIEF_TIME_HINT}"
        await send_subscription_card(update, context, kind, prefix=prefix)
