"""Card-style subscription interaction: buttons for every flow.

The rule under test: no subscription flow may end in "now go type another
command" — every touchpoint returns a card whose buttons finish the job.
"""

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import settings
from core.handlers.callbacks import CallbackHandlers
from core.handlers.subscriptions import (
    DAILY_TIME_PRESETS,
    build_subscription_blocks,
    render_empty_card,
    render_subscription_list,
    set_daily_time,
)
from services.telegram_rich import rich
from utils.formatter import get_weather_keyboard


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages))

    async def do_api_request(self, endpoint, api_kwargs=None, return_type=None):
        from telegram.error import EndPointNotFound

        raise EndPointNotFound(endpoint)


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.inline_message_id = None
        self.message = SimpleNamespace(
            message_id=10, chat=SimpleNamespace(id=1, type="private")
        )
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def make_geo(name="北京", adm1="北京市", tz="Asia/Shanghai"):
    async def geo(raw):
        return {"name": name, "adm1": adm1, "tz": tz}

    return geo


def make_handlers(geo=None):
    qweather = SimpleNamespace(get_geo_location=geo or make_geo())
    deps = SimpleNamespace(weather_service=SimpleNamespace(qweather=qweather), llm_service=None)
    return CallbackHandlers(deps)


def make_update(query, chat_type="private"):
    return SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=1, type=chat_type),
        effective_user=SimpleNamespace(id=7),
        effective_message=SimpleNamespace(message_thread_id=None),
    )


def make_context(chat_data):
    return SimpleNamespace(chat_data=chat_data, bot=FakeBot(), args=[])


class RichResetMixin:
    """The rich singleton memoizes failures; keep tests independent."""

    def setUp(self):
        super().setUp()
        rich.reset()

    def tearDown(self):
        rich.reset()
        super().tearDown()


class WeatherKeyboardTests(unittest.TestCase):
    def test_weather_card_offers_both_subscriptions(self):
        keyboard = get_weather_keyboard("北京", coords="116.4,39.9")
        buttons = {b.text: b for row in keyboard.inline_keyboard for b in row}
        self.assertEqual(buttons["🔔 降雨提醒"].callback_data, "sub|北京")
        self.assertEqual(buttons["📅 早安简报"].callback_data, "dsub|北京")


class CardLayoutTests(unittest.TestCase):
    def test_cards_cross_link_each_other(self):
        for kind, expected in (("rain", "subview|daily"), ("daily", "subview|rain")):
            chat_data = {"subs": ["北京"], "daily_subs": ["北京"]}
            _text, keyboard = render_subscription_list(chat_data, kind)
            flips = [
                b
                for row in keyboard.inline_keyboard
                for b in row
                if (b.callback_data or "").startswith("subview|")
            ]
            self.assertEqual([b.callback_data for b in flips], [expected], kind)

    def test_last_queried_city_gets_a_one_tap_subscribe_button(self):
        chat_data = {
            "subs": ["上海, 上海市"],
            "last_location": {"name": "北京", "coords": "116.4,39.9"},
        }
        _text, keyboard = render_subscription_list(chat_data, "rain")
        adds = [
            b
            for row in keyboard.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("sub|")
        ]
        self.assertEqual(len(adds), 1)
        self.assertIn("北京", adds[0].text)

    def test_no_add_button_when_the_city_is_already_subscribed(self):
        chat_data = {
            "subs": ["北京, 北京市"],
            "last_location": {"name": "北京", "coords": "116.4,39.9"},
        }
        _text, keyboard = render_subscription_list(chat_data, "rain")
        adds = [
            b
            for row in keyboard.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith("sub|")
        ]
        self.assertEqual(adds, [])

    def test_no_add_button_at_the_subscription_limit(self):
        original = settings.max_subscriptions_per_chat
        settings.max_subscriptions_per_chat = 1
        try:
            chat_data = {
                "subs": ["上海, 上海市"],
                "last_location": {"name": "北京", "coords": "116.4,39.9"},
            }
            _text, keyboard = render_subscription_list(chat_data, "rain")
            adds = [
                b
                for row in keyboard.inline_keyboard
                for b in row
                if (b.callback_data or "").startswith("sub|")
            ]
            self.assertEqual(adds, [])
        finally:
            settings.max_subscriptions_per_chat = original

    def test_empty_card_still_offers_buttons(self):
        chat_data = {"last_location": {"name": "北京", "coords": "116.4,39.9"}}
        text, keyboard = render_empty_card(chat_data, "rain")
        self.assertIn("📭", text)
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        self.assertIn("sub|北京", callbacks)
        self.assertIn("subview|daily", callbacks)

    def test_blocks_mirror_the_text_card(self):
        chat_data = {"subs": ["北京, 北京市"], "daily_subs": []}
        blocks = build_subscription_blocks(chat_data, "rain", prefix="✅ 已订阅")
        kinds = [b["type"] for b in blocks]
        self.assertEqual(kinds, ["paragraph", "heading", "list", "footer"])
        flattened = str(blocks)
        self.assertIn("北京, 北京市", flattened)
        self.assertIn("✅ 已订阅", flattened)

    def test_set_daily_time_validates_index_and_format(self):
        chat_data = {"daily_subs": ["北京"]}
        self.assertIsNone(set_daily_time(chat_data, 3, "07:00"))
        self.assertIsNone(set_daily_time(chat_data, 0, "25:99"))
        self.assertEqual(set_daily_time(chat_data, 0, "07:30"), "北京")
        self.assertEqual(chat_data["daily_sub_times"], {"北京": "07:30"})


class SubscribeCallbackTests(RichResetMixin, unittest.IsolatedAsyncioTestCase):
    async def test_daily_button_subscribes_with_timezone_and_card(self):
        handlers = make_handlers()
        query = FakeQuery("dsub|北京")
        update = make_update(query)
        context = make_context({})

        await handlers.handle_callback(update, context)

        self.assertEqual(context.chat_data["daily_subs"], ["北京, 北京市"])
        self.assertEqual(context.chat_data["daily_sub_tz"], {"北京, 北京市": "Asia/Shanghai"})
        self.assertTrue(any("✅" in (a[0] or "") for a in query.answers))
        # Rich unsupported in FakeBot → falls back to a text card with buttons.
        self.assertEqual(len(context.bot.messages), 1)
        sent = context.bot.messages[0]
        self.assertIn("早安", sent["text"])
        self.assertIsNotNone(sent.get("reply_markup"))

    async def test_rain_button_now_records_the_timezone(self):
        # Regression: the button path used to drop tz, so pushes fell back to
        # the global TIMEZONE instead of the city's own zone.
        handlers = make_handlers(make_geo(name="纽约", adm1="纽约州", tz="America/New_York"))
        query = FakeQuery("sub|纽约")
        update = make_update(query)
        context = make_context({})

        await handlers.handle_callback(update, context)

        self.assertEqual(context.chat_data["subs"], ["纽约, 纽约州"])
        self.assertEqual(context.chat_data["sub_tz"], {"纽约, 纽约州": "America/New_York"})

    async def test_duplicate_tap_is_a_toast_not_a_message(self):
        handlers = make_handlers()
        query = FakeQuery("sub|北京")
        update = make_update(query)
        context = make_context({"subs": ["北京, 北京市"]})

        await handlers.handle_callback(update, context)

        self.assertEqual(context.bot.messages, [])
        self.assertTrue(any("已订阅过" in (a[0] or "") for a in query.answers))

    async def test_group_subscribe_announces_publicly(self):
        handlers = make_handlers()
        query = FakeQuery("sub|北京")
        update = make_update(query, chat_type="supergroup")
        context = make_context({})

        await handlers.handle_callback(update, context)

        self.assertEqual(context.chat_data["subs"], ["北京, 北京市"])
        self.assertEqual(len(context.bot.messages), 1)
        self.assertIn("本群成员都会收到", context.bot.messages[0]["text"])

    async def test_limit_is_reported_as_an_alert(self):
        original = settings.max_subscriptions_per_chat
        settings.max_subscriptions_per_chat = 1
        try:
            handlers = make_handlers()
            query = FakeQuery("dsub|北京")
            update = make_update(query)
            context = make_context({"daily_subs": ["上海, 上海市"]})

            await handlers.handle_callback(update, context)

            self.assertEqual(context.chat_data["daily_subs"], ["上海, 上海市"])
            self.assertTrue(any(a[1] for a in query.answers), "expected show_alert")
        finally:
            settings.max_subscriptions_per_chat = original


class CardEditCallbackTests(RichResetMixin, unittest.IsolatedAsyncioTestCase):
    async def test_time_button_updates_and_rerenders_in_place(self):
        handlers = make_handlers()
        query = FakeQuery(f"dtime|0|{DAILY_TIME_PRESETS[1]}")
        update = make_update(query)
        context = make_context({"daily_subs": ["北京, 北京市"]})

        await handlers.handle_callback(update, context)

        self.assertEqual(
            context.chat_data["daily_sub_times"], {"北京, 北京市": DAILY_TIME_PRESETS[1]}
        )
        self.assertEqual(len(query.edits), 1)
        self.assertIn(DAILY_TIME_PRESETS[1], query.edits[0][0])

    async def test_stale_time_button_refreshes_instead_of_crashing(self):
        handlers = make_handlers()
        query = FakeQuery("dtime|5|07:00")
        update = make_update(query)
        context = make_context({"daily_subs": ["北京, 北京市"]})

        await handlers.handle_callback(update, context)

        self.assertNotIn("daily_sub_times", context.chat_data)
        self.assertTrue(any("已刷新" in (a[0] or "") for a in query.answers))

    async def test_flip_button_switches_between_cards(self):
        handlers = make_handlers()
        query = FakeQuery("subview|daily")
        update = make_update(query)
        context = make_context({"subs": ["北京"], "daily_subs": ["上海, 上海市"]})

        await handlers.handle_callback(update, context)

        self.assertEqual(len(query.edits), 1)
        text, kwargs = query.edits[0]
        self.assertIn("早安", text)
        markup = kwargs.get("reply_markup")
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("subview|rain", callbacks)

    async def test_flip_to_an_empty_card_keeps_buttons(self):
        handlers = make_handlers()
        query = FakeQuery("subview|daily")
        update = make_update(query)
        context = make_context({"subs": ["北京"]})

        await handlers.handle_callback(update, context)

        text, kwargs = query.edits[0]
        self.assertIn("📭", text)
        markup = kwargs.get("reply_markup")
        callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("subview|rain", callbacks)

    async def test_unsubscribing_the_last_city_leaves_a_usable_empty_card(self):
        handlers = make_handlers()
        query = FakeQuery("unsub|rain|0")
        update = make_update(query)
        context = make_context(
            {"subs": ["北京"], "last_location": {"name": "北京", "coords": "116.4,39.9"}}
        )

        await handlers.handle_callback(update, context)

        self.assertEqual(context.chat_data["subs"], [])
        text, kwargs = query.edits[0]
        self.assertIn("📭", text)
        callbacks = [
            b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row
        ]
        self.assertIn("sub|北京", callbacks)


if __name__ == "__main__":
    unittest.main()
