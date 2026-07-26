"""Tests for the push paths that fail silently in production.

Covers official warning pushes, derived threshold events, per-location
timezone scheduling, daily-brief catch-up and persistence backups.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core import scheduler
from core.config import settings
from domain.models import AirQuality, DailyForecast, HourlyForecast, WarningAlert, WeatherData
from utils.persistence_backup import rotate_persistence_backups

TZ = timezone(timedelta(hours=8))


def make_weather(*, alerts=None, aqi=None, temp_max=30.0, temp_min=20.0, wind_scale="3") -> WeatherData:
    now = datetime(2026, 7, 26, 16, 0, tzinfo=TZ)
    return WeatherData(
        source="qweather",
        location_name="北京, 北京市",
        coords="116.4,39.9",
        now_temp=30,
        now_text="晴",
        now_icon="100",
        summary="s",
        update_time=now,
        hourly=[
            HourlyForecast(
                time=now + timedelta(hours=i),
                temp=28,
                text="晴",
                icon="100",
                wind_scale=wind_scale,
            )
            for i in range(12)
        ],
        daily=[
            DailyForecast(
                date=datetime(2026, 7, 26) + timedelta(days=i),
                temp_min=temp_min,
                temp_max=temp_max,
                text_day="晴",
                icon_day="100",
                text_night="晴",
                icon_night="150",
            )
            for i in range(3)
        ],
        air_quality=aqi,
        alerts=alerts or [],
    )


def alert(title="暴雨预警", level="橙色", issued=None, alert_id="A1") -> WarningAlert:
    return WarningAlert(
        title=title,
        type="rain",
        level=level,
        text="请注意防范",
        pub_time=issued or datetime(2026, 7, 26, 15, 0, tzinfo=TZ),
        source="QWeather",
        alert_id=alert_id,
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
        # Pretend the server has no rich support so pushes take the text path.
        from telegram.error import EndPointNotFound

        raise EndPointNotFound(endpoint)


def make_context(chat_data):
    app = SimpleNamespace(chat_data=chat_data, bot_data={})
    return SimpleNamespace(application=app, bot=FakeBot()), app


class WarningPushTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._quiet = settings.rain_alert_quiet_hours
        self._derived = settings.enable_derived_event_alerts
        settings.rain_alert_quiet_hours = ""
        settings.enable_derived_event_alerts = False

    def tearDown(self):
        settings.rain_alert_quiet_hours = self._quiet
        settings.enable_derived_event_alerts = self._derived

    async def test_official_warning_is_pushed_once_per_revision(self):
        service = StubWeatherService(make_weather(alerts=[alert()]))
        context, app = make_context({1: {"subs": ["北京, 北京市"]}})

        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(len(context.bot.messages), 1)
        self.assertIn("暴雨预警", context.bot.messages[0]["text"])

        # Same warning again → silent.
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(len(context.bot.messages), 1)

        # Re-issued with a new publish time → alerts again.
        service.weather = make_weather(
            alerts=[alert(issued=datetime(2026, 7, 26, 18, 0, tzinfo=TZ))]
        )
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(len(context.bot.messages), 2)

    async def test_state_is_cleared_when_warning_expires(self):
        service = StubWeatherService(make_weather(alerts=[alert()]))
        context, app = make_context({1: {"subs": ["北京, 北京市"]}})
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertTrue(app.chat_data[1]["alert_seen"]["北京, 北京市"])

        service.weather = make_weather(alerts=[])
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertNotIn("北京, 北京市", app.chat_data[1]["alert_seen"])

    async def test_exempt_levels_bypass_quiet_hours_others_are_held(self):
        now_local = datetime.now(TZ)
        settings.rain_alert_quiet_hours = (
            f"{(now_local - timedelta(hours=1)).strftime('%H:%M')}-"
            f"{(now_local + timedelta(hours=1)).strftime('%H:%M')}"
        )

        # 黄色 is not exempt → held during quiet hours.
        service = StubWeatherService(make_weather(alerts=[alert(level="黄色", alert_id="Y1")]))
        context, app = make_context({1: {"subs": ["北京, 北京市"], "sub_tz": {"北京, 北京市": "Asia/Shanghai"}}})
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(context.bot.messages, [])

        # 橙色 is exempt → pushed even inside the window.
        service.weather = make_weather(alerts=[alert(level="橙色", alert_id="O1")])
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(len(context.bot.messages), 1)

    async def test_held_warning_is_delivered_after_quiet_hours(self):
        now_local = datetime.now(TZ)
        settings.rain_alert_quiet_hours = (
            f"{(now_local - timedelta(hours=1)).strftime('%H:%M')}-"
            f"{(now_local + timedelta(hours=1)).strftime('%H:%M')}"
        )
        service = StubWeatherService(make_weather(alerts=[alert(level="黄色", alert_id="Y2")]))
        context, app = make_context({1: {"subs": ["北京, 北京市"]}})

        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(context.bot.messages, [])

        settings.rain_alert_quiet_hours = ""
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(len(context.bot.messages), 1)

    async def test_no_subscribers_means_no_api_calls(self):
        service = StubWeatherService(make_weather(alerts=[alert()]))
        context, _app = make_context({1: {}})
        await scheduler.check_weather_alerts(context, weather_service=service)
        self.assertEqual(service.calls, 0)


class DerivedEventTests(unittest.TestCase):
    def setUp(self):
        self._derived = settings.enable_derived_event_alerts
        settings.enable_derived_event_alerts = True

    def tearDown(self):
        settings.enable_derived_event_alerts = self._derived

    def test_aqi_threshold_event(self):
        weather = make_weather(aqi=AirQuality(aqi=180, category="中度污染", primary="PM2.5"))
        keys = [key for key, _title, _detail in scheduler._derived_events(weather)]
        self.assertTrue(any(key.startswith("aqi:") for key in keys))

    def test_clean_air_produces_no_event(self):
        weather = make_weather(aqi=AirQuality(aqi=40, category="优"))
        keys = [key for key, _t, _d in scheduler._derived_events(weather)]
        self.assertFalse(any(key.startswith("aqi:") for key in keys))

    def test_heat_and_cold_events(self):
        hot = scheduler._derived_events(make_weather(temp_max=37.0))
        self.assertTrue(any(key.startswith("heat:") for key, _t, _d in hot))
        cold = scheduler._derived_events(make_weather(temp_min=-8.0))
        self.assertTrue(any(key.startswith("cold:") for key, _t, _d in cold))

    def test_wind_event_uses_the_scale_threshold(self):
        windy = scheduler._derived_events(make_weather(wind_scale="7"))
        self.assertTrue(any(key.startswith("wind:") for key, _t, _d in windy))
        calm = scheduler._derived_events(make_weather(wind_scale="3"))
        self.assertFalse(any(key.startswith("wind:") for key, _t, _d in calm))

    def test_range_scale_strings_are_parsed(self):
        windy = scheduler._derived_events(make_weather(wind_scale="6-7"))
        self.assertTrue(any(key.startswith("wind:") for key, _t, _d in windy))

    def test_events_disabled_by_config(self):
        settings.enable_derived_event_alerts = False
        self.assertEqual(scheduler._derived_events(make_weather(temp_max=40.0)), [])


class BriefTimezoneTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scheduler._daily_brief_last_check.clear()
        self._catchup = settings.daily_brief_catchup_hours
        settings.daily_brief_catchup_hours = 0

    def tearDown(self):
        settings.daily_brief_catchup_hours = self._catchup
        scheduler._daily_brief_last_check.clear()

    class LLM:
        def __init__(self):
            self.calls = 0

        async def generate_weather_report(self, weather):
            self.calls += 1
            return "日报正文"

    async def test_brief_fires_at_the_subscription_local_time(self):
        # Choose an HH:MM that is "now" in Tokyo but not in Shanghai.
        now_utc = datetime.now(timezone.utc)
        tokyo_now = now_utc.astimezone(scheduler.ZoneInfo("Asia/Tokyo"))
        target = tokyo_now.strftime("%H:%M")

        service = StubWeatherService(make_weather())
        llm = self.LLM()
        context, app = make_context({
            1: {
                "daily_subs": ["东京"],
                "daily_sub_times": {"东京": target},
                "daily_sub_tz": {"东京": "Asia/Tokyo"},
            },
            2: {
                "daily_subs": ["东京"],
                "daily_sub_times": {"东京": target},
                "daily_sub_tz": {"东京": "Asia/Shanghai"},
            },
        })

        # Seed the watermark just before the target so exactly one crosses it.
        scheduler._daily_brief_last_check[id(app)] = now_utc - timedelta(seconds=90)
        await scheduler.dispatch_daily_briefs(
            context, weather_service=service, llm_service=llm
        )

        recipients = {message["chat_id"] for message in context.bot.messages}
        self.assertIn(1, recipients)
        self.assertNotIn(2, recipients)

    async def test_catchup_delivers_a_brief_missed_during_downtime(self):
        settings.daily_brief_catchup_hours = 3
        now_local = datetime.now(scheduler.ZoneInfo(settings.timezone))
        missed = (now_local - timedelta(hours=1)).strftime("%H:%M")

        service = StubWeatherService(make_weather())
        llm = self.LLM()
        context, app = make_context({
            1: {"daily_subs": ["北京"], "daily_sub_times": {"北京": missed}},
        })

        # No watermark => fresh start, i.e. the bot just came back up.
        await scheduler.dispatch_daily_briefs(
            context, weather_service=service, llm_service=llm
        )
        self.assertEqual(len(context.bot.messages), 1)

        # Same day, already delivered → no duplicate on the next tick.
        await scheduler.dispatch_daily_briefs(
            context, weather_service=service, llm_service=llm
        )
        self.assertEqual(len(context.bot.messages), 1)

    async def test_catchup_disabled_skips_missed_brief(self):
        settings.daily_brief_catchup_hours = 0
        now_local = datetime.now(scheduler.ZoneInfo(settings.timezone))
        missed = (now_local - timedelta(hours=1)).strftime("%H:%M")

        service = StubWeatherService(make_weather())
        llm = self.LLM()
        context, _app = make_context({
            1: {"daily_subs": ["北京"], "daily_sub_times": {"北京": missed}},
        })
        await scheduler.dispatch_daily_briefs(
            context, weather_service=service, llm_service=llm
        )
        self.assertEqual(context.bot.messages, [])


class ThreadTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_pushes_go_to_the_subscribed_forum_topic(self):
        settings_quiet = settings.rain_alert_quiet_hours
        settings.rain_alert_quiet_hours = ""
        try:
            weather = make_weather()
            weather.is_raining = True
            service = StubWeatherService(weather)
            context, _app = make_context({
                -100: {"subs": ["北京, 北京市"], "push_thread_id": 77},
            })
            await scheduler.check_rain_alerts(context, weather_service=service)
            self.assertTrue(context.bot.messages)
            self.assertEqual(context.bot.messages[0].get("message_thread_id"), 77)
        finally:
            settings.rain_alert_quiet_hours = settings_quiet


class PersistenceBackupTests(unittest.TestCase):
    def test_rotation_keeps_generations_in_order(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "bot_data.pickle"
            for generation in ("first", "second", "third"):
                target.write_text(generation)
                rotate_persistence_backups(target, keep=2)

            self.assertEqual(target.with_suffix(".pickle.bak1").read_text(), "third")
            self.assertEqual(target.with_suffix(".pickle.bak2").read_text(), "second")
            self.assertFalse(target.with_suffix(".pickle.bak3").exists())

    def test_missing_or_empty_source_is_skipped(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "bot_data.pickle"
            rotate_persistence_backups(target, keep=3)
            self.assertFalse(target.with_suffix(".pickle.bak1").exists())

            target.write_text("")
            rotate_persistence_backups(target, keep=3)
            self.assertFalse(target.with_suffix(".pickle.bak1").exists())

    def test_keep_zero_disables_backups(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "bot_data.pickle"
            target.write_text("data")
            rotate_persistence_backups(target, keep=0)
            self.assertFalse(target.with_suffix(".pickle.bak1").exists())


if __name__ == "__main__":
    unittest.main()
