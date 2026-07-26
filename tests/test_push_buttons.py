"""Push messages must be actionable: every push carries buttons.

A rain alert / daily brief / warning arriving as plain text is a dead end —
the reader cannot inspect the full weather or adjust the subscription that
produced it without typing commands.
"""

import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core import scheduler
from core.config import settings
from core.handlers.callbacks import CallbackHandlers
from domain.models import MinutelyPrecipitation, WarningAlert, WeatherData
from services.telegram_rich import rich


def rain_weather(rates=(0.0, 1.0), alerts=None) -> WeatherData:
    now = datetime.now()
    return WeatherData(
        location_name="北京, 北京市",
        coords="116.4,39.9",
        now_temp=25,
        now_text="阴",
        now_icon="104",
        summary="s",
        minutely=[
            MinutelyPrecipitation(
                time=now + timedelta(minutes=5 * i),
                precip=value,
                precip_kind="amount",
                interval_minutes=5,
            )
            for i, value in enumerate(rates)
        ],
        alerts=alerts or [],
    )


class StubWeatherService:
    def __init__(self, weather):
        self.weather = weather

    async def get_fused_weather(self, location, *, profile, refresh_qweather=False):
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


def callbacks_of(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def make_context(chat_data):
    app = SimpleNamespace(chat_data=chat_data, bot_data={})
    return SimpleNamespace(application=app, bot=FakeBot())


class PushKeyboardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rich.reset()
        self._quiet = settings.rain_alert_quiet_hours
        self._plots = settings.enable_weather_plots
        self._derived = settings.enable_derived_event_alerts
        self._typhoon = settings.enable_typhoon_alerts
        settings.rain_alert_quiet_hours = ""
        settings.enable_weather_plots = False  # force the plain-text push path
        settings.enable_derived_event_alerts = False
        settings.enable_typhoon_alerts = False

    def tearDown(self):
        settings.rain_alert_quiet_hours = self._quiet
        settings.enable_weather_plots = self._plots
        settings.enable_derived_event_alerts = self._derived
        settings.enable_typhoon_alerts = self._typhoon
        rich.reset()

    def test_push_keyboard_shape_and_budget(self):
        markup = scheduler.push_keyboard("北京, 北京市", "116.4,39.9", "rain")
        data = callbacks_of(markup)
        self.assertEqual(len(data), 2)
        self.assertTrue(data[0].startswith("tq|"))
        self.assertEqual(data[1], "submy|rain")
        for value in data:
            self.assertLessEqual(len(value.encode("utf-8")), 64)

    async def test_rain_alert_carries_buttons(self):
        context = make_context({1: {"subs": ["北京, 北京市"]}})
        await scheduler.check_rain_alerts(
            context, weather_service=StubWeatherService(rain_weather())
        )
        self.assertEqual(len(context.bot.messages), 1)
        markup = context.bot.messages[0].get("reply_markup")
        self.assertIsNotNone(markup, "rain alert push must carry buttons")
        self.assertIn("submy|rain", callbacks_of(markup))

    async def test_warning_push_carries_buttons(self):
        warning = WarningAlert(
            title="暴雨橙色预警",
            type="rain",
            level="橙色",
            text="请注意防范",
            pub_time=datetime.now(),
            source="QWeather",
            alert_id="A1",
        )
        context = make_context({1: {"subs": ["北京, 北京市"]}})
        await scheduler.check_weather_alerts(
            context, weather_service=StubWeatherService(rain_weather(alerts=[warning]))
        )
        self.assertEqual(len(context.bot.messages), 1)
        markup = context.bot.messages[0].get("reply_markup")
        self.assertIsNotNone(markup, "warning push must carry buttons")
        self.assertIn("submy|rain", callbacks_of(markup))

    async def test_daily_brief_carries_buttons(self):
        class LLM:
            provider = object()

            async def generate_weather_report(self, weather):
                return "今日晴。"

        now = datetime.now(scheduler.zone_for("Asia/Shanghai"))
        chat_data = {
            1: {
                "daily_subs": ["北京, 北京市"],
                "daily_sub_times": {"北京, 北京市": now.strftime("%H:%M")},
                "daily_sub_tz": {"北京, 北京市": "Asia/Shanghai"},
            }
        }
        context = make_context(chat_data)
        await scheduler.dispatch_daily_briefs(
            context, weather_service=StubWeatherService(rain_weather()), llm_service=LLM()
        )
        self.assertEqual(len(context.bot.messages), 1)
        markup = context.bot.messages[0].get("reply_markup")
        self.assertIsNotNone(markup, "daily brief must carry buttons")
        self.assertIn("submy|daily", callbacks_of(markup))


class FakeQuery:
    def __init__(self, data, reply_markup=None):
        self.data = data
        self.inline_message_id = None
        self.message = SimpleNamespace(
            message_id=10,
            chat=SimpleNamespace(id=1, type="private"),
            reply_markup=reply_markup,
        )
        self.answers = []
        self.markup_edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_edits.append(reply_markup)

    async def edit_message_text(self, text, **kwargs):
        pass


def make_update(query):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_user=SimpleNamespace(id=7),
        effective_message=SimpleNamespace(message_thread_id=None),
    )


class WeatherHandlerStub:
    def __init__(self):
        self.calls = []

    async def _send_weather(self, update, context, coords, **kwargs):
        self.calls.append((coords, kwargs))


class PushButtonCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rich.reset()

    def tearDown(self):
        rich.reset()

    def make_handlers(self):
        deps = SimpleNamespace(
            weather_service=SimpleNamespace(qweather=SimpleNamespace()), llm_service=None
        )
        stub = WeatherHandlerStub()
        return CallbackHandlers(deps, weather_handlers=stub), stub

    async def test_submy_opens_the_subscription_card(self):
        handlers, _ = self.make_handlers()
        query = FakeQuery("submy|rain")
        context = SimpleNamespace(chat_data={"subs": ["北京, 北京市"]}, bot=FakeBot(), args=[])

        await handlers.handle_callback(make_update(query), context)

        self.assertEqual(len(context.bot.messages), 1)
        sent = context.bot.messages[0]
        self.assertIn("降雨提醒", sent["text"])
        self.assertIsNotNone(sent.get("reply_markup"))

    async def test_view_weather_on_a_push_keeps_the_buttons(self):
        markup = scheduler.push_keyboard("北京, 北京市", "116.4,39.9", "rain")
        handlers, stub = self.make_handlers()
        token = callbacks_of(markup)[0].split("|")[1]
        query = FakeQuery(f"tq|{token}|default|0|0", reply_markup=markup)
        context = SimpleNamespace(chat_data={}, bot=FakeBot(), args=[])

        await handlers.handle_callback(make_update(query), context)

        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(query.markup_edits, [], "push buttons must survive the tap")

    async def test_pure_chooser_still_retires_after_the_tap(self):
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        chooser = InlineKeyboardMarkup([
            [InlineKeyboardButton("北京", callback_data="tq|116.4,39.9|default|0|0")],
            [InlineKeyboardButton("北京西", callback_data="tq|116.1,39.9|default|0|0")],
        ])
        handlers, stub = self.make_handlers()
        query = FakeQuery("tq|116.4,39.9|default|0|0", reply_markup=chooser)
        context = SimpleNamespace(chat_data={}, bot=FakeBot(), args=[])

        await handlers.handle_callback(make_update(query), context)

        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(query.markup_edits, [None], "chooser must retire its buttons")


if __name__ == "__main__":
    unittest.main()
