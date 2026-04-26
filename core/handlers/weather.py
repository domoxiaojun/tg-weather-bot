import io

from loguru import logger
from telegram import InputFile, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from core.config import settings
from core.handlers.common import BotDependencies, parse_location_and_view
from core.handlers.messages import send_photo, send_text
from services.chart_cache import (
    get_cached_chart_file_id,
    get_chart_caption,
    normalize_chart_type,
    render_chart_bytes,
)
from services.visualizer import Visualizer
from utils.formatter import format_weather_response, get_weather_keyboard


class WeatherHandlers:
    def __init__(self, deps: BotDependencies):
        self.deps = deps

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_text = (
            "👋 <b>欢迎使用 DomoWeather Bot！</b>\n\n"
            "🔍 <b>查询天气</b>：\n"
            "• <code>/tq 北京</code> —— 实时天气\n"
            "• <code>/tq 北京 daily 3</code> —— 未来3天\n"
            "• <code>/tq 北京 hourly 24</code> —— 未来24小时\n"
            "• <code>/chart 北京</code> —— 生成趋势图\n"
            "• <code>/report 北京</code> —— 生成AI天气日报\n"
            "• <code>/rain_my</code> —— 查看降雨提醒订阅\n"
            "• <b>Inline模式</b>：直接在对话框输入 <code>@bot_name 北京</code>\n\n"
            "数据源：和风天气 (QWeather) & 彩云天气 (Caiyun)"
        )
        await send_text(update, context, welcome_text, parse_mode=ParseMode.HTML)

    async def chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/chart [城市] [daily|hourly|rain] -> 发送趋势图"""
        args = context.args
        if not args:
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

        location = args[0]
        chart_type = normalize_chart_type(args[1].lower() if len(args) > 1 else "temp")

        try:
            data = await self.deps.weather_service.get_fused_weather(location)
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
            )
            return

        img_bytes = render_chart_bytes(data, chart_type)
        if not img_bytes:
            await send_text(update, context, f"⚠️ 暂无{caption.split(' ', 1)[-1]}数据，无法绘制图表")
            return

        await send_photo(
            update,
            context,
            photo=InputFile(io.BytesIO(img_bytes), filename=f"{location}_{chart_type}.png"),
            caption=caption,
        )

    async def handle_weather_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /tq and location weather requests."""
        location_query = None
        view_type = "default"
        start_day = 0
        limit = None

        if update.message.location:
            location_query = f"{update.message.location.longitude},{update.message.location.latitude}"
        elif context.args:
            location_query, view_type, start_day, limit = parse_location_and_view(list(context.args))
        else:
            await send_text(update, context, "请提供城市名称或定位，例如 `/tq 北京`。")
            return

        if not location_query:
            await send_text(update, context, "请提供城市名称或定位，例如 `/tq 北京`。")
            return

        try:
            await update.message.set_reaction("👀")
        except Exception:
            pass
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        try:
            data = await self.deps.weather_service.get_fused_weather(location_query)
        except Exception as e:
            logger.error(f"Error fetching weather: {e}")
            await send_text(update, context, "❌ 系统繁忙，请稍后再试。")
            return

        if not data:
            await send_text(update, context, "❌ 找不到该地区的天气数据，请检查拼写。")
            return

        text = format_weather_response(data, view_type=view_type, days=limit, start_day=start_day)
        keyboard = get_weather_keyboard(location_query)

        chart_bytes = None
        if settings.enable_weather_plots:
            should_plot = view_type == "rain" or data.is_raining
            if should_plot:
                chart_bytes = Visualizer.draw_hourly_rain_chart(data)

        try:
            if chart_bytes:
                await send_photo(
                    update,
                    context,
                    photo=InputFile(io.BytesIO(chart_bytes), filename="rain.png"),
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
            else:
                await send_text(
                    update,
                    context,
                    text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard,
                )
        except Exception as e:
            logger.error(f"Reply failed: {e}")
            try:
                await send_text(update, context, "❌ 发送失败，请重试。")
            except Exception as fallback_error:
                logger.error(f"Fallback send failed: {fallback_error}")
