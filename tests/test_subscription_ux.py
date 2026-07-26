import os
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.handlers.subscriptions import remove_subscription_entry, render_subscription_list
from core.scheduler import parse_brief_time


class ParseBriefTimeTests(unittest.TestCase):
    def test_valid_times(self):
        self.assertEqual(parse_brief_time("07:30"), (7, 30))
        self.assertEqual(parse_brief_time("0:05"), (0, 5))
        self.assertEqual(parse_brief_time("23:59"), (23, 59))

    def test_invalid_times(self):
        for value in ("24:00", "07:60", "7点30", "0730", "", None, "aa:bb"):
            self.assertIsNone(parse_brief_time(value), value)


class SubscriptionListTests(unittest.TestCase):
    def test_render_daily_list_shows_custom_time_and_buttons(self):
        chat_data = {
            "daily_subs": ["北京, 北京市", "上海, 上海市"],
            "daily_sub_times": {"上海, 上海市": "07:15"},
        }
        text, keyboard = render_subscription_list(chat_data, "daily")
        self.assertIn("08:00", text)
        self.assertIn("07:15", text)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0].callback_data, "unsub|daily|0")

    def test_render_empty_list_returns_none(self):
        self.assertEqual(render_subscription_list({}, "rain"), (None, None))

    def test_remove_entry_cleans_auxiliary_records(self):
        chat_data = {
            "daily_subs": ["北京"],
            "daily_sub_times": {"北京": "07:00"},
            "daily_brief_last_sent": {"北京": "2026-07-26"},
        }
        removed = remove_subscription_entry(chat_data, "daily", 0)
        self.assertEqual(removed, "北京")
        self.assertEqual(chat_data["daily_subs"], [])
        self.assertNotIn("北京", chat_data["daily_sub_times"])
        self.assertNotIn("北京", chat_data["daily_brief_last_sent"])

    def test_remove_entry_out_of_range_is_safe(self):
        chat_data = {"subs": ["北京"]}
        self.assertIsNone(remove_subscription_entry(chat_data, "rain", 5))
        self.assertEqual(chat_data["subs"], ["北京"])


if __name__ == "__main__":
    unittest.main()
