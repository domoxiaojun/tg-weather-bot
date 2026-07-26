import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import settings
from core.handlers.common import parse_location_and_view, parse_query_param
from core.handlers.subscriptions import SubscriptionHandlers, subscription_limit_message
from core.scheduler import check_rain_alerts
from domain.models import WeatherData
from utils.schedule_times import is_within_quiet_hours, parse_quiet_hours


def weather(is_raining: bool) -> WeatherData:
    return WeatherData(
        location_name="北京",
        coords="116.4,39.9",
        now_temp=30,
        now_text="小雨" if is_raining else "晴",
        now_icon="305" if is_raining else "100",
        summary="s",
        is_raining=is_raining,
    )


class SwitchableWeatherService:
    def __init__(self):
        self.raining = True
        self.calls = 0

    async def get_fused_weather(self, location, *, profile, refresh_qweather=False):
        self.calls += 1
        return weather(self.raining)


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)

    async def send_photo(self, **kwargs):
        self.messages.append(kwargs)


class QuietHoursHelperTests(unittest.TestCase):
    def test_parse_valid_and_invalid(self):
        self.assertEqual(parse_quiet_hours("23:00-07:00"), ((23, 0), (7, 0)))
        for value in ("", None, "23:00", "23:00-24:00", "07:00-07:00", "night"):
            self.assertIsNone(parse_quiet_hours(value), value)

    def test_membership_crossing_midnight(self):
        window = parse_quiet_hours("23:00-07:00")
        from datetime import time as dtime

        self.assertTrue(is_within_quiet_hours(dtime(23, 30), window))
        self.assertTrue(is_within_quiet_hours(dtime(3, 0), window))
        self.assertFalse(is_within_quiet_hours(dtime(8, 0), window))

    def test_membership_same_day_window(self):
        window = parse_quiet_hours("12:00-14:00")
        from datetime import time as dtime

        self.assertTrue(is_within_quiet_hours(dtime(13, 0), window))
        self.assertFalse(is_within_quiet_hours(dtime(11, 59), window))
        self.assertFalse(is_within_quiet_hours(dtime(14, 0), window))


class RainEpisodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old_quiet = settings.rain_alert_quiet_hours
        settings.rain_alert_quiet_hours = ""

    def tearDown(self):
        settings.rain_alert_quiet_hours = self._old_quiet

    async def test_one_alert_per_episode_and_realert_after_reset(self):
        service = SwitchableWeatherService()
        bot = FakeBot()
        app = SimpleNamespace(chat_data={1: {"subs": ["北京"]}}, bot_data={})
        context = SimpleNamespace(application=app, bot=bot)

        # 第一轮：开始下雨 → 提醒一次
        await check_rain_alerts(context, weather_service=service)
        self.assertEqual(len(bot.messages), 1)
        self.assertTrue(app.bot_data["rain_state"])

        # 第二轮：仍在下雨 → 不重复提醒（即使冷却清空也不该发）
        app.chat_data[1]["last_rain_alert"] = {}
        await check_rain_alerts(context, weather_service=service)
        self.assertEqual(len(bot.messages), 1)

        # 第三轮：雨停 → 状态复位，不提醒
        service.raining = False
        await check_rain_alerts(context, weather_service=service)
        self.assertEqual(len(bot.messages), 1)

        # 第四轮：再次降雨且冷却已过 → 重新提醒
        service.raining = True
        app.chat_data[1]["last_rain_alert"] = {
            "北京": datetime.now() - timedelta(hours=settings.rain_alert_cooldown_hours + 1)
        }
        await check_rain_alerts(context, weather_service=service)
        self.assertEqual(len(bot.messages), 2)

    async def test_quiet_hours_skip_whole_check(self):
        now_local = datetime.now(ZoneInfo(settings.timezone))
        start = (now_local - timedelta(hours=1)).strftime("%H:%M")
        end = (now_local + timedelta(hours=1)).strftime("%H:%M")
        settings.rain_alert_quiet_hours = f"{start}-{end}"

        service = SwitchableWeatherService()
        bot = FakeBot()
        app = SimpleNamespace(chat_data={1: {"subs": ["北京"]}}, bot_data={})
        context = SimpleNamespace(application=app, bot=bot)

        await check_rain_alerts(context, weather_service=service)
        self.assertEqual(service.calls, 0)
        self.assertEqual(bot.messages, [])
        # 状态未被写入，免打扰结束后的下一轮检查会正常补上提醒
        self.assertEqual(app.bot_data.get("rain_state", {}), {})


class SubscriptionLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_rain_sub_rejects_beyond_limit(self):
        class FakeGeo:
            async def get_geo_location(self, raw):
                return {"name": raw, "adm1": "测试省"}

        deps = SimpleNamespace(
            weather_service=SimpleNamespace(qweather=FakeGeo()),
            llm_service=None,
        )
        handlers = SubscriptionHandlers(deps)
        bot = FakeBot()
        existing = [f"城市{i}, 测试省" for i in range(settings.max_subscriptions_per_chat)]
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_message=None,
        )
        context = SimpleNamespace(args=["新城"], chat_data={"subs": list(existing)}, bot=bot)

        await handlers.rain_sub(update, context)

        self.assertEqual(context.chat_data["subs"], existing)
        self.assertIn("最多订阅", bot.messages[-1]["text"])
        self.assertIn(str(settings.max_subscriptions_per_chat), subscription_limit_message("/rain_my"))


class RelativeDayParsingTests(unittest.TestCase):
    def test_relative_day_keywords(self):
        self.assertEqual(parse_query_param("明天"), ("daily", 1, 1))
        self.assertEqual(parse_query_param("后天"), ("daily", 2, 1))
        self.assertEqual(parse_query_param("今天"), ("daily", 0, 1))

    def test_location_with_relative_day(self):
        location, view, start_day, limit = parse_location_and_view(["北京", "明天"])
        self.assertEqual((location, view, start_day, limit), ("北京", "daily", 1, 1))


if __name__ == "__main__":
    unittest.main()
