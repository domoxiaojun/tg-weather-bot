import os
import unittest
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from telegram import SentGuestMessage
from telegram.error import BadRequest

from core.config import settings
from core.handlers.guest import GuestHandlers, extract_guest_request
from core.handlers.inline import InlineHandlers
from domain.models import WeatherData
from services.telegram_rich import (
    RichMessenger,
    input_rich_message_content,
    paragraph,
    rich,
)


def make_weather() -> WeatherData:
    return WeatherData(
        location_name="珠海, 广东省",
        coords="113.57,22.27",
        update_time=datetime(2026, 7, 27, 12, 0),
        now_temp=27,
        now_text="多云",
        now_icon="101",
        summary="未来两小时不会下雨",
    )


class WeatherService:
    async def get_fused_weather(self, location, profile="full"):
        return make_weather()


class InlineQueryDouble:
    def __init__(self, query="珠海"):
        self.id = "inline-query-1"
        self.query = query
        self.location = None
        self.fallback_calls = []

    async def answer(self, results, **kwargs):
        self.fallback_calls.append((results, kwargs))
        return True


class ApiBotDouble:
    username = "DomoWeatherBot"

    def __init__(self, error=None):
        self.error = error
        self.calls = []
        self.guest_fallback_calls = []

    async def do_api_request(self, endpoint, api_kwargs=None, return_type=None):
        self.calls.append((endpoint, api_kwargs, return_type))
        if self.error:
            raise self.error
        if endpoint == "answerGuestQuery":
            return SentGuestMessage("guest-inline-message")
        return True

    async def answer_guest_query(self, guest_query_id, result):
        self.guest_fallback_calls.append((guest_query_id, result))
        return SentGuestMessage("guest-fallback-message")


class RichQueryPayloadTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        rich.reset()

    def test_input_rich_message_content_wire_shape(self):
        content = input_rich_message_content(blocks=[paragraph("hello")])
        self.assertEqual(content["rich_message"]["blocks"][0]["text"], "hello")
        self.assertNotIn("message_text", content)

    async def test_inline_handler_answers_every_weather_article_with_rich_content(self):
        query = InlineQueryDouble()
        bot = ApiBotDouble()
        update = SimpleNamespace(inline_query=query)
        context = SimpleNamespace(bot=bot)
        handler = InlineHandlers(SimpleNamespace(weather_service=WeatherService()))

        await handler.handle_inline_query(update, context)

        endpoint, payload, _return_type = bot.calls[-1]
        self.assertEqual(endpoint, "answerInlineQuery")
        self.assertFalse(query.fallback_calls)
        self.assertGreaterEqual(len(payload["results"]), 6)
        for result in payload["results"]:
            self.assertIn("rich_message", result["input_message_content"])

    async def test_inline_rich_rejection_retries_plain_results(self):
        query = InlineQueryDouble(query="")
        bot = ApiBotDouble(error=BadRequest("RICH_MESSAGE_INVALID"))
        update = SimpleNamespace(inline_query=query)
        context = SimpleNamespace(bot=bot)
        handler = InlineHandlers(SimpleNamespace(weather_service=WeatherService()))

        await handler.handle_inline_query(update, context)

        self.assertEqual(len(query.fallback_calls), 1)
        fallback_result = query.fallback_calls[0][0][0].to_dict()
        self.assertIn("message_text", fallback_result["input_message_content"])

    async def test_transport_uses_independent_inline_endpoint(self):
        messenger = RichMessenger()
        bot = ApiBotDouble()
        result = SimpleNamespace(to_dict=lambda: {"type": "article", "id": "1", "title": "t"})

        answered = await messenger.answer_inline_query(bot, "qid", [result], cache_time=1)

        self.assertTrue(answered)
        self.assertEqual(bot.calls[0][0], "answerInlineQuery")
        self.assertEqual(bot.calls[0][1]["inline_query_id"], "qid")


class GuestModeTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self):
        rich.reset()

    def test_extracts_mention_and_reply_context(self):
        direct = SimpleNamespace(
            text="@DomoWeatherBot 珠海 明天",
            caption=None,
            reply_to_message=None,
        )
        self.assertEqual(extract_guest_request(direct, "DomoWeatherBot"), "珠海 明天")

        replied = SimpleNamespace(text="北京 24h", caption=None)
        summon = SimpleNamespace(
            text="@domoweatherbot",
            caption=None,
            reply_to_message=replied,
        )
        self.assertEqual(extract_guest_request(summon, "DomoWeatherBot"), "北京 24h")

    async def test_guest_handler_answers_with_rich_weather_result(self):
        message = SimpleNamespace(
            guest_query_id="guest-query-1",
            text="@DomoWeatherBot 珠海",
            caption=None,
            reply_to_message=None,
        )
        update = SimpleNamespace(guest_message=message)
        bot = ApiBotDouble()
        context = SimpleNamespace(bot=bot)
        handler = GuestHandlers(SimpleNamespace(weather_service=WeatherService()))

        await handler.handle_guest_message(update, context)

        endpoint, payload, _return_type = bot.calls[-1]
        self.assertEqual(endpoint, "answerGuestQuery")
        self.assertEqual(payload["guest_query_id"], "guest-query-1")
        self.assertIn("rich_message", payload["result"]["input_message_content"])
        self.assertFalse(bot.guest_fallback_calls)

    async def test_guest_mode_plain_fallback_when_rich_is_disabled(self):
        old = settings.enable_rich_messages
        settings.enable_rich_messages = False
        try:
            message = SimpleNamespace(
                guest_query_id="guest-query-2",
                text="@DomoWeatherBot 珠海",
                caption=None,
                reply_to_message=None,
            )
            bot = ApiBotDouble()
            handler = GuestHandlers(SimpleNamespace(weather_service=WeatherService()))

            await handler.handle_guest_message(
                SimpleNamespace(guest_message=message),
                SimpleNamespace(bot=bot),
            )

            self.assertFalse(bot.calls)
            self.assertEqual(len(bot.guest_fallback_calls), 1)
            fallback = bot.guest_fallback_calls[0][1].to_dict()
            self.assertIn("message_text", fallback["input_message_content"])
        finally:
            settings.enable_rich_messages = old


if __name__ == "__main__":
    unittest.main()
