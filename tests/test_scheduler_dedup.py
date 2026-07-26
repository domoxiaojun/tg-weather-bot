import os
import unittest
from types import SimpleNamespace


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.scheduler import check_rain_alerts, send_daily_brief
from domain.models import WeatherData


def raining_weather():
    return WeatherData(
        location_name="北京",
        coords="116.4,39.9",
        now_temp=30,
        now_text="小雨",
        now_icon="305",
        summary="当前有雨",
        is_raining=True,
    )


class FakeWeatherService:
    def __init__(self):
        self.calls = []

    async def get_fused_weather(self, location, *, profile, refresh_qweather=False):
        self.calls.append((location, profile, refresh_qweather))
        return raining_weather()


class FakeLLMService:
    def __init__(self):
        self.calls = 0

    async def generate_weather_report(self, weather):
        self.calls += 1
        return "测试日报"


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class SchedulerDeduplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_brief_fetches_and_generates_once_per_location(self):
        weather_service = FakeWeatherService()
        llm_service = FakeLLMService()
        bot = FakeBot()
        app = SimpleNamespace(
            chat_data={
                1: {"daily_subs": ["北京"]},
                2: {"daily_subs": ["北京"]},
            }
        )
        context = SimpleNamespace(application=app, bot=bot)

        await send_daily_brief(
            context,
            weather_service=weather_service,
            llm_service=llm_service,
        )

        self.assertEqual(weather_service.calls, [("北京", "full", False)])
        self.assertEqual(llm_service.calls, 1)
        self.assertEqual({message["chat_id"] for message in bot.messages}, {1, 2})

    async def test_rain_check_fetches_once_and_fans_out(self):
        weather_service = FakeWeatherService()
        bot = FakeBot()
        app = SimpleNamespace(
            chat_data={
                1: {"subs": ["北京"]},
                2: {"subs": ["北京"]},
            }
        )
        context = SimpleNamespace(application=app, bot=bot)

        await check_rain_alerts(context, weather_service=weather_service)

        self.assertEqual(weather_service.calls, [("北京", "rain", False)])
        self.assertEqual({message["chat_id"] for message in bot.messages}, {1, 2})
        self.assertIn("北京", app.chat_data[1]["last_rain_alert"])
        self.assertIn("北京", app.chat_data[2]["last_rain_alert"])


if __name__ == "__main__":
    unittest.main()
