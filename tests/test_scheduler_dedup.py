import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import settings
from core.scheduler import check_rain_alerts, dispatch_daily_briefs
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
        now_hhmm = datetime.now(ZoneInfo(settings.timezone)).strftime("%H:%M")
        app = SimpleNamespace(
            chat_data={
                1: {"daily_subs": ["北京"], "daily_sub_times": {"北京": now_hhmm}},
                2: {"daily_subs": ["北京"], "daily_sub_times": {"北京": now_hhmm}},
            }
        )
        context = SimpleNamespace(application=app, bot=bot)

        await dispatch_daily_briefs(
            context,
            weather_service=weather_service,
            llm_service=llm_service,
        )

        self.assertEqual(weather_service.calls, [("北京", "full", False)])
        self.assertEqual(llm_service.calls, 1)
        self.assertEqual({message["chat_id"] for message in bot.messages}, {1, 2})
        # Same-day duplicates are guarded by the per-chat last-sent marker.
        self.assertIn("北京", app.chat_data[1]["daily_brief_last_sent"])

        await dispatch_daily_briefs(
            context,
            weather_service=weather_service,
            llm_service=llm_service,
        )
        self.assertEqual(llm_service.calls, 1)

    async def test_rain_check_fetches_once_and_fans_out(self):
        old_quiet = settings.rain_alert_quiet_hours
        settings.rain_alert_quiet_hours = ""
        try:
            weather_service = FakeWeatherService()
            bot = FakeBot()
            app = SimpleNamespace(
                chat_data={
                    1: {"subs": ["北京"]},
                    2: {"subs": ["北京"]},
                },
                bot_data={},
            )
            context = SimpleNamespace(application=app, bot=bot)

            await check_rain_alerts(context, weather_service=weather_service)

            self.assertEqual(weather_service.calls[0][:2], ("北京", "rain"))
            self.assertEqual(len(weather_service.calls), 1)
            self.assertEqual({message["chat_id"] for message in bot.messages}, {1, 2})
            self.assertIn("北京", app.chat_data[1]["last_rain_alert"])
            self.assertIn("北京", app.chat_data[2]["last_rain_alert"])
        finally:
            settings.rain_alert_quiet_hours = old_quiet


if __name__ == "__main__":
    unittest.main()
