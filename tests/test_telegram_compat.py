import unittest

import telegram
from telegram.constants import BOT_API_VERSION_INFO


class TelegramCompatibilityTests(unittest.TestCase):
    def test_runtime_matches_pinned_ptb_release(self):
        self.assertEqual(telegram.__version__, "22.8")

    def test_ptb_has_typed_bot_api_10_support(self):
        self.assertGreaterEqual(
            (BOT_API_VERSION_INFO.major, BOT_API_VERSION_INFO.minor),
            (10, 0),
        )


if __name__ == "__main__":
    unittest.main()
