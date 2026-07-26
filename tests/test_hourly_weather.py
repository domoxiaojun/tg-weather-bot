import os
import struct
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.qweather import QWeatherAdapter
from core.handlers.common import join_location_args, parse_chart_request
from domain.models import HourlyForecast, WeatherData
from services.chart_cache import chart_cache_key
from services.fusion import WeatherFusionService
from services.visualizer import Visualizer
from utils.formatter import format_hourly_weather, format_weather_number


class QWeatherHourlyMappingTests(unittest.TestCase):
    def setUp(self):
        self.adapter = QWeatherAdapter.__new__(QWeatherAdapter)

    def test_maps_hourly_fields_without_estimating_feels_like(self):
        mapped = self.adapter._map_hourly(
            {
                "hourly": [
                    {
                        "fxTime": "2026-07-25T20:00+08:00",
                        "temp": "30",
                        "icon": "302",
                        "text": "雷阵雨",
                        "windDir": "南风",
                        "windScale": "3-4",
                        "windSpeed": "10",
                        "humidity": "70",
                        "pop": "55",
                        "precip": "0.35",
                        "pressure": "1003",
                        "cloud": "85",
                        "dew": "24",
                        "uvIndex": "6",
                    }
                ]
            },
            {"now": {}},
        )

        self.assertEqual(len(mapped), 1)
        hour = mapped[0]
        self.assertIsNone(hour.feels_like)
        self.assertFalse(hour.feels_like_estimated)
        self.assertIsNone(hour.feels_like_source)
        self.assertEqual(hour.wind_speed, 10)
        self.assertEqual(hour.uv_index, 6)
        self.assertEqual(hour.pop, 55)
        self.assertEqual(hour.precip, 0.35)

    def test_prepended_current_hour_uses_provider_feels_like(self):
        mapped = self.adapter._map_hourly(
            {
                "hourly": [
                    {
                        "fxTime": "2026-07-25T20:00+08:00",
                        "temp": "29",
                        "icon": "100",
                        "text": "晴",
                        "windSpeed": "8",
                        "humidity": "60",
                    }
                ]
            },
            {
                "now": {
                    "obsTime": "2026-07-25T18:22+08:00",
                    "temp": "30",
                    "feelsLike": "34",
                    "icon": "100",
                    "text": "晴",
                    "windSpeed": "5",
                    "humidity": "68",
                    "precip": "0",
                }
            },
        )

        self.assertEqual(len(mapped), 2)
        current = mapped[0]
        self.assertEqual(current.feels_like, 34)
        self.assertFalse(current.feels_like_estimated)
        self.assertEqual(current.feels_like_source, "qweather")
        self.assertEqual(current.wind_speed, 5)


class FusionHourlyFeelsLikeTests(unittest.TestCase):
    def test_fills_only_missing_values_from_native_caiyun_hours(self):
        qweather_hours = [
            HourlyForecast(
                time=datetime(2026, 7, 25, 18, tzinfo=timezone(timedelta(hours=8))),
                temp=30,
                text="多云",
                icon="101",
            ),
            HourlyForecast(
                time=datetime(2026, 7, 25, 19, tzinfo=timezone(timedelta(hours=8))),
                temp=29,
                feels_like=31,
                feels_like_source="qweather",
                text="多云",
                icon="101",
            ),
        ]
        caiyun_hours = [
            HourlyForecast(
                time=datetime(2026, 7, 25, 10, tzinfo=timezone.utc),
                temp=30,
                feels_like=35,
                feels_like_source="caiyun",
                text="多云",
                icon="☁️",
            ),
            HourlyForecast(
                time=datetime(2026, 7, 25, 11, tzinfo=timezone.utc),
                temp=29,
                feels_like=34,
                feels_like_source="caiyun",
                text="多云",
                icon="☁️",
            ),
        ]

        enriched = WeatherFusionService._enrich_hourly_feels_like(qweather_hours, caiyun_hours)

        self.assertEqual(enriched, 1)
        self.assertEqual(qweather_hours[0].feels_like, 35)
        self.assertEqual(qweather_hours[0].feels_like_source, "caiyun")
        self.assertEqual(qweather_hours[1].feels_like, 31)
        self.assertEqual(qweather_hours[1].feels_like_source, "qweather")

    def test_rejects_legacy_estimated_caiyun_values(self):
        qweather_hour = HourlyForecast(
            time=datetime(2026, 7, 25, 18),
            temp=30,
            text="多云",
            icon="101",
        )
        estimated_hour = HourlyForecast(
            time=datetime(2026, 7, 25, 18),
            temp=30,
            feels_like=35,
            feels_like_estimated=True,
            text="多云",
            icon="☁️",
        )
        self.assertIsNone(estimated_hour.feels_like)
        self.assertFalse(estimated_hour.feels_like_estimated)

        enriched = WeatherFusionService._enrich_hourly_feels_like(
            [qweather_hour],
            [estimated_hour],
        )

        self.assertEqual(enriched, 0)
        self.assertIsNone(qweather_hour.feels_like)


class HourlyPresentationTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 7, 25, 18, tzinfo=timezone(timedelta(hours=8)))
        self.hours = [
            HourlyForecast(
                time=self.start + timedelta(hours=index),
                temp=30 + index,
                feels_like=33.9 + index,
                feels_like_estimated=False,
                feels_like_source="caiyun",
                text="雷阵雨" if index == 0 else "多云",
                icon="302" if index == 0 else "101",
                pop=55 - index * 5,
                precip=0.35 if index == 0 else 0,
                precip_kind="amount",
                precip_source="qweather",
                wind_dir="南风",
                wind_scale="3-4",
                wind_speed=10 + index,
                humidity=70 - index,
                uv_index=6 - index,
            )
            for index in range(4)
        ]

    def _weather_data(self):
        return WeatherData(
            location_name="广州",
            coords="113.26,23.13",
            now_temp=30,
            now_feels_like=34,
            now_text="雷阵雨",
            now_icon="302",
            hourly=self.hours,
        )

    def test_hourly_text_includes_native_feels_like_and_precipitation(self):
        text = format_hourly_weather(self.hours[:1])
        self.assertIn("体感", text)
        self.assertNotIn("估算", text)
        self.assertIn("33\\.9°C", text)
        self.assertIn("降概 55%", text)
        self.assertIn("降水 0\\.35mm", text)
        self.assertIn("UV 6", text)
        self.assertIn("10km/h", text)

    def test_caiyun_precipitation_is_labeled_as_intensity(self):
        intensity = self.hours[0].model_copy(
            update={
                "precip": 1.25,
                "precip_kind": "intensity",
                "precip_source": "caiyun",
            }
        )
        text = format_hourly_weather([intensity])
        self.assertIn("1\\.25mm/h", text)

    def test_missing_precipitation_is_not_rendered_as_zero(self):
        missing = self.hours[0].model_copy(
            update={"precip": None, "precip_kind": None, "precip_source": None}
        )
        text = format_hourly_weather([missing])
        self.assertIn("降水 N/A", text)

    def test_legacy_estimated_feels_like_is_hidden(self):
        estimated = self.hours[0].model_copy(
            update={"feels_like": 40, "feels_like_estimated": True}
        )
        text = format_hourly_weather([estimated])
        self.assertNotIn("体感", text)
        self.assertNotIn("40°C", text)

    def test_24_hour_text_stays_within_telegram_message_limit(self):
        hours = [
            self.hours[index % len(self.hours)].model_copy(
                update={"time": self.start + timedelta(hours=index)}
            )
            for index in range(24)
        ]
        text = format_hourly_weather(hours)
        self.assertLess(len(text), 3500)

    def test_number_formatting_does_not_strip_integer_zeroes(self):
        self.assertEqual(format_weather_number(50.4, decimals=0), "50")
        self.assertEqual(format_weather_number(0, decimals=0), "0")

    def test_temperature_and_rain_charts_render_png(self):
        data = self._weather_data()
        temperature_png = Visualizer.draw_hourly_temp_chart(data)
        rain_png = Visualizer.draw_hourly_rain_chart(data)
        self.assertTrue(temperature_png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(rain_png.startswith(b"\x89PNG\r\n\x1a\n"))
        # 12x6.75 inch card at 120 dpi (slimmer PNGs for faster uploads).
        # Square 1080x1080: chat bubbles scale by width, so square reads larger.
        self.assertEqual(struct.unpack(">II", temperature_png[16:24]), (1080, 1080))
        self.assertEqual(struct.unpack(">II", rain_png[16:24]), (1080, 1080))

    def test_temperature_chart_keeps_native_feels_like_gaps(self):
        runs = Visualizer._finite_runs(
            np.array([34.0, 35.0, np.nan, np.nan, 33.0, 32.0])
        )
        self.assertEqual([run.tolist() for run in runs], [[0, 1], [4, 5]])

    def test_rain_chart_renders_explicit_missing_data_state(self):
        data = self._weather_data().model_copy(deep=True)
        data.hourly = [
            hour.model_copy(
                update={
                    "pop": None,
                    "precip": None,
                    "precip_kind": None,
                    "precip_source": None,
                }
            )
            for hour in data.hourly
        ]

        rain_png = Visualizer.draw_hourly_rain_chart(data)

        self.assertTrue(rain_png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_rain_chart_separates_amount_and_intensity_series(self):
        data = self._weather_data().model_copy(deep=True)
        data.hourly[0] = data.hourly[0].model_copy(
            update={
                "precip": 0.8,
                "precip_kind": "amount",
                "precip_source": "qweather",
            }
        )
        data.hourly[1] = data.hourly[1].model_copy(
            update={
                "precip": 1.2,
                "precip_kind": "intensity",
                "precip_source": "caiyun",
            }
        )

        rain_png = Visualizer.draw_hourly_rain_chart(data)

        self.assertTrue(rain_png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_old_cached_hourly_shape_remains_compatible(self):
        hour = HourlyForecast(
            time=self.start,
            temp=25,
            text="晴",
            icon="100",
        )
        self.assertIsNone(hour.feels_like)
        self.assertFalse(hour.feels_like_estimated)
        self.assertIsNone(hour.feels_like_source)
        self.assertIsNone(hour.wind_speed)
        self.assertIsNone(hour.uv_index)

    def test_chart_cache_key_changes_with_rendered_weather_data(self):
        original = self._weather_data()
        changed = original.model_copy(deep=True)
        changed.hourly[0].temp += 1
        original_key = chart_cache_key(original, "temp")
        self.assertTrue(original_key.startswith("chart:v7:"))
        self.assertNotEqual(
            original_key,
            chart_cache_key(changed, "temp"),
        )

        renamed = original.model_copy(update={"location_name": "广州天河"})
        self.assertNotEqual(original_key, chart_cache_key(renamed, "temp"))

        extended = original.model_copy(deep=True)
        extended.hourly = [
            original.hourly[index % len(original.hourly)].model_copy(
                update={"time": self.start + timedelta(hours=index)}
            )
            for index in range(30)
        ]
        changed_outside_chart = extended.model_copy(deep=True)
        changed_outside_chart.hourly[24].temp += 5
        self.assertEqual(
            chart_cache_key(extended, "temp"),
            chart_cache_key(changed_outside_chart, "temp"),
        )

    def test_multi_word_locations_are_preserved_for_chart_and_report(self):
        self.assertEqual(parse_chart_request(["New", "York", "daily"]), ("New York", "daily"))
        self.assertEqual(parse_chart_request(["New", "York"]), ("New York", "temp"))
        self.assertEqual(join_location_args(["New", "York"]), "New York")


if __name__ == "__main__":
    unittest.main()
