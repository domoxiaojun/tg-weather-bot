import asyncio
from uuid import uuid4

from loguru import logger
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.config import settings
from core.handlers.common import BotDependencies, parse_location_and_view
from core.handlers.special_queries import (
    extract_special_query,
    resolve_tide,
    resolve_typhoon,
)
from services.telegram_rich import (
    bold,
    bullet_list,
    heading,
    input_rich_message_content,
    paragraph,
    rich,
)
from utils.formatter import format_weather_response, get_weather_keyboard
from utils.rich_formatter import build_weather_blocks


def _article_variants(
    *,
    result_id: str,
    title: str,
    description: str,
    blocks: list,
    fallback_text: str,
    fallback_parse_mode=None,
    reply_markup=None,
) -> tuple[InlineQueryResultArticle, InlineQueryResultArticle]:
    """Build matching rich/plain articles with the same stable result id."""
    common = {
        "id": result_id,
        "title": title,
        "description": description,
        "reply_markup": reply_markup,
    }
    rich_result = InlineQueryResultArticle(
        **common,
        # PTB 22.8 has no typed InputRichMessageContent yet, but accepts and
        # serializes the Bot API dictionary without altering it.
        input_message_content=input_rich_message_content(blocks=blocks),
    )
    plain_result = InlineQueryResultArticle(
        **common,
        input_message_content=InputTextMessageContent(
            fallback_text,
            parse_mode=fallback_parse_mode,
        ),
    )
    return rich_result, plain_result


class InlineHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    @staticmethod
    async def _answer(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        rich_results: list,
        plain_results: list,
        *,
        cache_time: int,
    ) -> None:
        """Prefer rich inline content, retry the same query with plain results."""
        inline_query = update.inline_query
        if await rich.answer_inline_query(
            context.bot,
            inline_query.id,
            rich_results,
            cache_time=cache_time,
            is_personal=True,
        ):
            return
        await inline_query.answer(plain_results, cache_time=cache_time, is_personal=True)

    async def _answer_special(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        payload,
        *,
        cache_time: int,
    ) -> None:
        """Answer with a single typhoon/tide article (rich preferred)."""
        from services.telegram_rich import heading, paragraph

        blocks = payload.blocks
        if not blocks:
            blocks = [
                heading(payload.title, size=3),
                paragraph(payload.fallback_text.replace("<b>", "").replace("</b>", "")),
            ]
        rich_result, plain_result = _article_variants(
            result_id=str(uuid4()),
            title=payload.title,
            description=payload.description,
            blocks=blocks,
            fallback_text=payload.fallback_text,
            fallback_parse_mode=ParseMode.HTML,
        )
        await self._answer(
            update,
            context,
            [rich_result],
            [plain_result],
            cache_time=cache_time,
        )

    async def _answer_notice(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        title: str,
        description: str,
        heading_text: str,
        detail: str,
        cache_time: int,
    ) -> None:
        rich_result, plain_result = _article_variants(
            result_id=str(uuid4()),
            title=title,
            description=description,
            blocks=[heading(heading_text, size=4), paragraph(detail)],
            fallback_text=f"{heading_text}\n{detail}",
        )
        await self._answer(
            update,
            context,
            [rich_result],
            [plain_result],
            cache_time=cache_time,
        )

    async def handle_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle ``@bot 城市`` queries with rich result content and fallback."""
        query = update.inline_query.query.strip()

        location_query = None
        if update.inline_query.location:
            lon = update.inline_query.location.longitude
            lat = update.inline_query.location.latitude
            location_query = f"{lon},{lat}"
            logger.info(f"Inline查询使用用户位置: {location_query}")
            if not query:
                query = None

        if not location_query and not query:
            help_blocks = [
                heading("🌤️ DomoWeather 使用方法", size=3),
                paragraph([bold("📍 位置查询"), "：开启位置权限，直接回车"]),
                paragraph([bold("✍️ 文本查询")]),
                bullet_list([
                    "北京 — 今日天气",
                    "上海 3 — 未来3天",
                    "广州 24h — 逐小时",
                    "深圳 降水 — 降水预报",
                    "杭州 指数 — 生活指数",
                    "台风 / 台风 珠海 — 活跃台风与影响",
                    "潮汐 青岛 — 当日高低潮",
                ]),
            ]
            fallback = (
                "🌤️ <b>DomoWeather 使用方法</b>\n\n"
                "📍 <b>方式1：位置查询</b>\n"
                "   开启位置权限，直接回车即可\n\n"
                "✍️ <b>方式2：文本查询</b>\n"
                "   • <code>北京</code> - 今日天气\n"
                "   • <code>上海 3</code> - 未来3天\n"
                "   • <code>广州 24h</code> - 逐小时\n"
                "   • <code>深圳 降水</code> - 降水预报\n"
                "   • <code>杭州 指数</code> - 生活指数\n"
                "   • <code>台风</code> / <code>台风 珠海</code> - 活跃台风\n"
                "   • <code>潮汐 青岛</code> - 当日高低潮"
            )
            rich_result, plain_result = _article_variants(
                result_id=str(uuid4()),
                title="💡 使用说明",
                description="输入城市名或开启位置权限",
                blocks=help_blocks,
                fallback_text=fallback,
                fallback_parse_mode=ParseMode.HTML,
            )
            await self._answer(
                update,
                context,
                [rich_result],
                [plain_result],
                cache_time=300,
            )
            return

        try:
            view_type = "default"
            days = None
            start_day = 0
            special_mode = None
            special_location = None

            if query is not None:
                parts = query.split()
                special_mode, special_location = extract_special_query(parts)
                if special_mode is None:
                    parsed_location, view_type, start_day, days = parse_location_and_view(parts)
                    if not location_query:
                        location_query = parsed_location
                elif special_location and not location_query:
                    location_query = special_location

            # Dedicated typhoon / tide results (keyword path).
            if special_mode == "typhoon":
                payload = await asyncio.wait_for(
                    resolve_typhoon(self.deps, location_query),
                    timeout=10.0,
                )
                await self._answer_special(update, context, payload, cache_time=30)
                return
            if special_mode == "tide":
                if not location_query:
                    await self._answer_notice(
                        update,
                        context,
                        title="🌊 需要沿海城市",
                        description="例如：潮汐 青岛",
                        heading_text="🌊 潮汐查询",
                        detail="请加上城市，例如：潮汐 青岛 / 厦门潮汐",
                        cache_time=60,
                    )
                    return
                payload = await asyncio.wait_for(
                    resolve_tide(self.deps, location_query),
                    timeout=10.0,
                )
                await self._answer_special(update, context, payload, cache_time=60)
                return

            data = await asyncio.wait_for(
                self.deps.weather_service.get_fused_weather(location_query, profile="full"),
                timeout=8.0,
            )

            if not data:
                await self._answer_notice(
                    update,
                    context,
                    title="❌ 未找到数据",
                    description="请检查城市名称或网络连接",
                    heading_text="⚠️ 无法获取天气数据",
                    detail="请检查输入或稍后重试",
                    cache_time=10,
                )
                return

            summary_short = data.summary.split("\n")[0] if data.summary else ""
            specs = [
                ("default", f"🌤️ {data.location_name} · 实时", f"{data.now_text} {data.now_temp}°C · {summary_short[:35]}", "default", None),
                ("daily3", f"📅 {data.location_name} · 未来3天", "详细逐日预报", "daily", 3),
                ("daily7", f"📅 {data.location_name} · 未来7天", "一周天气趋势", "daily", 7),
                ("hourly12", f"⏰ {data.location_name} · 未来12小时", "逐小时预报", "hourly", 12),
                ("hourly24", f"⏰ {data.location_name} · 未来24小时", "全天逐小时预报", "hourly", 24),
            ]
            if data.minutely:
                specs.append(("rain", f"☔️ {data.location_name} · 降水预报", "分钟级降水趋势", "rain", None))
            if data.indices:
                specs.append(("indices", f"💡 {data.location_name} · 生活指数", "穿衣、洗车、运动等建议", "indices", None))

            rich_results = []
            plain_results = []
            tags = []
            for tag, title, description, result_view, result_days in specs:
                result_id = str(uuid4())
                keyboard = get_weather_keyboard(
                    data.location_name,
                    show_charts=True,
                    coords=data.coords,
                    view_type=result_view,
                )
                rich_result, plain_result = _article_variants(
                    result_id=result_id,
                    title=title,
                    description=description,
                    blocks=build_weather_blocks(data, view_type=result_view, days=result_days),
                    fallback_text=format_weather_response(data, view_type=result_view, days=result_days),
                    fallback_parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
                tags.append(tag)
                rich_results.append(rich_result)
                plain_results.append(plain_result)

            # Honor the requested view by promoting the matching result.
            preferred = None
            if view_type == "daily":
                preferred = "daily7" if (days or 0) >= 7 else "daily3"
            elif view_type == "hourly":
                preferred = "hourly24" if (days or 24) >= 24 else "hourly12"
            elif view_type in {"rain", "indices"}:
                preferred = view_type
            if preferred in tags:
                index = tags.index(preferred)
                if index > 0:
                    tags.insert(0, tags.pop(index))
                    rich_results.insert(0, rich_results.pop(index))
                    plain_results.insert(0, plain_results.pop(index))

            loading_keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⏳ 生成中...", callback_data="noop")]]
            )
            ai_rich, ai_plain = _article_variants(
                result_id=f"ai_report:{data.coords}",
                title=f"🤖 {data.location_name} · AI 天气日报",
                description="点击发送，Bot 将实时生成日报",
                blocks=[
                    heading(f"🤖 {data.location_name} · AI 天气日报", size=4),
                    paragraph("⏳ 正在撰写，通常需要 10-30 秒…"),
                    paragraph("若长时间没有更新，请回到输入框重新选择一次。"),
                ],
                fallback_text=(
                    f"⏳ 正在为 {data.location_name} 撰写 AI 天气日报…\n"
                    "通常需要 10-30 秒；若长时间没有更新，请回到输入框重新选择一次。"
                ),
                reply_markup=loading_keyboard,
            )
            rich_results.insert(1, ai_rich)
            plain_results.insert(1, ai_plain)

            # Extra one-tap cards for typhoon / tide when we already have a city.
            if settings.enable_typhoon_alerts and data.coords:
                try:
                    typhoon_payload = await asyncio.wait_for(
                        resolve_typhoon(self.deps, data.coords),
                        timeout=6.0,
                    )
                    if typhoon_payload.blocks or typhoon_payload.fallback_text:
                        t_rich, t_plain = _article_variants(
                            result_id=str(uuid4()),
                            title=typhoon_payload.title,
                            description=typhoon_payload.description,
                            blocks=typhoon_payload.blocks
                            or [
                                heading(typhoon_payload.title, size=3),
                                paragraph(typhoon_payload.fallback_text),
                            ],
                            fallback_text=typhoon_payload.fallback_text,
                            fallback_parse_mode=ParseMode.HTML,
                        )
                        rich_results.append(t_rich)
                        plain_results.append(t_plain)
                except Exception as error:
                    logger.debug(f"Inline typhoon append skipped: {error}")

            if settings.enable_tide and data.coords:
                try:
                    tide_payload = await asyncio.wait_for(
                        resolve_tide(self.deps, data.coords),
                        timeout=6.0,
                    )
                    if tide_payload.blocks:
                        td_rich, td_plain = _article_variants(
                            result_id=str(uuid4()),
                            title=tide_payload.title,
                            description=tide_payload.description,
                            blocks=tide_payload.blocks,
                            fallback_text=tide_payload.fallback_text,
                            fallback_parse_mode=ParseMode.HTML,
                        )
                        rich_results.append(td_rich)
                        plain_results.append(td_plain)
                except Exception as error:
                    logger.debug(f"Inline tide append skipped: {error}")

            await self._answer(
                update,
                context,
                rich_results,
                plain_results,
                cache_time=1,
            )

        except asyncio.TimeoutError:
            logger.error("Inline查询超时")
            await self._answer_notice(
                update,
                context,
                title="⏱️ 查询超时",
                description="服务器响应过慢，请重试",
                heading_text="⚠️ 查询超时",
                detail="请稍后重试",
                cache_time=10,
            )
        except Exception as error:
            logger.error(f"Inline Query Error: {error}")
            await self._answer_notice(
                update,
                context,
                title="⚠️ 系统错误",
                description="请稍后重试",
                heading_text="⚠️ 系统繁忙",
                detail="请稍后重试",
                cache_time=10,
            )
