"""Exercise 10.3 payloads through PTB's real HTTP request serialization."""
import json
import os
import unittest
from urllib.parse import parse_qs
from unittest.mock import patch

import httpx
from telegram import Bot
from telegram.request import HTTPXRequest

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import settings
from services.telegram_rich import RichMessenger, table


class BotApi103Tests(unittest.IsolatedAsyncioTestCase):
    async def test_ephemeral_parameters_survive_ptb_serialization(self):
        requests = []

        def respond(request):
            if request.url.path.endswith('/getMe'):
                result = {"id": 1, "is_bot": True, "first_name": "test"}
            else:
                payload = parse_qs(request.content.decode())
                requests.append(payload)
                result = {
                    "message_id": 1, "date": 1,
                    "chat": {"id": -1001, "type": "supergroup"},
                    "text": "personal",
                }
            return httpx.Response(200, json={"ok": True, "result": result})

        request = HTTPXRequest(httpx_kwargs={"transport": httpx.MockTransport(respond)})
        async with Bot("123:test", request=request) as bot:
            with patch.object(settings, "enable_ephemeral_messages", True):
                for callback in (None, "callback-1"):
                    sent = await RichMessenger().send_ephemeral(
                        bot, -1001, "personal", 42, callback_query_id=callback,
                    )
                    self.assertIsNotNone(sent)
                    payload = requests[-1]
                    expected = {"receiver_user_id": 42}
                    if callback:
                        expected["callback_query_id"] = callback
                    self.assertEqual(json.loads(payload["ephemeral_message_parameters"][0]), expected)
                    self.assertNotIn("receiver_user_id", payload)
                    self.assertNotIn("callback_query_id", payload)

    def test_compact_is_opt_in_and_preserves_table_content(self):
        normal = table([["北京", "25°C"]], headers=["城市", "温度"])
        compact = table([["北京", "25°C"]], headers=["城市", "温度"], compact=True)
        self.assertNotIn("is_compact", normal)
        self.assertTrue(compact.pop("is_compact"))
        self.assertEqual(compact, normal)
