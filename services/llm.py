from typing import Optional, Any
from abc import ABC, abstractmethod
import json
import asyncio
import re
import time
from pathlib import Path
from html import escape as html_escape, unescape as html_unescape
from loguru import logger

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    logger.warning("Failed to import 'openai'. OpenAI features disabled.")

import httpx

# Gemini now uses httpx (no SDK required)
HAS_GEMINI = True

from core.config import settings
from domain.models import WeatherData


DEFAULT_WEATHER_REPORT_PROMPT = (
    "你是 Domo，一个会读天气数据、会排版、语气轻松但判断严谨的中文天气助手。"
    "你会收到结构化 JSON 天气数据，请生成适合 Telegram 展示的中文天气日报正文。\n\n"
    "输出边界：\n"
    "1. 只输出正文，不要输出标题，不要写“天气日报”“Domo天气日报”“城市｜天气日报”等标题；Bot 外层已经添加标题。\n"
    "2. 第一行必须是：当前时间：MM月DD日 HH:MM，时间只能使用 JSON 的 updated_at。\n"
    "3. 只使用 Telegram HTML：允许 <b> 和 <i>，禁止 Markdown 符号（**、_、`、#）。\n"
    "4. 必须自己完成排版，使用空行分隔信息块；每个信息块开头必须有贴合内容的 emoji。\n\n"
    "推荐版式：\n"
    "当前时间：MM月DD日 HH:MM\n\n"
    "🌤️ <b>现在</b>\n"
    "用 1-2 句概括当前天气、体感、湿度、风、空气质量，只保留对用户有用的数字。\n\n"
    "⏱️ <b>接下来</b>\n"
    "用 1-2 句说明未来 2-6 小时趋势、今晚/明天变化；有雨说清时间和强度，没雨不要强行提醒带伞。\n\n"
    "👕 <b>建议</b>\n"
    "用 2-3 个短建议覆盖穿衣、出行/运动、防晒/健康；必须基于 life_indices、risk_signals、空气质量和预警。\n\n"
    "🌈 最后一行给一句简短收尾，不要鸡汤过度。\n\n"
    "事实规则：\n"
    "1. 优先依据 risk_signals、预警、分钟级降水、逐小时预报、空气质量和生活指数；不要编造 JSON 没有的数据。\n"
    "2. 如果 risk_signals.avoid_unfounded_rain_advice 为 true，不要提醒带伞、洗车会被雨打湿或今晚下雨，除非日预报明确有雨。\n"
    "3. 如果有预警，必须在当前时间之后第一块用 ⚠️ <b>预警</b> 单独突出。\n"
    "4. 控制在 260-420 个中文字符；可幽默，但每个玩笑都要服务于天气判断。"
)

class LLMProvider(ABC):
    @abstractmethod
    async def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a complete response from the LLM"""
        pass



class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "gpt-5.5",
        api_mode: str = "responses",
        reasoning_effort: str = "medium",
        verbosity: str = "medium",
        temperature: Optional[float] = None,
        timeout_seconds: float = 40.0,
        max_output_tokens: Optional[int] = 900,
    ):
        self.client = openai.AsyncClient(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds)),
        )
        self.model = model
        self.api_mode = api_mode
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    async def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        if self.api_mode == "chat_completions":
            return await self._generate_report_chat_completions(system_prompt, user_prompt)
        return await self._generate_report_responses(system_prompt, user_prompt)

    async def _generate_report_responses(self, system_prompt: str, user_prompt: str) -> str:
        try:
            request_kwargs = {
                "model": self.model,
                "instructions": system_prompt,
                "input": user_prompt,
                "reasoning": {"effort": self.reasoning_effort},
                "text": {"verbosity": self.verbosity},
            }
            if self.temperature is not None:
                request_kwargs["temperature"] = self.temperature
            if self.max_output_tokens is not None:
                request_kwargs["max_output_tokens"] = self.max_output_tokens

            response = await self.client.responses.create(**request_kwargs)
            return self._extract_responses_text(response)
        except Exception as e:
            logger.error(f"OpenAI Responses API Error: {e}")
            raise

    async def _generate_report_chat_completions(self, system_prompt: str, user_prompt: str) -> str:
        try:
            request_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "reasoning_effort": self.reasoning_effort,
                "verbosity": self.verbosity,
            }
            if self.temperature is not None:
                request_kwargs["temperature"] = self.temperature
            if self.max_output_tokens is not None:
                request_kwargs["max_completion_tokens"] = self.max_output_tokens

            response = await self.client.chat.completions.create(**request_kwargs)
            return response.choices[0].message.content or ""
        except TypeError as e:
            logger.error(
                "OpenAI Chat Completions parameter error. "
                "Upgrade the openai package or set OPENAI_API_MODE=responses. "
                f"Original error: {e}"
            )
            raise
        except Exception as e:
            logger.error(f"OpenAI Chat Completions API Error: {e}")
            raise

    def _extract_responses_text(self, response: Any) -> str:
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        response_dict = response if isinstance(response, dict) else None
        if response_dict and response_dict.get("output_text"):
            return response_dict["output_text"]

        output = getattr(response, "output", None)
        if output is None and response_dict:
            output = response_dict.get("output", [])

        text_parts = []
        for item in output or []:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, dict):
                content = item.get("content", [])

            for content_item in content or []:
                text = getattr(content_item, "text", None)
                if text is None and isinstance(content_item, dict):
                    text = content_item.get("text")
                if text:
                    text_parts.append(text)

        return "\n".join(text_parts).strip()



class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        # Ensure base_url is set, defaulting to Google's official if None
        self.base_url = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        self.model = model
        # 增加超时：总超时60秒，连接超时10秒
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        payload = self._build_payload(system_prompt, user_prompt)
        params = {"key": self.api_key}
        headers = {"Content-Type": "application/json"}

        try:
            response = await self.client.post(url, json=payload, params=params, headers=headers)
            if response.status_code != 200:
                logger.error(f"Gemini API Error {response.status_code}: {response.text}")
                raise Exception(f"HTTP {response.status_code} - {response.text[:100]}")
            
            data = response.json()
            return self._extract_text(data)
        except Exception as e:
            # 改进错误日志：显示异常类型和详细信息
            error_msg = f"{type(e).__name__}: {str(e)}" if str(e) else type(e).__name__
            logger.error(f"Gemini Request Failed: {error_msg}")
            raise



    def _build_payload(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "contents": [{
                "parts": [{"text": f"{system_prompt}\n\nUser Data:\n{user_prompt}"}]
            }]
        }

    def _extract_text(self, data: dict) -> str:
        try:
            # 1. Google Gemini Format
            if "candidates" in data:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            
            # 2. OpenAI Format (Proxy Adaptation)
            if "choices" in data:
                choice = data["choices"][0]
                # Stream delta
                if "delta" in choice:
                     ct = choice["delta"].get("content", "")
                     return ct if ct else ""
                # Non-stream message
                if "message" in choice:
                     return choice["message"].get("content", "")
            
            return ""
        except (KeyError, IndexError, TypeError):
            return ""

class LLMService:
    def __init__(self):
        self.provider: Optional[LLMProvider] = None
        self._setup_provider()

    def _setup_provider(self):
        provider_type = settings.llm_provider.lower()
        
        if provider_type == "openai":
            if not HAS_OPENAI:
                logger.error("OpenAI library not installed.")
                return
            if not settings.openai_api_key:
                logger.warning("OpenAI API Key is missing. LLM features will be disabled.")
                return
            self.provider = OpenAIProvider(
                api_key=settings.openai_api_key, 
                base_url=settings.openai_api_base,
                model=settings.openai_model or settings.llm_model or "gpt-5.5",
                api_mode=settings.openai_api_mode,
                reasoning_effort=settings.openai_reasoning_effort,
                verbosity=settings.openai_verbosity,
                temperature=settings.openai_temperature,
                timeout_seconds=settings.openai_timeout_seconds,
                max_output_tokens=settings.openai_max_output_tokens,
            )
        elif provider_type == "gemini":
            if not HAS_GEMINI:
                logger.error("Google GenerativeAI library not installed.")
                return
            if not settings.gemini_api_key:
                logger.warning("Gemini API Key is missing. LLM features will be disabled.")
                return
            self.provider = GeminiProvider(
                api_key=settings.gemini_api_key,
                base_url=settings.gemini_api_base,
                model=settings.gemini_model or settings.llm_model or "gemini-2.5-flash"
            )
        else:
            logger.error(f"Unsupported LLM provider: {provider_type}")

    @staticmethod
    def _round_number(value: Any, digits: int = 1) -> Any:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return round(value, digits)
        return value

    @staticmethod
    def _time_text(value, fmt: str = "%H:%M") -> Optional[str]:
        if not value:
            return None
        return value.strftime(fmt)

    @staticmethod
    def _date_text(value) -> Optional[str]:
        if not value:
            return None
        return value.strftime("%m月%d日")

    @staticmethod
    def _numeric_text_to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            match = re.search(r"-?\d+(?:\.\d+)?", str(value))
            return float(match.group(0)) if match else None

    def _build_risk_signals(self, data: WeatherData) -> dict:
        minutely_next_60m = data.minutely[:12]
        hourly_next_6h = data.hourly[:6]
        minutely_precip_total = round(sum(item.precip for item in minutely_next_60m), 2)
        hourly_precip_total = round(sum(hour.precip for hour in hourly_next_6h), 2)
        max_hourly_pop = max((hour.pop for hour in hourly_next_6h if hour.pop is not None), default=0)

        today = data.daily[0] if data.daily else None
        today_text = f"{today.text_day}/{today.text_night}" if today else ""
        next_two_days_have_rain = any(
            day.precip > 0 or "雨" in f"{day.text_day}{day.text_night}"
            for day in data.daily[:2]
        )
        rain_soon = (
            data.is_raining
            or data.now_precip > 0
            or minutely_precip_total > 0
            or hourly_precip_total > 0
            or max_hourly_pop >= 50
        )

        uv_value = self._numeric_text_to_float(today.uv_index if today else None)
        heat_signal = data.now_temp >= 32 or data.now_feels_like >= 33

        return {
            "rain_now_or_soon": rain_soon,
            "rain_expected_today_or_tomorrow": rain_soon or next_two_days_have_rain,
            "avoid_unfounded_rain_advice": not (rain_soon or next_two_days_have_rain),
            "next_60m_precip_total_mm": minutely_precip_total,
            "next_6h_precip_total_mm": hourly_precip_total,
            "next_6h_max_pop_pct": max_hourly_pop,
            "heat_signal": heat_signal,
            "high_uv_signal": uv_value is not None and uv_value >= 5,
            "current_weather_text": data.now_text,
            "today_weather_text": today_text,
            "has_alerts": bool(data.alerts),
        }

    def _format_weather_data(self, data: WeatherData) -> str:
        """Convert WeatherData to a readable text summary for the LLM"""
        priority_indices = {"3", "1", "2", "5", "9", "8", "7", "10", "15", "16"}
        selected_indices = [
            index for index in data.indices
            if index.type in priority_indices
        ][:10]

        payload = {
            "report_contract": {
                "output_scope": "body_only",
                "title_is_added_by_bot": True,
                "do_not_output_title": True,
                "telegram_parse_mode": "HTML",
            },
            "location": {
                "name": data.location_name,
                "coords": data.coords,
                "source": data.source,
            },
            "updated_at": data.update_time.strftime("%m月%d日 %H:%M"),
            "current": {
                "temp_c": self._round_number(data.now_temp),
                "feels_like_c": self._round_number(data.now_feels_like),
                "weather": data.now_text,
                "wind": f"{data.now_wind_dir} {data.now_wind_scale}级".strip(),
                "humidity_pct": data.now_humidity,
                "precip_mm": self._round_number(data.now_precip, 2),
                "pressure_hpa": data.now_pressure,
                "visibility_km": self._round_number(data.now_vis),
            },
            "risk_signals": self._build_risk_signals(data),
            "summary_from_weather_api": data.summary,
            "air_quality": {
                "aqi": data.air_quality.aqi,
                "category": data.air_quality.category,
                "primary_pollutant": data.air_quality.primary,
                "pm2_5": self._round_number(data.air_quality.pm2p5),
                "advice": data.air_quality.description,
            } if data.air_quality else None,
            "alerts": [
                {
                    "title": alert.title,
                    "type": alert.type,
                    "level": alert.level,
                    "published_at": alert.pub_time.strftime("%m月%d日 %H:%M"),
                    "source": alert.source,
                    "description": alert.text[:300],
                }
                for alert in data.alerts[:3]
            ],
            "minutely_next_60m": [
                {
                    "time": self._time_text(item.time),
                    "precip_mm": self._round_number(item.precip, 2),
                    "probability_pct": (
                        round(item.probability * 100)
                        if item.probability is not None
                        else None
                    ),
                    "type": item.precip_type,
                }
                for item in data.minutely[:12]
            ],
            "hourly_next_12h": [
                {
                    "time": self._time_text(hour.time),
                    "temp_c": self._round_number(hour.temp),
                    "weather": hour.text,
                    "pop_pct": hour.pop,
                    "precip_mm": self._round_number(hour.precip, 2),
                    "humidity_pct": hour.humidity,
                    "wind": f"{hour.wind_dir} {hour.wind_scale}级".strip(),
                    "pressure_hpa": self._round_number(hour.pressure),
                    "cloud_pct": hour.cloud,
                }
                for hour in data.hourly[:12]
            ],
            "daily_forecast": [
                {
                    "date": self._date_text(day.date),
                    "day_weather": day.text_day,
                    "night_weather": day.text_night,
                    "temp_min_c": self._round_number(day.temp_min),
                    "temp_max_c": self._round_number(day.temp_max),
                    "precip_mm": self._round_number(day.precip, 2),
                    "humidity_pct": day.humidity,
                    "visibility_km": self._round_number(day.vis),
                    "uv_index": day.uv_index,
                    "sunrise": day.sunrise,
                    "sunset": day.sunset,
                    "moon_phase": day.moon_phase,
                    "day_wind": f"{day.wind_dir_day or ''} {day.wind_scale_day or ''}级".strip(),
                    "night_wind": f"{day.wind_dir_night or ''} {day.wind_scale_night or ''}级".strip(),
                }
                for day in data.daily[:5]
            ],
            "life_indices": [
                {
                    "type": index.type,
                    "name": index.name,
                    "category": index.category,
                    "text": index.text,
                }
                for index in selected_indices
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    async def generate_weather_report(self, data: WeatherData) -> str:
        """Generate a complete AI weather report"""
        if not self.provider:
            logger.warning("No LLM Provider Configured")
            return "⚠️ AI 天气日报功能尚未配置"

        system_prompt = self._build_system_prompt()
        user_prompt = self._format_weather_data(data)
        started_at = time.perf_counter()
        
        try:
            timeout_seconds = settings.llm_report_timeout_seconds
            logger.info(
                "Generating LLM report for {} using {} model={} timeout={}s...",
                data.location_name,
                settings.llm_provider,
                self.provider.model,
                timeout_seconds,
            )
            text = await asyncio.wait_for(
                self.provider.generate_report(system_prompt, user_prompt), 
                timeout=timeout_seconds,
            )
            elapsed = time.perf_counter() - started_at
            logger.info(
                "LLM report generated for {} in {:.1f}s ({} chars).",
                data.location_name,
                elapsed,
                len(text),
            )
            
            # 清理HTML标签确保兼容性，并移除模型可能误加的标题。
            text = self._fix_telegram_html(text)
            text = self._strip_report_title(text, data.location_name)
            
            text += f"\n\n🤖 Generated by {self.provider.model}"
            return text
            
        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - started_at
            logger.error(
                "LLM Generation Timed Out for {} after {:.1f}s (limit={}s).",
                data.location_name,
                elapsed,
                settings.llm_report_timeout_seconds,
            )
            return "⏱️ AI 响应超时，请稍后重试。"
        except Exception as e:
            # 改进错误日志：显示异常类型和详细信息
            elapsed = time.perf_counter() - started_at
            error_msg = f"{type(e).__name__}: {str(e)}" if str(e) else type(e).__name__
            logger.error(f"LLM Generation Failed after {elapsed:.1f}s: {error_msg}")
            return f"❌ 生成日报失败: {error_msg}"

    def _build_system_prompt(self) -> str:
        if settings.llm_weather_report_prompt:
            return settings.llm_weather_report_prompt

        if settings.llm_weather_report_prompt_file:
            prompt_path = Path(settings.llm_weather_report_prompt_file)
            try:
                prompt = prompt_path.read_text(encoding="utf-8").strip()
                if prompt:
                    return prompt
            except Exception as e:
                logger.warning(f"Failed to read LLM_WEATHER_REPORT_PROMPT_FILE={prompt_path}: {e}")

        return DEFAULT_WEATHER_REPORT_PROMPT

    def _strip_report_title(self, text: str, location_name: str) -> str:
        """Remove title-like lines if the model ignores the body-only contract."""
        lines = text.strip().splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)

        location_compact = re.sub(r"[\s,，]+", "", location_name)
        stripped_count = 0
        while lines and stripped_count < 3:
            raw_line = lines[0].strip()
            plain_line = re.sub(r"<[^>]+>", "", raw_line)
            plain_line = re.sub(r"^[#\s>|｜:：\-—·•🤖☀️🌤️]+", "", plain_line).strip()
            compact = re.sub(r"[\s,，|｜:：\-—·•]+", "", plain_line)

            is_title = (
                "天气日报" in compact
                or compact == location_compact
                or compact == f"{location_compact}日报"
                or compact.startswith("Domo天气")
            )
            if not is_title:
                break
            lines.pop(0)
            stripped_count += 1

        return "\n".join(lines).strip()
    
    def _fix_telegram_html(self, text: str) -> str:
        """
        清理和修复HTML标签以确保Telegram兼容性。
        Fixes:
        - 移除可能残留的Markdown符号
        - 清理不支持的HTML标签
        """
        # Fix 1: 移除可能残留的Markdown符号（防止LLM误用）
        # 如果LLM不小心使用了**或__，转换为HTML
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
        text = re.sub(r'\*(.+?)\*', r'<b>\1</b>', text)
        text = re.sub(r'_(.+?)_', r'<i>\1</i>', text)
        
        # Fix 2: 移除反引号（Telegram HTML不需要）
        text = re.sub(r'`([^`]+)`', r'\1', text)

        # Fix 3: 只保留 Telegram 支持的基础标签，其他 HTML 符号按普通文本显示。
        placeholders = {
            "__DOMO_B_OPEN__": "<b>",
            "__DOMO_B_CLOSE__": "</b>",
            "__DOMO_I_OPEN__": "<i>",
            "__DOMO_I_CLOSE__": "</i>",
        }
        text = re.sub(r"<\s*b\s*>", "__DOMO_B_OPEN__", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*b\s*>", "__DOMO_B_CLOSE__", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*i\s*>", "__DOMO_I_OPEN__", text, flags=re.IGNORECASE)
        text = re.sub(r"<\s*/\s*i\s*>", "__DOMO_I_CLOSE__", text, flags=re.IGNORECASE)
        text = html_escape(html_unescape(text), quote=False)
        for placeholder, tag in placeholders.items():
            text = text.replace(placeholder, tag)

        return text.strip()

