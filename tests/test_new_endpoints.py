"""Tests for the newly integrated QWeather capabilities.

Field names are asserted against the official documentation:
- grid weather reuses the city field names but has no vis / feelsLike / pop
- Time Machine is LocationID-only, uses weatherDaily/weatherHourly, yyyyMMdd
- life indices 3d repeat every type per day, so views must pick one day
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.qweather import GRID_FALLBACK_DAYS, GRID_FALLBACK_HOURS, QWeatherAdapter
from core.config import settings
from domain.models import HistoricalDaySummary, LifeIndex
from utils.formatter import format_indices_data, select_indices_for_day

TZ = timezone(timedelta(hours=8))


class DistanceTests(unittest.TestCase):
    def test_known_distance_beijing_to_shanghai(self):
        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        km = adapter._distance_km(116.41, 39.92, 121.47, 31.23)
        self.assertGreater(km, 1000)
        self.assertLess(km, 1200)

    def test_identical_points_are_zero(self):
        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        self.assertAlmostEqual(adapter._distance_km(116.41, 39.92, 116.41, 39.92), 0.0, places=6)

    def test_one_hundredth_degree_is_about_one_km(self):
        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        km = adapter._distance_km(116.40, 39.90, 116.41, 39.90)
        self.assertLess(km, 1.5)

    def test_parse_coords_accepts_only_numeric_pairs(self):
        self.assertEqual(QWeatherAdapter._parse_coords("116.4,39.9"), (116.4, 39.9))
        self.assertIsNone(QWeatherAdapter._parse_coords("北京"))
        self.assertIsNone(QWeatherAdapter._parse_coords("116.4,39.9,7"))
        self.assertIsNone(QWeatherAdapter._parse_coords("a,b"))


class GridFallbackRangeTests(unittest.TestCase):
    def test_ranges_downgrade_to_supported_grid_values(self):
        # Grid weather only offers 24h/72h and 3d/7d.
        self.assertEqual(GRID_FALLBACK_HOURS["168h"], "72h")
        self.assertEqual(GRID_FALLBACK_HOURS["72h"], "72h")
        self.assertEqual(GRID_FALLBACK_DAYS["30d"], "7d")
        self.assertEqual(GRID_FALLBACK_DAYS["3d"], "3d")

    def test_every_configurable_range_has_a_grid_mapping(self):
        for value in ("24h", "72h", "168h"):
            self.assertIn(value, GRID_FALLBACK_HOURS)
        for value in ("3d", "7d", "10d", "15d", "30d"):
            self.assertIn(value, GRID_FALLBACK_DAYS)


class GridMappingTests(unittest.TestCase):
    """Grid payloads must flow through the existing mappers unchanged."""

    def setUp(self):
        self.adapter = QWeatherAdapter.__new__(QWeatherAdapter)

    def test_grid_hourly_maps_without_pop_or_vis(self):
        payload = {
            "hourly": [
                {
                    "fxTime": "2026-07-26T16:00+08:00",
                    "temp": "28",
                    "icon": "101",
                    "text": "多云",
                    "wind360": "180",
                    "windDir": "南风",
                    "windScale": "3",
                    "windSpeed": "12",
                    "humidity": "60",
                    "precip": "0.3",
                    "pressure": "1005",
                    "cloud": "40",
                    "dew": "20",
                }
            ]
        }
        hours = self.adapter._map_hourly(payload, {})
        self.assertEqual(len(hours), 1)
        hour = hours[0]
        self.assertEqual(hour.temp, 28)
        self.assertEqual(hour.precip, 0.3)
        self.assertEqual(hour.dew, 20)
        self.assertEqual(hour.cloud, 40)
        # Absent in grid data — must stay unset, never estimated.
        self.assertIsNone(hour.pop)
        self.assertIsNone(hour.visibility)
        self.assertIsNone(hour.feels_like)

    def test_grid_now_payload_has_no_feels_like(self):
        payload = {
            "now": {
                "obsTime": "2026-07-26T16:00+08:00",
                "temp": "30",
                "icon": "100",
                "text": "晴",
                "windDir": "南风",
                "windScale": "3",
                "humidity": "50",
                "precip": "0.0",
                "pressure": "1008",
                "cloud": "10",
                "dew": "18",
            }
        }
        hours = self.adapter._map_hourly({"hourly": [
            {"fxTime": "2026-07-26T18:00+08:00", "temp": "29", "icon": "101", "text": "多云"}
        ]}, payload)
        # The current hour is prepended from the now payload.
        self.assertEqual(hours[0].temp, 30)
        self.assertIsNone(hours[0].feels_like)


class HistoryMappingTests(unittest.TestCase):
    def setUp(self):
        self.adapter = QWeatherAdapter.__new__(QWeatherAdapter)

    def test_maps_weather_daily_block(self):
        payload = {
            "weatherDaily": {
                "date": "2026-07-25",
                "tempMax": "33",
                "tempMin": "24",
                "humidity": "70",
                "precip": "1.2",
                "pressure": "1003",
            }
        }
        summary = self.adapter._map_history(payload)
        self.assertIsInstance(summary, HistoricalDaySummary)
        self.assertEqual(summary.temp_max, 33)
        self.assertEqual(summary.temp_min, 24)
        self.assertEqual(summary.humidity, 70)
        self.assertEqual(summary.precip, 1.2)
        self.assertEqual(summary.date.date(), datetime(2026, 7, 25).date())

    def test_missing_or_unavailable_history_is_none(self):
        self.assertIsNone(self.adapter._map_history(None))
        self.assertIsNone(self.adapter._map_history({}))
        self.assertIsNone(self.adapter._map_history({"weatherDaily": "nope"}))
        marker = QWeatherAdapter._unavailable_marker("/v7/historical/weather")
        self.assertIsNone(self.adapter._map_history(marker))

    def test_dirty_values_stay_none_rather_than_zero(self):
        payload = {"weatherDaily": {"date": "2026-07-25", "tempMax": "N/A", "precip": ""}}
        summary = self.adapter._map_history(payload)
        self.assertIsNone(summary.temp_max)
        self.assertIsNone(summary.precip)


class AirStationTests(unittest.TestCase):
    def test_station_names_are_deduplicated_and_capped(self):
        payload = {"stations": [{"name": f"站{i % 3}"} for i in range(10)]}
        names = QWeatherAdapter._map_air_stations(payload)
        self.assertEqual(names, ["站0", "站1", "站2"])

    def test_no_stations_yields_empty_list(self):
        self.assertEqual(QWeatherAdapter._map_air_stations(None), [])
        self.assertEqual(QWeatherAdapter._map_air_stations({}), [])


class IndicesDaySelectionTests(unittest.TestCase):
    def _indices(self):
        entries = []
        for offset in range(3):
            day = datetime(2026, 7, 26) + timedelta(days=offset)
            for type_id, name in (("3", "穿衣"), ("5", "紫外线")):
                entries.append(
                    LifeIndex(type=type_id, name=name, category=f"第{offset}天", text="", date=day)
                )
        return entries

    def test_selects_the_earliest_day_by_default(self):
        chosen = select_indices_for_day(self._indices())
        self.assertEqual(len(chosen), 2)
        self.assertTrue(all(index.category == "第0天" for index in chosen))

    def test_can_select_a_later_day(self):
        target = (datetime(2026, 7, 26) + timedelta(days=2)).date()
        chosen = select_indices_for_day(self._indices(), target)
        self.assertTrue(all(index.category == "第2天" for index in chosen))

    def test_dateless_entries_are_kept(self):
        entries = [LifeIndex(type="3", name="穿衣", category="无日期", text="")]
        self.assertEqual(len(select_indices_for_day(entries)), 1)

    def test_text_view_does_not_repeat_indices_across_days(self):
        rendered = format_indices_data(self._indices())
        # Each index name must appear once, not once per day.
        self.assertEqual(rendered.count("穿衣"), 1)
        self.assertEqual(rendered.count("紫外线"), 1)

    def test_config_defaults_to_three_day_indices(self):
        self.assertEqual(settings.qweather_indices_days, "3d")


if __name__ == "__main__":
    unittest.main()


class OptionalComponentTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """One hung optional endpoint must degrade, not hold the reply hostage."""

    async def test_slow_optional_component_degrades_but_core_reply_survives(self):
        import asyncio

        from adapters.qweather import QWeatherAdapter

        adapter = QWeatherAdapter()
        original_timeout = QWeatherAdapter.OPTIONAL_COMPONENT_TIMEOUT
        QWeatherAdapter.OPTIONAL_COMPONENT_TIMEOUT = 0.05

        async def fake_cached_request(key, path, params, **kwargs):
            if "/v7/indices" in path:
                await asyncio.sleep(0.5)  # hangs well past the optional budget
                return {"daily": []}
            if "/geo/" in path:
                return None
            if path.endswith("/now") or "/weather/now" in path:
                return {"now": {"temp": "25", "text": "晴", "icon": "100"}}
            return None

        adapter._cached_request = fake_cached_request
        adapter.get_geo_location = self._fake_geo

        try:
            started = asyncio.get_event_loop().time()
            data = await adapter.get_weather("北京", profile="indices")
            elapsed = asyncio.get_event_loop().time() - started
        finally:
            QWeatherAdapter.OPTIONAL_COMPONENT_TIMEOUT = original_timeout

        self.assertIsNotNone(data, "core weather must survive a hung optional component")
        self.assertEqual(data.indices, [], "the slow component degrades to empty")
        self.assertLess(elapsed, 0.4, "reply must not wait for the hung component")

    @staticmethod
    async def _fake_geo(raw):
        return {
            "id": "101010100", "name": "北京", "adm1": "北京市",
            "lat": "39.9", "lon": "116.4", "tz": "Asia/Shanghai",
        }
