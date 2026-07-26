"""Onboarding funnel: /start, /help, glued Chinese input, unknown commands.

The bar (owner decision): pure command operation is legacy — every entry point
leads with buttons or plain-text input, and no reply may dead-end.
"""

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.handlers.common import parse_location_and_view, split_glued_query
from core.handlers.guide import build_guide
from core.handlers.weather import WeatherHandlers


class GluedInputTests(unittest.TestCase):
    def test_common_glued_suffixes_split(self):
        self.assertEqual(split_glued_query("北京明天"), ["北京", "明天"])
        self.assertEqual(split_glued_query("上海降水"), ["上海", "降水"])
        self.assertEqual(split_glued_query("石家庄今天"), ["石家庄", "今天"])

    def test_short_names_and_bare_suffixes_never_missplit(self):
        # 朝阳 is a city; 大后天 alone leaves an empty city; ascii stays whole.
        self.assertEqual(split_glued_query("朝阳"), ["朝阳"])
        self.assertEqual(split_glued_query("大后天"), ["大后天"])
        self.assertEqual(split_glued_query("tokyo明天"), ["tokyo明天"])

    def test_parse_pipeline_uses_the_split(self):
        self.assertEqual(parse_location_and_view(["北京明天"]), ("北京", "daily", 1, 1))
        self.assertEqual(parse_location_and_view(["北京"]), ("北京", "default", 0, None))


class GuideTests(unittest.TestCase):
    def test_three_pages_with_tab_navigation(self):
        for page in ("query", "push", "more"):
            text, keyboard = build_guide(page)
            self.assertTrue(text)
            tabs = [
                b.callback_data
                for row in keyboard.inline_keyboard
                for b in row
                if (b.callback_data or "").startswith("help|")
            ]
            self.assertEqual(len(tabs), 3, page)
            checked = [
                b
                for row in keyboard.inline_keyboard
                for b in row
                if b.text.startswith("✅")
            ]
            self.assertEqual(len(checked), 1)

    def test_push_page_names_levels_without_technical_units(self):
        text, keyboard = build_guide("push")
        for label in ("全部降雨", "一般降雨", "仅大雨"):
            self.assertIn(label, text)
        self.assertNotIn("mm/h", text)
        callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
        self.assertIn("submy|rain", callbacks)
        self.assertIn("submy|daily", callbacks)

    def test_unknown_page_falls_back(self):
        text, _ = build_guide("nope")
        self.assertIn("查天气", text)


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(message_id=len(self.messages))


def make_update(chat_type="private", text=""):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=1, type=chat_type),
        effective_user=SimpleNamespace(id=7),
        effective_message=SimpleNamespace(message_thread_id=None, text=text),
        callback_query=None,
    )


def make_context(chat_data=None):
    return SimpleNamespace(
        args=[], chat_data=chat_data if chat_data is not None else {}, bot=FakeBot()
    )


class StartFunnelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        deps = SimpleNamespace(weather_service=None, llm_service=None)
        self.handlers = WeatherHandlers(deps)

    async def test_private_start_sends_query_hint_then_quick_buttons(self):
        context = make_context()
        await self.handlers.start(make_update(), context)
        self.assertEqual(len(context.bot.messages), 2)
        first, second = context.bot.messages
        self.assertIn("直接发城市名", first["text"])
        self.assertIsNotNone(first.get("reply_markup"), "location keyboard expected")
        callbacks = [
            b.callback_data
            for row in second["reply_markup"].inline_keyboard
            for b in row
        ]
        self.assertIn("submy|rain", callbacks)
        self.assertIn("submy|daily", callbacks)
        self.assertIn("help|query", callbacks)

    async def test_returning_user_gets_one_tap_subscribe_for_their_city(self):
        context = make_context({"last_location": {"name": "北京", "coords": "116.4,39.9"}})
        await self.handlers.start(make_update(), context)
        callbacks = [
            b.callback_data
            for row in context.bot.messages[1]["reply_markup"].inline_keyboard
            for b in row
        ]
        self.assertIn("sub|北京", callbacks)
        self.assertIn("dsub|北京", callbacks)

    async def test_group_start_is_one_message_with_buttons(self):
        context = make_context()
        await self.handlers.start(make_update(chat_type="supergroup"), context)
        self.assertEqual(len(context.bot.messages), 1)
        self.assertIsNotNone(context.bot.messages[0].get("reply_markup"))

    async def test_unknown_command_points_at_plain_text_and_help(self):
        context = make_context()
        await self.handlers.handle_unknown_command(make_update(text="/weather 北京"), context)
        self.assertIn("/help", context.bot.messages[0]["text"])
        self.assertIn("城市名", context.bot.messages[0]["text"])

    async def test_glued_tq_command_is_rescued(self):
        called = {}

        async def fake_request(update, context):
            called["args"] = list(context.args)

        self.handlers.handle_weather_request = fake_request
        context = make_context()
        await self.handlers.handle_unknown_command(make_update(text="/tq北京"), context)
        self.assertEqual(called["args"], ["北京"])
        self.assertEqual(context.bot.messages, [])


if __name__ == "__main__":
    unittest.main()
