import asyncio
from uuid import uuid4

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.handlers.common import BotDependencies, parse_location_and_view
from utils.formatter import format_weather_response, get_weather_keyboard


class InlineHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    async def handle_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle inline queries.
        Supports location sharing and text like: @bot 北京, @bot 上海 3, @bot 广州 rain.
        """
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
            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="💡 使用说明",
                    description="输入城市名或开启位置权限",
                    input_message_content=InputTextMessageContent(
                        "🌤️ **DomoWeather 使用方法**\n\n"
                        "📍 **方式1：位置查询**\n"
                        "   开启位置权限，直接回车即可\n\n"
                        "✍️ **方式2：文本查询**\n"
                        "   • `北京` - 今日天气\n"
                        "   • `上海 3` - 未来3天\n"
                        "   • `广州 24h` - 逐小时\n"
                        "   • `深圳 降水` - 降水预报\n"
                        "   • `杭州 指数` - 生活指数",
                        parse_mode=ParseMode.MARKDOWN,
                    ),
                )
            ]
            await update.inline_query.answer(results, cache_time=300, is_personal=True)
            return

        try:
            view_type = "default"
            days = None
            start_day = 0

            if query is not None:
                parts = query.split()
                parsed_location, view_type, start_day, days = parse_location_and_view(parts)
                if not location_query:
                    location_query = parsed_location

            data = await asyncio.wait_for(
                self.deps.weather_service.get_fused_weather(location_query),
                timeout=8.0,
            )

            if not data:
                results = [
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title="❌ 未找到数据",
                        description="请检查城市名称或网络连接",
                        input_message_content=InputTextMessageContent("⚠️ 无法获取天气数据\n请检查输入或稍后重试"),
                    )
                ]
                await update.inline_query.answer(results, cache_time=10, is_personal=True)
                return

            text_default = format_weather_response(data, view_type="default")
            text_3d = format_weather_response(data, view_type="daily", days=3)
            text_7d = format_weather_response(data, view_type="daily", days=7)
            text_12h = format_weather_response(data, view_type="hourly", days=12)
            text_24h = format_weather_response(data, view_type="hourly", days=24)

            summary_short = data.summary.split("\n")[0] if data.summary else ""
            keyboard = get_weather_keyboard(data.location_name, show_charts=True)

            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"🌤️ {data.location_name} · 实时",
                    description=f"{data.now_text} {data.now_temp}°C · {summary_short[:35]}",
                    input_message_content=InputTextMessageContent(
                        text_default,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    ),
                    reply_markup=keyboard,
                ),
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"📅 {data.location_name} · 未来3天",
                    description="详细逐日预报",
                    input_message_content=InputTextMessageContent(
                        text_3d,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    ),
                    reply_markup=keyboard,
                ),
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"📅 {data.location_name} · 未来7天",
                    description="一周天气趋势",
                    input_message_content=InputTextMessageContent(
                        text_7d,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    ),
                    reply_markup=keyboard,
                ),
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"⏰ {data.location_name} · 未来12小时",
                    description="逐小时预报",
                    input_message_content=InputTextMessageContent(
                        text_12h,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    ),
                    reply_markup=keyboard,
                ),
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"⏰ {data.location_name} · 未来24小时",
                    description="全天逐小时预报",
                    input_message_content=InputTextMessageContent(
                        text_24h,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    ),
                    reply_markup=keyboard,
                ),
            ]

            if data.minutely:
                text_rain = format_weather_response(data, view_type="rain")
                results.append(
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title=f"☔️ {data.location_name} · 降水预报",
                        description="分钟级降水趋势",
                        input_message_content=InputTextMessageContent(
                            text_rain,
                            parse_mode=ParseMode.MARKDOWN_V2,
                        ),
                        reply_markup=keyboard,
                    )
                )

            if data.indices:
                text_indices = format_weather_response(data, view_type="indices")
                results.append(
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title=f"💡 {data.location_name} · 生活指数",
                        description="穿衣、洗车、运动等建议",
                        input_message_content=InputTextMessageContent(
                            text_indices,
                            parse_mode=ParseMode.MARKDOWN_V2,
                        ),
                        reply_markup=keyboard,
                    )
                )

            loading_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⏳ 生成中...", callback_data="noop")]])
            results.insert(
                1,
                InlineQueryResultArticle(
                    id=f"ai_report:{data.location_name}",
                    title=f"🤖 {data.location_name} · AI 天气日报",
                    description="点击发送，Bot 将实时生成日报",
                    input_message_content=InputTextMessageContent(
                        f"⏳ 正在为 {data.location_name} 撰写 AI 天气日报...\n(Domo 正在思考 💭)",
                        parse_mode=None,
                    ),
                    reply_markup=loading_keyboard,
                ),
            )

            await update.inline_query.answer(results, cache_time=1, is_personal=True)

        except asyncio.TimeoutError:
            logger.error("Inline查询超时")
            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="⏱️ 查询超时",
                    description="服务器响应过慢，请重试",
                    input_message_content=InputTextMessageContent("⚠️ 查询超时\n请稍后重试"),
                )
            ]
            await update.inline_query.answer(results, cache_time=10, is_personal=True)
        except Exception as e:
            logger.error(f"Inline Query Error: {e}")
            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="⚠️ 系统错误",
                    description="请稍后重试",
                    input_message_content=InputTextMessageContent("系统繁忙，请稍后重试"),
                )
            ]
            await update.inline_query.answer(results, cache_time=10, is_personal=True)
