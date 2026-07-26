"""Per-subscription rain sensitivity: command, list UI, and push filtering.

Each subscribed city carries its own level, so two people (or two cities in one
chat) can share the same weather data and still get different decisions from it.
"""

import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core import scheduler
from core.config import settings
from core.handlers.subscriptions import (
    SubscriptionHandlers,
    remove_subscription_entry,
    render_subscription_list,
    set_rain_level,
)
from core.scheduler import DEFAULT_RAIN_LEVEL, RAIN_LEVELS
from domain.models import MinutelyPrecipitation, WeatherData


def rain_weather(rates_mm_per_5min) -> WeatherData:
    now = datetime.now()
    return WeatherData(
        location_name="北京",
        coords="116.4,39.9",
        now_temp=25,
        now_text="阴",
        now_icon="104",
        summary="s",
        minutely=[
            MinutelyPrecipitation(
                time=now + timedelta(minutes=5 * index),
                precip=value,
                precip_kind="amount",
                interval_minutes=5,
            )
            for index, value in enumerate(rates_mm_per_5min)
        ],
    )


class StubWeatherService:
    def __init__(self, weather):
        self.weather = weather
        self.calls = 0

    async def get_fused_weather(self, location, *, profile, refresh_qweather=False):
        self.calls += 1
        return self.weather


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages))

    async def send_photo(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages), photo=None)

    async def do_api_request(self, endpoint, api_kwargs=None, return_type=None):
        from telegram.error import EndPointNotFound

        raise EndPointNotFound(endpoint)


class CommandTests(unittest.IsolatedAsyncioTestCase):
    """/rain_sub 城市 [档位]"""

    def setUp(self):
        geo = SimpleNamespace(
            get_geo_location=self._geo,
        )
        deps = SimpleNamespace(weather_service=SimpleNamespace(qweather=geo), llm_service=None)
        self.handlers = SubscriptionHandlers(deps)
        self.bot = FakeBot()
        self.chat_data = {}
        self.context = SimpleNamespace(args=[], chat_data=self.chat_data, bot=self.bot)
        self.update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1, type="private"),
            effective_user=SimpleNamespace(id=7),
            effective_message=SimpleNamespace(message_thread_id=None),
            callback_query=None,
        )

    async def _geo(self, raw):
        return {"name": "北京", "adm1": "北京市", "tz": "Asia/Shanghai"}

    @property
    def last_text(self) -> str:
        return self.bot.messages[-1]["text"]

    async def test_plain_subscribe_uses_the_default_level(self):
        self.context.args = ["北京"]
        await self.handlers.rain_sub(self.update, self.context)
        self.assertEqual(self.chat_data["subs"], ["北京, 北京市"])
        # Default is implicit: no stored entry, but it is named in the reply.
        self.assertNotIn("rain_level", self.chat_data)
        self.assertIn(RAIN_LEVELS[DEFAULT_RAIN_LEVEL][0], self.last_text)

    async def test_level_word_is_stored_and_not_treated_as_the_city(self):
        self.context.args = ["北京", "仅大雨"]
        await self.handlers.rain_sub(self.update, self.context)
        self.assertEqual(self.chat_data["subs"], ["北京, 北京市"])
        self.assertEqual(self.chat_data["rain_level"], {"北京, 北京市": "heavy"})
        self.assertIn("仅大雨", self.last_text)

    async def test_resubscribing_with_a_level_changes_it_instead_of_refusing(self):
        self.context.args = ["北京"]
        await self.handlers.rain_sub(self.update, self.context)
        self.context.args = ["北京", "全部降雨"]
        await self.handlers.rain_sub(self.update, self.context)
        self.assertEqual(self.chat_data["subs"], ["北京, 北京市"])
        self.assertEqual(self.chat_data["rain_level"], {"北京, 北京市": "all"})
        self.assertIn("改为", self.last_text)

    async def test_second_word_that_is_not_a_level_stays_part_of_the_city(self):
        seen = []

        async def geo(raw):
            seen.append(raw)
            return {"name": "纽约", "adm1": None, "tz": "America/New_York"}

        self.handlers.deps.weather_service.qweather.get_geo_location = geo
        self.context.args = ["New", "York"]
        await self.handlers.rain_sub(self.update, self.context)
        self.assertEqual(seen, ["New York"])

    async def test_bare_command_opens_the_card_not_a_usage_wall(self):
        # No subscriptions yet: the empty-state guidance points at the button.
        await self.handlers.rain_sub(self.update, self.context)
        self.assertIn("降雨提醒", self.last_text)
        self.assertIn("城市名", self.last_text)

    async def test_bare_command_with_subscriptions_lists_levels_on_the_card(self):
        self.context.args = ["北京"]
        await self.handlers.rain_sub(self.update, self.context)
        self.context.args = []
        await self.handlers.rain_sub(self.update, self.context)
        for label, _rate, _pop, _hint in RAIN_LEVELS.values():
            self.assertIn(label, self.last_text)


class ListRenderingTests(unittest.TestCase):
    def setUp(self):
        self._quiet = settings.rain_alert_quiet_hours
        settings.rain_alert_quiet_hours = ""

    def tearDown(self):
        settings.rain_alert_quiet_hours = self._quiet

    def test_level_row_marks_the_active_choice(self):
        chat_data = {"subs": ["北京"], "rain_level": {"北京": "heavy"}}
        text, keyboard = render_subscription_list(chat_data, "rain")
        self.assertIn("仅大雨", text)
        level_buttons = [
            b
            for row in keyboard.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("lvl|")
        ]
        self.assertEqual(len(level_buttons), len(RAIN_LEVELS))
        checked = [b for b in level_buttons if b.text.startswith("✅")]
        self.assertEqual(len(checked), 1)
        self.assertIn("仅大雨", checked[0].text)
        self.assertEqual(checked[0].callback_data, "lvl|0|heavy")

    def test_callback_data_stays_within_the_telegram_budget(self):
        chat_data = {"subs": ["Llanfairpwllgwyngyllgogerychwyrndrobwllllantysiliogogogoch"]}
        _text, keyboard = render_subscription_list(chat_data, "rain")
        for row in keyboard.inline_keyboard:
            for button in row:
                self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64)

    def test_status_line_shows_last_alert_or_says_there_was_none(self):
        never = render_subscription_list({"subs": ["北京"]}, "rain")[0]
        self.assertIn("尚未提醒过", never)
        stamped = render_subscription_list(
            {"subs": ["北京"], "last_rain_alert": {"北京": datetime(2026, 7, 25, 18, 40)}}, "rain"
        )[0]
        self.assertIn("07-25 18:40", stamped)

    def test_quiet_hours_are_surfaced_so_silence_is_not_mistaken_for_breakage(self):
        zone = "Asia/Shanghai"
        now = datetime.now(ZoneInfo(zone))
        settings.rain_alert_quiet_hours = (
            f"{(now.hour - 1) % 24:02d}:00-{(now.hour + 1) % 24:02d}:00"
        )
        text = render_subscription_list({"subs": ["北京"], "sub_tz": {"北京": zone}}, "rain")[0]
        self.assertIn("免打扰", text)

    def test_set_level_rejects_bad_index_or_unknown_level(self):
        chat_data = {"subs": ["北京"]}
        self.assertIsNone(set_rain_level(chat_data, 5, "heavy"))
        self.assertIsNone(set_rain_level(chat_data, 0, "typhoon"))
        self.assertNotIn("rain_level", chat_data)
        self.assertEqual(set_rain_level(chat_data, 0, "all"), "北京")
        self.assertEqual(chat_data["rain_level"], {"北京": "all"})

    def test_unsubscribe_forgets_the_level(self):
        chat_data = {"subs": ["北京"], "rain_level": {"北京": "heavy"}}
        self.assertEqual(remove_subscription_entry(chat_data, "rain", 0), "北京")
        self.assertNotIn("北京", chat_data["rain_level"])


class PerSubscriptionPushTests(unittest.IsolatedAsyncioTestCase):
    """One weather fetch, different verdicts per subscriber."""

    def setUp(self):
        self._quiet = settings.rain_alert_quiet_hours
        settings.rain_alert_quiet_hours = ""

    def tearDown(self):
        settings.rain_alert_quiet_hours = self._quiet

    async def _run(self, weather, chat_data):
        service = StubWeatherService(weather)
        app = SimpleNamespace(chat_data=chat_data, bot_data={})
        context = SimpleNamespace(application=app, bot=FakeBot())
        await scheduler.check_rain_alerts(context, weather_service=service)
        return service, context.bot

    async def test_moderate_rain_reaches_only_the_sensitive_subscriber(self):
        chat_data = {
            1: {"subs": ["北京"]},                                  # 一般降雨
            2: {"subs": ["北京"], "rain_level": {"北京": "heavy"}},   # 仅大雨
        }
        service, bot = await self._run(rain_weather([0.0, 0.4]), chat_data)  # 4.8mm/h
        self.assertEqual(service.calls, 1, "shared location must be fetched once")
        self.assertEqual([m["chat_id"] for m in bot.messages], [1])

    async def test_a_downpour_reaches_everyone(self):
        chat_data = {
            1: {"subs": ["北京"]},
            2: {"subs": ["北京"], "rain_level": {"北京": "heavy"}},
        }
        _service, bot = await self._run(rain_weather([0.0, 1.5]), chat_data)  # 18mm/h
        self.assertEqual(sorted(m["chat_id"] for m in bot.messages), [1, 2])

    async def test_below_threshold_clears_that_subscribers_episode(self):
        # The 仅大雨 subscriber must not be locked out of a later burst just
        # because a moderate episode was open for someone else.
        chat_data = {
            1: {"subs": ["北京"]},
            2: {"subs": ["北京"], "rain_level": {"北京": "heavy"}},
        }
        await self._run(rain_weather([0.0, 0.4]), chat_data)
        self.assertFalse(chat_data[2].get("rain_episode", {}).get("北京"))
        _service, bot = await self._run(rain_weather([0.0, 1.5]), chat_data)
        self.assertIn(2, [m["chat_id"] for m in bot.messages])

    async def test_all_level_catches_drizzle_the_default_ignores(self):
        chat_data = {
            1: {"subs": ["北京"]},
            2: {"subs": ["北京"], "rain_level": {"北京": "all"}},
        }
        _service, bot = await self._run(rain_weather([0.0, 0.05]), chat_data)  # 0.6mm/h
        self.assertEqual([m["chat_id"] for m in bot.messages], [2])

    async def test_two_cities_in_one_chat_keep_separate_levels(self):
        chat_data = {1: {"subs": ["北京", "上海"], "rain_level": {"上海": "heavy"}}}
        _service, bot = await self._run(rain_weather([0.0, 0.4]), chat_data)
        self.assertEqual(len(bot.messages), 1)
        self.assertIn("北京", bot.messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
