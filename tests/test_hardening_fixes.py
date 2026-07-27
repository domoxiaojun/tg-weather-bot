import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.qweather import QWeatherAdapter
from domain.models import DailyForecast, HourlyForecast, WeatherData
from services.llm import LLMService
from services.visualizer import Visualizer
from utils.formatter import callback_location_token


class OptionalFloatTests(unittest.TestCase):
    """脏值不得再被静默转成 0（fill-only 策略的前提）。"""

    def test_unparseable_string_returns_none_instead_of_zero(self):
        self.assertIsNone(QWeatherAdapter._optional_float("N/A"))
        self.assertIsNone(QWeatherAdapter._optional_float("--"))

    def test_numeric_and_suffixed_values_still_parse(self):
        self.assertEqual(QWeatherAdapter._optional_float("3.2"), 3.2)
        self.assertEqual(QWeatherAdapter._optional_float("3.2mm"), 3.2)
        self.assertEqual(QWeatherAdapter._optional_float(0), 0.0)

    def test_empty_values_are_none(self):
        self.assertIsNone(QWeatherAdapter._optional_float(None))
        self.assertIsNone(QWeatherAdapter._optional_float(""))


class MalformedRecordTests(unittest.TestCase):
    """单条畸形记录只跳过自身，不拖垮整个数据源。"""

    def setUp(self):
        self.adapter = QWeatherAdapter.__new__(QWeatherAdapter)

    def test_hourly_records_missing_time_or_temp_are_skipped(self):
        payload = {
            "hourly": [
                {"fxTime": "2026-07-26T10:00+08:00", "temp": "30"},
                {"fxTime": "not-a-time", "temp": "31"},
                {"fxTime": "2026-07-26T12:00+08:00", "temp": "N/A"},
                {"fxTime": "2026-07-26T13:00+08:00", "temp": "29"},
            ]
        }
        hours = self.adapter._map_hourly(payload, {})
        self.assertEqual(len(hours), 2)
        self.assertTrue(all(h.time.tzinfo is not None for h in hours))

    def test_daily_records_missing_temps_or_date_are_skipped(self):
        payload = {
            "daily": [
                {"fxDate": "2026-07-26", "tempMin": "22", "tempMax": "31"},
                {"tempMin": "22", "tempMax": "31"},
                {"fxDate": "2026-07-27", "tempMin": "N/A", "tempMax": "31"},
            ]
        }
        days = self.adapter._map_daily(payload)
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0].temp_min, 22.0)

    def test_indices_records_missing_keys_are_skipped(self):
        payload = {
            "daily": [
                {"type": "1", "name": "运动", "category": "适宜"},
                {"name": "缺type"},
            ]
        }
        indices = self.adapter._map_indices(payload)
        self.assertEqual(len(indices), 1)


class GeoCacheKeyTests(unittest.TestCase):
    def test_coordinates_are_normalized_to_two_decimals(self):
        key_a = QWeatherAdapter._geo_cache_key("116.397428,39.90923")
        key_b = QWeatherAdapter._geo_cache_key("116.399999,39.905001")
        self.assertEqual(key_a, key_b)
        self.assertEqual(key_a, "geo:116.40,39.91")

    def test_city_names_keep_plain_keys(self):
        self.assertEqual(QWeatherAdapter._geo_cache_key(" 北京 "), "geo:北京")


class TelegramHtmlHardeningTests(unittest.TestCase):
    def setUp(self):
        self.svc = LLMService.__new__(LLMService)

    def test_unbalanced_tags_are_stripped(self):
        fixed = self.svc._fix_telegram_html("未闭合 <b>加粗")
        self.assertNotIn("<b>", fixed)

    def test_inline_underscores_in_identifiers_survive(self):
        fixed = self.svc._fix_telegram_html("PM2_5 与 pm_10 数据")
        self.assertIn("PM2_5", fixed)
        self.assertIn("pm_10", fixed)

    def test_report_is_truncated_under_telegram_limit(self):
        truncated = self.svc._truncate_report("<b>" + "长" * 5000 + "</b>")
        self.assertLessEqual(len(truncated), 3810)
        self.assertEqual(truncated.count("<b>"), truncated.count("</b>"))


class CallbackTokenTests(unittest.TestCase):
    def test_short_names_pass_through(self):
        self.assertEqual(callback_location_token("北京", "116.4,39.9"), "北京")

    def test_long_names_fall_back_to_coords_within_limit(self):
        long_name = "新疆维吾尔自治区巴音郭楞蒙古自治州某某某很长的地名"
        token = callback_location_token(long_name, "86.15,41.77")
        self.assertEqual(token, "86.15,41.77")
        self.assertLessEqual(len(f"chart|{token}|temp".encode("utf-8")), 64)

    def test_long_names_without_coords_are_byte_safe(self):
        long_name = "超长地名" * 20
        token = callback_location_token(long_name, None)
        self.assertLessEqual(len(f"chart|{token}|temp".encode("utf-8")), 64)
        token.encode("utf-8")  # must not raise


class DailyChartRobustnessTests(unittest.TestCase):
    def _weather(self, day_count: int) -> WeatherData:
        tz = timezone(timedelta(hours=8))
        return WeatherData(
            source="qweather",
            location_name="北京",
            coords="116.4,39.9",
            now_temp=30,
            now_text="晴",
            now_icon="100",
            summary="s",
            update_time=datetime(2026, 7, 26, 8, tzinfo=tz),
            daily=[
                DailyForecast(
                    date=datetime(2026, 7, 26) + timedelta(days=i),
                    temp_min=22 + i,
                    temp_max=31 + i,
                    text_day="晴",
                    icon_day="100",
                    text_night="晴",
                    icon_night="150",
                )
                for i in range(day_count)
            ],
        )

    def test_daily_chart_renders_png_with_card_pipeline(self):
        png = Visualizer.draw_daily_temp_chart(self._weather(6))
        self.assertIsNotNone(png)
        self.assertEqual(png[:4], b"\x89PNG")

    def test_single_day_returns_none_instead_of_crashing(self):
        self.assertIsNone(Visualizer.draw_daily_temp_chart(self._weather(1)))


class SchedulerHelpersTests(unittest.TestCase):
    def test_remove_subscription_only_touches_matching_entry(self):
        from core.scheduler import _remove_subscription

        chat_data = {"subs": ["北京", "上海"]}
        _remove_subscription(chat_data, "subs", "北京")
        self.assertEqual(chat_data["subs"], ["上海"])
        _remove_subscription(chat_data, "subs", "不存在")
        self.assertEqual(chat_data["subs"], ["上海"])

    def test_daily_brief_time_is_timezone_aware(self):
        from zoneinfo import ZoneInfo

        from core.config import settings

        self.assertIsNotNone(ZoneInfo(settings.timezone))


class ChartFailureSentinelTests(unittest.TestCase):
    def test_failure_sentinel_is_not_returned_as_file_id(self):
        from services import chart_cache
        from utils.cache import cache

        tz = timezone(timedelta(hours=8))
        data = WeatherData(
            source="qweather",
            location_name="北京",
            coords="116.4,39.9",
            now_temp=30,
            now_text="晴",
            now_icon="100",
            summary="s",
            update_time=datetime(2026, 7, 26, 8, tzinfo=tz),
            hourly=[
                HourlyForecast(
                    time=datetime(2026, 7, 26, 8 + i, tzinfo=tz),
                    temp=25 + i,
                    text="晴",
                    icon="100",
                )
                for i in range(3)
            ],
        )

        async def run():
            key = chart_cache.chart_cache_key(data, "temp")
            await cache.set(key, chart_cache._CHART_FAILURE_SENTINEL, ttl=60)
            try:
                return await chart_cache.get_cached_chart_file_id(data, "temp")
            finally:
                await cache.delete(key)

        self.assertIsNone(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()


class LogTimestampTests(unittest.TestCase):
    def test_log_records_are_stamped_cst(self):
        from datetime import timedelta

        import main  # noqa: F401 - importing configures the logger
        from loguru import logger

        captured = {}

        def sink(message):
            captured["time"] = message.record["time"]

        handle = logger.add(sink, level="INFO")
        try:
            logger.info("timestamp probe")
        finally:
            logger.remove(handle)
        self.assertEqual(captured["time"].utcoffset(), timedelta(hours=8))
