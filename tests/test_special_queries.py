"""Typhoon/tide special query parsing (no network)."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.handlers.special_queries import extract_special_query


class ExtractSpecialQueryTests(unittest.TestCase):
    def test_typhoon_bare(self):
        self.assertEqual(extract_special_query(["台风"]), ("typhoon", None))

    def test_typhoon_with_city_either_order(self):
        self.assertEqual(extract_special_query(["台风", "珠海"]), ("typhoon", "珠海"))
        self.assertEqual(extract_special_query(["珠海", "台风"]), ("typhoon", "珠海"))

    def test_tide_requires_city_token(self):
        self.assertEqual(extract_special_query(["潮汐", "青岛"]), ("tide", "青岛"))
        self.assertEqual(extract_special_query(["青岛", "潮汐"]), ("tide", "青岛"))
        self.assertEqual(extract_special_query(["潮汐"]), ("tide", None))

    def test_glued_suffix(self):
        self.assertEqual(extract_special_query(["青岛潮汐"]), ("tide", "青岛"))
        self.assertEqual(extract_special_query(["珠海台风"]), ("typhoon", "珠海"))

    def test_weather_query_not_special(self):
        self.assertEqual(extract_special_query(["北京", "24h"]), (None, None))
        self.assertEqual(extract_special_query(["上海", "降水"]), (None, None))


if __name__ == "__main__":
    unittest.main()
