import datetime
import re
import io
import time
import asyncio
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, 
    filters, PicklePersistence, CallbackQueryHandler, InlineQueryHandler,
    ChosenInlineResultHandler
)
from telegram.constants import ParseMode, ChatAction

from core.config import settings
from services.fusion import WeatherFusionService
from services.visualizer import Visualizer
from utils.formatter import format_weather_response, get_weather_keyboard
from loguru import logger

from services.llm import LLMService

class WeatherBot:
    def __init__(self):
        self.weather_service = WeatherFusionService()
        self.subscription_service = None # Placeholder if needed
        self.llm_service = LLMService()
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        welcome_text = (
            "👋 **欢迎使用 DomoWeather Bot！**\n\n"
            "🔍 **查询天气**：\n"
            "• `/tq 北京` —— 实时天气\n"
            "• `/tq 北京 daily 3` —— 未来3天\n"
            "• `/tq 北京 hourly` —— 未来24小时\n"
            "• `/chart 北京` —— 生成趋势图\n"
            "• `/report 北京` —— 生成AI天气日报 (NEW! ✨)\n"
            "• **Inline模式**：直接在对话框输入 `@bot_name 北京`\n\n"
            "数据源：和风天气 (QWeather) & 彩云天气 (Caiyun)"
        )
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN
        )

    async def report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /report command for AI-generated summary"""
        if not context.args:
            await update.message.reply_text("请提供城市名称，例如：`/report 北京`", parse_mode=ParseMode.MARKDOWN)
            return

        location = context.args[0]
        
        # Check availability first
        # 1. Check availability
        if not self.llm_service.provider:
            await update.message.reply_text("⚠️ AI 天气日报功能尚未配置。")
            return

        # Start 'Typing' status immediately
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        
        try:
            # 2. Fetch Weather Data (Fast)
            weather_data = await self.weather_service.get_fused_weather(location)
            if not weather_data:
                await update.message.reply_text(f"❌ 未找到城市：{location}")
                return

            # Keep 'Typing' status alive for LLM generation
            # We explicitly send separate typing actions to ensure it stays visible
            async def keep_typing():
                while True:
                    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
                    await asyncio.sleep(4)

            typing_task = asyncio.create_task(keep_typing())

            try:
                # 3. Generate Report (Wait for full text)
                report_text = await self.llm_service.generate_weather_report(weather_data)
            finally:
                typing_task.cancel()
            
            # 4. Final Send (New Message)
            try:
                await update.message.reply_text(
                    text=f"📝 **{weather_data.location_name} 天气日报**\n\n{report_text}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception:
                # Markdown Fallback
                await update.message.reply_text(
                    text=f"📝 {weather_data.location_name} 天气日报\n\n{report_text}",
                    parse_mode=None
                )

        except Exception as e:
            logger.error(f"Report generation failed: {e}")
            await update.message.reply_text("❌ 生成日报失败，请稍后重试。")

    def parse_query_param(self, param: str) -> tuple[str, int, int]:
        """
        Parses command parameter to determine intent.
        Returns: (view_type, start_day, count_or_limit)
        """
        param = param.lower()
        today = datetime.date.today()
        
        # 1. Keywords
        if param in ["降水", "rain", "雨"]:
            return "rain", 0, 0
        if param in ["指数", "index", "indices", "life"]:
            return "indices", 0, 0
        
        # 2. Hourly: "24h", "48h"
        if param.endswith("h") and param[:-1].isdigit():
            hours = int(param[:-1])
            return "hourly", 0, min(hours, 72)
            
        # 3. Date Range or Specific Date (MM-DD or D-D)
        if "-" in param:
             parts = param.split("-")
             if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                 p1, p2 = int(parts[0]), int(parts[1])
                 
                 # Case A: Relative Range "1-5" (Start Day - End Day)
                 # Heuristic: If numbers are small (<32) and p1 <p2, could be date OR range.
                 # But "1-5" is likely range. "01-18" (Jan 18) is likely date.
                 # If first part is > 12 (Month), impossible. 
                 # Let's assume if it looks like a month-day, it is.
                 
                 # Try to interpret as Date MM-DD
                 try:
                     # Simple logic: assume current year first
                     target_date = datetime.date(today.year, p1, p2)
                     if target_date < today:
                         target_date = target_date.replace(year=today.year + 1)
                     
                     diff = (target_date - today).days
                     if 0 <= diff <= 15:
                         return "daily", diff, 1 # Specific date view
                 except ValueError:
                     pass # Not a valid date, try Range
                 
                 # Case B: Range "1-5" or "2-4"
                 start_idx = p1 - 1
                 end_idx = p2 - 1
                 if 0 <= start_idx < end_idx <= 15:
                     return "daily", start_idx, (end_idx - start_idx + 1)

        if "-" in param:
             # (Existing code for parse_query_param...)
             pass 

    async def daily_sub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_sub [城市] - 订阅每日早安简报"""
        if not context.args:
            await update.message.reply_text("usage: /daily_sub [城市名]")
            return
            
        location = context.args[0]
        subs = context.chat_data.setdefault("daily_subs", [])
        
        if location in subs:
            await update.message.reply_text(f"已订阅过 {location} 的日报。")
        else:
            subs.append(location)
            await update.message.reply_text(f"✅ 成功订阅 {location} 的早安简报！\n每天早晨 8:00 推送。")

    async def daily_unsub(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_unsub [城市] - 取消订阅"""
        if not context.args:
            await update.message.reply_text("usage: /daily_unsub [城市名]")
            return
            
        location = context.args[0]
        subs = context.chat_data.get("daily_subs", [])
        
        if location in subs:
            subs.remove(location)
            await update.message.reply_text(f"✅ 已取消 {location} 的订阅。")
        else:
            await update.message.reply_text(f"你没有订阅 {location}。")

    async def daily_my(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/daily_my - 查看我的订阅"""
        subs = context.chat_data.get("daily_subs", [])
        if not subs:
            await update.message.reply_text("📭 你还没有订阅任何早安简报。")
        else:
            msg = "📅 **我的早安订阅**：\n"
            for s in subs:
                msg += f"• {s}\n"
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def get_or_create_chart_file_id(self, bot, weather_data, chart_type: str) -> Optional[str]:
        """
        获取或创建图表的 file_id (带缓存)
        :param bot: Telegram Bot instance
        :param chart_type: 'temp' or 'rain'
        :return: file_id or None
        """
        from core.config import settings
        
        # 1. 尝试从缓存获取
        # key 包含位置和类型，确保唯一性
        cache_key = f"chart_v2:{weather_data.location_name}:{chart_type}"
        cached_id = await self.cache.get(cache_key)
        if cached_id:
            return cached_id
            
        # 2. 生成新图表
        if chart_type == "rain":
            img_bytes = Visualizer.draw_hourly_rain_chart(weather_data)
        else:
            img_bytes = Visualizer.draw_hourly_temp_chart(weather_data)
            
        if not img_bytes:
            return None
            
        # 3. 发送给管理员以获取 file_id
        if not settings.super_admin_id:
            logger.warning("No super_admin_id configured, cannot cache chart file_id for inline mode.")
            return None

        from telegram import InputFile
        try:
            # 静默发送
            msg = await bot.send_photo(
                chat_id=settings.super_admin_id,
                photo=InputFile(io.BytesIO(img_bytes), filename="chart.png"),
                disable_notification=True
            )
            file_id = msg.photo[-1].file_id
            
            # 4. 存入缓存 (TTL 30分钟)
            await self.cache.set(cache_key, file_id, ttl=1800)
            
            # 5. 删除临时消息
            try:
                await msg.delete()
            except:
                pass
                
            return file_id
        except Exception as e:
            logger.error(f"Failed to create chart file_id: {e}")
            return None

    async def chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/chart [城市] [daily|hourly|rain] → 发送趋势图"""
        args = context.args
        if not args:
            await update.message.reply_text(
                "⚠️ 用法：/chart 城市 [daily|hourly|rain]\n\n"
                "示例：\n"
                "• `/chart 北京` - 逐小时温度图\n"
                "• `/chart 上海 daily` - 逐日温度图\n"
                "• `/chart 广州 rain` - 逐小时降水概率",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        location = args[0]
        view_type = args[1].lower() if len(args) > 1 else "hourly"

        # 获取天气数据
        try:
            data = await self.weather_service.get_fused_weather(location)
        except Exception as e:
            logger.error(f"获取天气数据失败: {e}")
            await update.message.reply_text("❌ 系统繁忙，请稍后再试")
            return

        if not data:
            await update.message.reply_text("⚠️ 未获取到天气数据，请检查城市名称")
            return

        # 生成图表
        if view_type == "daily":
            img_bytes = Visualizer.draw_daily_temp_chart(data)
            chart_type = "逐日温度"
        elif view_type == "rain":
            img_bytes = Visualizer.draw_hourly_rain_chart(data)
            chart_type = "逐小时降水"
        else:
            img_bytes = Visualizer.draw_hourly_temp_chart(data)
            chart_type = "逐小时温度"

        if not img_bytes:
            await update.message.reply_text(f"⚠️ 暂无{chart_type}数据，无法绘制图表")
            return

        # 发送图片
        from telegram import InputFile
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=InputFile(io.BytesIO(img_bytes), filename=f"{location}_{view_type}.png"),
            caption=f"📈 {data.location_name} {chart_type}趋势"
        )

    async def handle_weather_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Main handler for weather requests.
        Supports: /tq [location] [param]
        """
        # 1. Parse Arguments & Determine Location
        location_query = None
        view_type = "default"
        start_day = 0
        limit = None
        
        if update.message.location:
            location_query = f"{update.message.location.longitude},{update.message.location.latitude}"
        
        elif context.args:
            raw_location = " ".join(context.args)
            
            if len(context.args) >= 2:
                potential_param = context.args[-1]
                v_type, v_start, v_limit = self.parse_query_param(potential_param)
                
                if v_type != "default":
                    # It IS a param
                    view_type = v_type
                    start_day = v_start
                    limit = v_limit
                    location_query = " ".join(context.args[:-1])
                else:
                    location_query = raw_location
            else:
                # Check single arg specific cases? 
                # If valid query param, but no city? No, tq requires city.
                # But "tq 北京" matches default.
                location_query = raw_location
                
        else:
            await update.message.reply_text("请提供城市名称或定位，例如 `/tq 北京`。")
            return
        
        if not location_query:
            return 

        # 2. React & Type
        try:
            await update.message.set_reaction("👀")
        except Exception:
            pass 
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        # 3. Fetch Data
        try:
            data = await self.weather_service.get_fused_weather(location_query)
        except Exception as e:
            logger.error(f"Error fetching weather: {e}")
            await update.message.reply_text("❌ 系统繁忙，请稍后再试。")
            return

        if not data:
            await update.message.reply_text("❌ 找不到该地区的天气数据，请检查拼写。")
            return

        # 4. Format & Visualize
        text = format_weather_response(data, view_type=view_type, days=limit, start_day=start_day)
        keyboard = get_weather_keyboard(location_query)
        
        # Check for rain chart (Only show chart if raining or specifically asked for rain)
        chart_bytes = None
        if settings.enable_weather_plots:
             should_plot = (view_type == "rain") or (data.is_raining)
             if should_plot:
                 chart_bytes = Visualizer.draw_rain_chart(data)

        # 5. Send Response
        try:
            if chart_bytes:
                sent_msg = await update.message.reply_photo(
                    photo=chart_bytes,
                    caption=text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard
                 )
            else:
                sent_msg = await update.message.reply_text(
                    text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=keyboard
                )
            
            
        except Exception as e:
            logger.error(f"Reply failed: {e}")
            await update.message.reply_text("❌ 发送失败，请重试。")

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle Button Clicks"""
        query = update.callback_query
        await query.answer()
        
        data_parts = query.data.split("|")
        action = data_parts[0]
        
        # 处理图表显示 (chart|location|type)
        if action == "chart":
            location = data_parts[1] if len(data_parts) > 1 else "北京"
            chart_type = data_parts[2] if len(data_parts) > 2 else "temp"
            
            try:
                weather_data = await self.weather_service.get_fused_weather(location)
            except Exception as e:
                logger.error(f"Chart data fetch error: {e}")
                return # 忽略错误，避免弹窗

            if not weather_data:
                return

            # 根据类型生成图表
            if chart_type == "rain":
                img_bytes = Visualizer.draw_hourly_rain_chart(weather_data)
                caption = f"🌧️ {weather_data.location_name} 逐小时降水"
            else:
                img_bytes = Visualizer.draw_hourly_temp_chart(weather_data)
                caption = f"📈 {weather_data.location_name} 逐小时温度"

            if not img_bytes:
                await query.answer("⚠️ 暂无数据", show_alert=True)
                return

            # 区分：Inline模式 vs 普通模式
            is_inline = (update.effective_message is None)
            
            from telegram import InputMediaPhoto, InputFile

            if is_inline:
                # Inline模式：直接原地变身 (Edit Message Media)
                # 附带 "返回" 按钮
                chart_keyboard = get_weather_keyboard(location, mode="chart")
                
                try:
                    await query.edit_message_media(
                        media=InputMediaPhoto(
                            media=InputFile(io.BytesIO(img_bytes), filename="chart.png"),
                            caption=caption
                        ),
                        reply_markup=chart_keyboard
                    )
                except Exception as e:
                    logger.error(f"Inline edit media failed: {e}")
                    # Telegram 不允许将 Inline 文本消息直接编辑为图片
                    await query.answer("⚠️ 抱歉，Inline 文本消息无法转换为图表。\n请直接使用 /chart 命令。", show_alert=True)
            else:
                # 普通模式：发送新图片 (保持原消息不动，方便对比)
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=InputFile(io.BytesIO(img_bytes), filename="chart.png"),
                    caption=caption
                    # 普通模式不需要返回按钮，因为原消息还在
                )
            return

        # 处理返回文本 (back|location) - 仅限 Inline 模式使用
        if action == "back":
            location = data_parts[1] if len(data_parts) > 1 else "北京"
            try:
                weather_data = await self.weather_service.get_fused_weather(location)
                if weather_data:
                    text = format_weather_response(weather_data)
                    keyboard = get_weather_keyboard(location)
                    
                    # 变回文本消息 (editMessageText 无法直接将 Photo 变回 Text吗？)
                    # Telegram 限制：editMessageMedia 可以把 Text 变 Photo，也可以 Photo 变 Photo。
                    # 但把 Photo 变回 Text 需要 editMessageText? 
                    # 不，如果当前是 Photo，调用 editMessageText 会报错吗？
                    # 实际上：如果是 Photo 消息，不能直接 edit_message_text 变成纯文本，必须用 edit_message_media 变成 InputMedia(type='text')? 不存在。
                    # 正确做法：EditCaption? 不，我们要去掉图片。
                    # 只有 editMessageMedia 可以改变类型。但 InputMedia 只有 Photo/Video/Animation/Audio/Document。没有 Text。
                    # 糟糕！Telegram API 不允许从 Photo 变回 Text。
                    
                    # workround: 重新编辑为 "InputMediaPhoto" 随着一个透明图？不行。
                    # 备选方案：Inline模式下不发图片，而是发图片链接？体验不好。
                    # 或者：Inline模式下也 "Send New Photo"？
                    # 但是用户问 "inlinemode发送的消息能带按钮吗"，是的。
                    # 如果不能变回文本，那么 "返回" 按钮就无法实现。
                    
                    # 让我们查一下文档。
                    # 确实，editMessageMedia cannot change to text.
                    # 所以：Inline模式下点击图表，必须忍受无法变回文本？
                    # 或者：我们在 Inline 模式下虽然显示 Text，但点击图表时，不仅仅是一个 button，而是一个 URL button (t.me/bot?start=chart_...)? 不。
                    
                    # 妥协方案：在 Inline 模式下，点击图表按钮，**发送一张新图片** 到当前聊天（如果Bot有权限）。
                    # 如果 Bot 是群成员，可以发送。如果只是 Inline 引用，Bot 可能没权说话。
                    # 那么 editMessageMedia 是唯一选择。
                    # 既然变不回去，那就把 "返回" 按钮改成 "🔄 刷新" (刷新图表) 或者去掉。
                    # 或者：InputMediaPhoto 可以是一个很小的透明图，利用 caption 显示长文本？
                    # 虽然有点 hacky，但可以试试。
                    # 但 caption 长度限制 1024。
                    # 天气文本通常可以容纳。
                    # 我们可以用一张 "天气概览" 的静态图 (如 icon 拼图) 作为 "Text 模式" 的替代？
                    # 或者就让它变成单向旅程：看图之后就停留在图。用户想看文字可以重新搜。
                    # 
                    # 改进：如果不能变回，那就在图表下方保留 "📈 温度" "🌧️ 降水" 按钮，让用户能在不同图表间切换，
                    # 并且加一个链接按钮 "查看文字详情" -> 跳转到 Bot 私聊？
                    
                    # 让我们先试试 edit_message_text。如果当前是 Photo，它会报错 "Bad Request: message is not a text message".
                    # 所以确实回不去。
                    
                    # 调整策略：
                    # Inline 模式下，点击图表按钮 -> 保持 Text 不变，直接通过 answerCallbackQuery(url=...) 弹出一个 WebApp? 太复杂。
                    # 或者：send_photo 到 chat。如果失败（没权限），则 answer("请在私聊中使用此功能或将Bot拉入群组", show_alert=True).
                    # 这是最稳妥的。不破坏原消息。
                    pass
            except Exception:
                pass
            
            # 由于不能完美“返回”，我们将策略改为：
            # Inline模式下，只尝试 send_photo。如果失败提示用户。
            # 这样原消息（文本）保留，用户可以看到新发出来的图。
            # 这是符合 Telegram 习惯的（Bot 回应 Inline 交互通常是发新消息）。
            pass

        # === 重新实现 chart action (Cache Optimized) ===
        if action == "chart":
            location = data_parts[1] if len(data_parts) > 1 else "北京"
            chart_type = data_parts[2] if len(data_parts) > 2 else "temp"
            
            try:
                weather_data = await self.weather_service.get_fused_weather(location)
            except Exception:
                return 

            if not weather_data: return

            # 1. 获取 file_id (优先尝试缓存/生成)
            file_id = await self.get_or_create_chart_file_id(context.bot, weather_data, chart_type)
            
            caption = f"🌧️ {weather_data.location_name} 逐小时降水" if chart_type == "rain" else f"📈 {weather_data.location_name} 逐小时温度"
            is_inline = (update.effective_message is None)
            from telegram import InputMediaPhoto, InputFile

            if file_id:
                # ✅ 成功获取 file_id (最佳路径)
                if is_inline:
                    # Inline 模式：使用 file_id 进行 Edit
                    chart_keyboard = get_weather_keyboard(location, mode="chart")
                    try:
                        await query.edit_message_media(
                            media=InputMediaPhoto(
                                media=file_id, # 直接传 file_id 字符串
                                caption=caption
                            ),
                            reply_markup=chart_keyboard
                        )
                    except Exception as e:
                        logger.error(f"Inline edit failed: {e}")
                        await query.answer("❌ 更新图表失败", show_alert=True)
                else:
                    # 普通模式：使用 file_id 发送 (秒发)
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=file_id,
                        caption=caption
                    )
            else:
                # ❌ 获取 file_id 失败 (例如没配 admin_id 或 生成失败)
                # 尝试降级：现场生成 bytes
                if chart_type == "rain":
                    img_bytes = Visualizer.draw_hourly_rain_chart(weather_data)
                else:
                    img_bytes = Visualizer.draw_hourly_temp_chart(weather_data)

                if not img_bytes:
                    await query.answer("⚠️ 暂无数据", show_alert=True)
                    return

                if is_inline:
                    # Inline 模式下无法上传 bytes，必须报错
                    await query.answer("⚠️系统配置错误：无法在Inline模式生成图表 (Missing Admin ID)", show_alert=True)
                else:
                    # 普通模式可以用 bytes
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=InputFile(io.BytesIO(img_bytes), filename="chart.png"),
                        caption=caption
                    )
            return
        
        # 原有的刷新和订阅逻辑
        location = data_parts[1] if len(data_parts) > 1 else None
        
        if action == "refresh" and location:
            try:
                weather_data = await self.weather_service.get_fused_weather(location)
                if weather_data:
                    is_inline = query.inline_message_id is not None
                    text = format_weather_response(weather_data) 
                    # Inline 模式下隐藏图表按钮
                    keyboard = get_weather_keyboard(location, show_charts=not is_inline)
                    
                    try:
                        # 只有非Inline消息才有 query.message
                        is_caption = False
                        if query.message:
                             is_caption = bool(query.message.caption)

                        if is_caption:
                            await query.edit_message_caption(
                                caption=text, 
                                parse_mode=ParseMode.MARKDOWN_V2,
                                reply_markup=keyboard
                            )
                        else:
                            await query.edit_message_text(
                                text=text,
                                parse_mode=ParseMode.MARKDOWN_V2,
                                reply_markup=keyboard
                            )
                        await query.answer("✅ 数据已更新")
                    except Exception as e:
                        if "Message is not modified" in str(e):
                            await query.answer("暂无新数据")
                        else:
                            logger.error(f"Refresh edit failed: {e}")
                            await query.answer("刷新失败", show_alert=True)
            except Exception as e:
                logger.error(f"Refresh failed: {e}")
                await query.answer("刷新出错", show_alert=True)
            return
                
        elif action == "sub" and location:
            # Inline模式下无法获取 Chat ID，无法订阅
            if update.callback_query.inline_message_id:
                await query.answer("⚠️ Inline模式无法订阅，请在与Bot私聊或群组中使用 /tq 后点击订阅。", show_alert=True)
                return

            subs = context.chat_data.get("subs", [])
            if location not in subs:
                subs.append(location)
                context.chat_data["subs"] = subs
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, 
                    text=f"✅ 已订阅降雨提醒: {location}"
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id, 
                    text=f"ℹ️ 你已经订阅了 {location}。"
                )

    async def handle_inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle inline queries
        支持：1) 位置+空查询 → 实时天气  2) 文本查询：@bot 北京 或 @bot 上海 3
        """
        from telegram import InlineQueryResultArticle, InputTextMessageContent
        from uuid import uuid4
        import asyncio

        query = update.inline_query.query.strip()
        
        # 优先检查用户是否分享了位置
        location_query = None
        if update.inline_query.location:
            lon = update.inline_query.location.longitude
            lat = update.inline_query.location.latitude
            location_query = f"{lon},{lat}"
            logger.info(f"Inline查询使用用户位置: {location_query}")
            
            # 如果有位置但没有文本，直接查询该位置天气
            if not query:
                query = None  # 标记为位置查询
        
        # 如果既没有位置也没有文本查询，显示提示
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
                        parse_mode=ParseMode.MARKDOWN
                    )
                )
            ]
            await update.inline_query.answer(results, cache_time=300, is_personal=True)
            return

        try:
            # 解析查询来源
            view_type = "default"
            days = None
            start_day = 0
            
            if query is None:
                # 位置查询，默认实时天气
                view_type = "default"
            else:
                # 解析文本输入
                parts = query.split(maxsplit=1)
                if not location_query:
                    location_query = parts[0]
                param = parts[1] if len(parts) > 1 else None
                
                if param:
                    view_type, start_day, days = self.parse_query_param(param)
            
            # 获取数据（带超时保护）
            data = await asyncio.wait_for(
                self.weather_service.get_fused_weather(location_query),
                timeout=8.0
            )
            
            if not data:
                results = [
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title="❌ 未找到数据",
                        description="请检查城市名称或网络连接",
                        input_message_content=InputTextMessageContent(
                            "⚠️ 无法获取天气数据\n请检查输入或稍后重试"
                        )
                    )
                ]
                await update.inline_query.answer(results, cache_time=10, is_personal=True)
                return
                
            # 格式化各种视图的文本
            text_default = format_weather_response(data, view_type="default")
            text_3d = format_weather_response(data, view_type="daily", days=3)
            text_7d = format_weather_response(data, view_type="daily", days=7)
            text_12h = format_weather_response(data, view_type="hourly", days=12)
            text_24h = format_weather_response(data, view_type="hourly", days=24)
            
            # 构建完整结果列表
            summary_short = data.summary.split('\n')[0] if '\n' in data.summary else data.summary
            # Inline模式下隐藏图表按钮，因为无法进行 Text->Photo 的切换
            keyboard = get_weather_keyboard(data.location_name, show_charts=False)
            
            results = [
                # 1. 实时天气（默认）
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"🌤️ {data.location_name} · 实时",
                    description=f"{data.now_text} {data.now_temp}°C · {summary_short[:35]}",
                    input_message_content=InputTextMessageContent(
                        text_default, 
                        parse_mode=ParseMode.MARKDOWN_V2
                    ),
                    reply_markup=keyboard
                ),
                # 2. 未来3天
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"📅 {data.location_name} · 未来3天",
                    description="详细逐日预报",
                    input_message_content=InputTextMessageContent(
                        text_3d,
                        parse_mode=ParseMode.MARKDOWN_V2
                    ),
                    reply_markup=keyboard
                ),
                # 3. 未来7天
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"📅 {data.location_name} · 未来7天",
                    description="一周天气趋势",
                    input_message_content=InputTextMessageContent(
                        text_7d,
                        parse_mode=ParseMode.MARKDOWN_V2
                    ),
                    reply_markup=keyboard
                ),
                # 4. 未来12小时
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"⏰ {data.location_name} · 未来12小时",
                    description="逐小时预报",
                    input_message_content=InputTextMessageContent(
                        text_12h,
                        parse_mode=ParseMode.MARKDOWN_V2
                    ),
                    reply_markup=keyboard
                ),
                # 5. 未来24小时
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title=f"⏰ {data.location_name} · 未来24小时",
                    description="全天逐小时预报",
                    input_message_content=InputTextMessageContent(
                        text_24h,
                        parse_mode=ParseMode.MARKDOWN_V2
                    ),
                    reply_markup=keyboard
                ),
            ]
            
            # 6. 降水预报（如果有数据）
            if data.minutely:
                text_rain = format_weather_response(data, view_type="rain")
                results.append(
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title=f"☔️ {data.location_name} · 降水预报",
                        description="分钟级降水趋势",
                        input_message_content=InputTextMessageContent(
                            text_rain,
                            parse_mode=ParseMode.MARKDOWN_V2
                        ),
                        reply_markup=keyboard
                    )
                )
            
            # 7. 生活指数（如果有数据）
            if data.indices:
                text_indices = format_weather_response(data, view_type="indices")
                results.append(
                    InlineQueryResultArticle(
                        id=str(uuid4()),
                        title=f"💡 {data.location_name} · 生活指数",
                        description="穿衣、洗车、运动等建议",
                        input_message_content=InputTextMessageContent(
                            text_indices,
                            parse_mode=ParseMode.MARKDOWN_V2
                        ),
                        reply_markup=keyboard
                    )
                )

            # 8. AI 天气日报 (Async Generation)
            # Sends a placeholder message. The Bot will detect "Chosen Result" and edit it.
            loading_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⏳ 生成中...", callback_data="noop")]])
            
            results.insert(1, 
                InlineQueryResultArticle(
                    id=f"ai_report:{data.location_name}",
                    title=f"🤖 {data.location_name} · AI 天气日报",
                    description="点击发送，Bot 将实时生成日报",
                    input_message_content=InputTextMessageContent(
                        f"⏳ 正在为 {data.location_name} 撰写 AI 天气日报...\n(Domo 正在思考 💭)",
                        parse_mode=None  # Disable Markdown to ensure safe delivery
                    ),
                    reply_markup=loading_keyboard  # Explicit simple keyboard
                )
            )
            
            # 返回结果
            await update.inline_query.answer(
                results, 
                cache_time=1, # Disable cache to ensure markup updates propagate
                is_personal=True
            )
            
        except asyncio.TimeoutError:
            logger.error("Inline查询超时")
            results = [
                InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="⏱️ 查询超时",
                    description="服务器响应过慢，请重试",
                    input_message_content=InputTextMessageContent(
                        "⚠️ 查询超时\n请稍后重试"
                    )
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
                    input_message_content=InputTextMessageContent(
                        f"系统繁忙，请稍后重试"
                    )
                )
            ]
            await update.inline_query.answer(results, cache_time=10, is_personal=True)


    async def handle_chosen_inline_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Handle 'chosen_inline_result' updates.
        Used for async generation of AI reports in Inline Mode.
        """
        result_id = update.chosen_inline_result.result_id
        inline_message_id = update.chosen_inline_result.inline_message_id
        
        logger.info(f"Received Chosen Inline Result: {result_id}, MsgID: {inline_message_id}")
        
        # Check if it's our AI Report trigger
        if not result_id.startswith("ai_report:"):
            logger.debug("Not an AI report trigger.")
            return
            
        location = result_id.split(":", 1)[1]
        
        # If user didn't share data, inline_message_id might be None? 
        if not inline_message_id:
            logger.error("No inline_message_id found to edit.")
            return

        try:
            # 1. Fetch Data
            # Note: We use simpler logic here assuming location name is valid or QWeather adapter handles it
            weather_data = await self.weather_service.get_fused_weather(location)
            if not weather_data:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"❌ 获取数据失败：{location}"
                )
                return

            # 2. Check LLM Config
            if not self.llm_service.provider:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text="⚠️ LLM 服务未配置，无法生成日报。"
                )
                return

            # 3. Generate Report (Non-Streaming)
            # Use non-streaming method to ensure robustness
            report_text = await self.llm_service.generate_weather_report(weather_data)
            
            # 4. Final Report Edit
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"📝 **{weather_data.location_name} 天气日报**\n\n{report_text}",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                # Fallback to Plain Text
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"📝 {weather_data.location_name} 天气日报\n\n{report_text}",
                    parse_mode=None
                )

        except Exception as e:
            logger.error(f"Async inline generation failed: {e}")
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text="❌ 生成失败，请稍后重试。"
                )
            except:
                pass

        except Exception as e:
            logger.error(f"Async inline generation failed: {e}")
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text="❌ 生成失败，请稍后重试。"
                )
            except:
                pass


def create_app() -> Application:
    """Factory to create the PTB Application"""
    
    # 1. Persistence
    import os
    os.makedirs("data", exist_ok=True)
    persistence = PicklePersistence(filepath="data/bot_data.pickle")
    
    # 2. Builder
    builder = Application.builder()
    builder.token(settings.bot_token)
    builder.persistence(persistence)
    
    app = builder.build()
    
    # 3. Handlers
    bot_logic = WeatherBot()
    
    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("tq", bot_logic.handle_weather_request))
    app.add_handler(CommandHandler("chart", bot_logic.chart))
    app.add_handler(CommandHandler("report", bot_logic.report))
    app.add_handler(CommandHandler("daily_sub", bot_logic.daily_sub))
    app.add_handler(CommandHandler("daily_unsub", bot_logic.daily_unsub))
    app.add_handler(CommandHandler("daily_my", bot_logic.daily_my))
    app.add_handler(MessageHandler(filters.LOCATION, bot_logic.handle_weather_request))
    app.add_handler(CallbackQueryHandler(bot_logic.handle_callback))
    app.add_handler(InlineQueryHandler(bot_logic.handle_inline_query))
    
    # NEW: Chosen Result Handler
    from telegram.ext import ChosenInlineResultHandler
    app.add_handler(ChosenInlineResultHandler(bot_logic.handle_chosen_inline_result))
    
    return app
