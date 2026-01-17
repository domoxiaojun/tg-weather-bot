from typing import Optional, AsyncIterator
from abc import ABC, abstractmethod
import json
import asyncio
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

class LLMProvider(ABC):
    @abstractmethod
    async def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        """Generate a complete response from the LLM"""
        pass



class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "gpt-5"):
        self.client = openai.AsyncClient(api_key=api_key, base_url=base_url)
        self.model = model

    async def generate_report(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI API Error: {e}")
            raise



class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "gemini-2.5-flash"):
        self.api_key = api_key
        # Ensure base_url is set, defaulting to Google's official if None
        self.base_url = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")
        self.model = model
        self.client = httpx.AsyncClient(timeout=30.0)

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
            logger.error(f"Gemini Request Failed: {e}")
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
                model=settings.llm_model or "gpt-5"
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
                model=settings.llm_model or "gemini-2.5-flash"
            )
        else:
            logger.error(f"Unsupported LLM provider: {provider_type}")

    def _format_weather_data(self, data: WeatherData) -> str:
        """Convert WeatherData to a readable text summary for the LLM"""
        # Essential Info
        summary = {
            "location": data.location_name,
            "time": data.update_time.strftime("%m月%d日 %H:%M"),
            "current": {
                "temp": data.now_temp,
                "weather": data.now_text,
                "feels_like": data.now_feels_like,
                "humidity": data.now_humidity,
                "wind": f"{data.now_wind_dir} {data.now_wind_scale}级"
            },
            "alerts": [a.title for a in data.alerts] if data.alerts else [],
            "forecast_daily": [
                f"{d.date}: {d.text_day}/{d.text_night}, {d.temp_min}-{d.temp_max}°C" 
                for d in data.daily[:3]
            ],
            "forecast_hourly_next_6h": [
                f"{h.time}: {h.temp}°C, {h.text}, Rain Prob {h.pop}%" 
                for h in data.hourly[:6]
            ],
            "indices": {i.name: f"{i.category} ({i.text})" for i in data.indices[:3]} # e.g. Dressing, UV
        }
        return json.dumps(summary, ensure_ascii=False, indent=2)

    async def generate_weather_report(self, data: WeatherData) -> str:
        if not self.provider:
            return "⚠️ LLM服务未配置或API Key缺失。"

        system_prompt = self._build_system_prompt()
        user_prompt = self._format_weather_data(data)
        
        try:
            logger.info(f"Generating LLM report for {data.location_name} using {settings.llm_provider}...")
            text = await asyncio.wait_for(
                self.provider.generate_report(system_prompt, user_prompt), 
                timeout=30.0
            )
            text += f"\n\n🤖 Generated by {self.provider.model}"
            return text
            
        except asyncio.TimeoutError:
            logger.error("LLM Generation Timed Out (30s)")
            return "⏱️ AI 响应超时 (30s)，请稍后重试。"
        except Exception as e:
            logger.error(f"LLM Generation Failed: {e}")
            return f"🤖 生成日报失败: {e}"

    def _build_system_prompt(self) -> str:
        return (
            "你是一个幽默、风趣且贴心的天气预报员助手。你的名字叫'Domo'。"
            "请根据提供的 JSON 天气数据，为用户生成一份简短、易读且有趣的天气日报。"
            "要求："
            "1. **核心信息及其突出**：标题下方必须包含 **当前时间**（从JSON数据获取）。"
            "2. **穿衣与出行建议**：根据指数和天气情况给出人性化建议。"
            "3. **幽默感**：适当调侃天气，或者用可爱的语气，但不要过度啰嗦。"
            "4. **排版美观 - Telegram Markdown 严格规则**："
            "   - 加粗：*必须* 使用 *单个星号* 包裹，例如：*重点内容*"
            "   - 斜体：使用 _下划线_ 包裹，例如：_斜体文本_"
            "   - ❌ 禁止使用：**双星号** (这会导致解析错误)"
            "   - ❌ 禁止使用：`反引号` (除非是代码)"
            "   - 示例正确格式：今天的温度是 *15°C*，体感 _略冷_。"
            "5. **长度控制**：控制在 300 字左右，适合手机快速阅读。"
            "6. 如果有预警信息，必须在最开始使用 *加粗* 强调。"
            "7. **结尾**：必须包含一句暖心的祝福。"
        )
