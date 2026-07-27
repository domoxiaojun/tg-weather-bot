import os
import struct
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import numpy as np
from matplotlib.axes import Axes


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import settings
from core.handlers.weather import WeatherHandlers, select_auto_chart_type
from domain.models import DailyForecast, HourlyForecast, WeatherData
from services.chart_cache import PreparedChart, chart_cache_key
from services.telegram_rich import rich
from services.visualizer import Visualizer


LOCAL_TZ = timezone(timedelta(hours=8))
START = datetime(2026, 7, 27, 8, tzinfo=LOCAL_TZ)


def make_weather() -> WeatherData:
    hourly = []
    for index in range(24):
        hourly.append(
            HourlyForecast(
                time=START + timedelta(hours=index),
                temp=28 + (index % 7) * 0.6,
                feels_like=None if index == 5 else 30 + (index % 6) * 0.7,
                feels_like_source=None if index == 5 else "caiyun",
                text="晴",
                icon="100",
                pop=10,
                precip=0,
                precip_kind="amount",
                precip_source="qweather",
            )
        )
    daily = [
        DailyForecast(
            date=(START + timedelta(days=index)).replace(tzinfo=None),
            temp_min=23 + index % 4,
            temp_max=31 + index % 5,
            text_day="晴",
            icon_day="100",
            text_night="多云",
            icon_night="150",
        )
        for index in range(15)
    ]
    return WeatherData(
        update_time=START,
        location_name="广州",
        coords="113.26,23.13",
        now_temp=28,
        now_feels_like=31,
        now_text="晴",
        now_icon="100",
        now_precip=0,
        hourly=hourly,
        daily=daily,
    )


class AutoChartSelectionTests(unittest.TestCase):
    def setUp(self):
        self.original_enabled = settings.enable_weather_plots
        settings.enable_weather_plots = True

    def tearDown(self):
        settings.enable_weather_plots = self.original_enabled

    def test_clear_weather_routes_each_view_to_its_matching_chart(self):
        data = make_weather()
        self.assertEqual(select_auto_chart_type(data, "default"), "temp")
        self.assertEqual(select_auto_chart_type(data, "hourly"), "temp")
        self.assertEqual(select_auto_chart_type(data, "daily"), "daily")
        self.assertEqual(select_auto_chart_type(data, "rain"), "rain")
        self.assertIsNone(select_auto_chart_type(data, "indices"))

    def test_current_or_future_meaningful_precipitation_routes_to_rain(self):
        current = make_weather().model_copy(update={"is_raining": True})
        self.assertEqual(select_auto_chart_type(current, "default"), "rain")

        snow = make_weather().model_copy(update={"now_text": "小雪"})
        self.assertEqual(select_auto_chart_type(snow, "default"), "rain")

        probability = make_weather().model_copy(deep=True)
        probability.hourly[6].pop = 50
        self.assertEqual(select_auto_chart_type(probability, "hourly"), "rain")

        amount = make_weather().model_copy(deep=True)
        amount.hourly[7].precip = 0.1
        self.assertEqual(select_auto_chart_type(amount, "default"), "rain")

        forecast_text = make_weather().model_copy(deep=True)
        forecast_text.hourly[8].text = "阵雨"
        self.assertEqual(select_auto_chart_type(forecast_text, "default"), "rain")

    def test_subthreshold_probability_stays_temperature_and_disabled_wins(self):
        data = make_weather().model_copy(deep=True)
        data.hourly[4].pop = 49
        self.assertEqual(select_auto_chart_type(data, "default"), "temp")

        settings.enable_weather_plots = False
        for view in ("default", "hourly", "daily", "rain", "indices"):
            self.assertIsNone(select_auto_chart_type(data, view))


class ChartLabelLogicTests(unittest.TestCase):
    def test_rain_label_mask_marks_only_meaningful_hours(self):
        probability = np.array([0.0, 29.0, 30.0, np.nan, 10.0])
        precipitation = np.array([0.0, 0.0, 0.0, 0.4, np.nan])
        mask = Visualizer._rain_label_mask(probability, precipitation)
        self.assertEqual(mask.tolist(), [False, False, True, True, False])

    def test_precipitation_units_are_not_mixed(self):
        self.assertEqual(Visualizer._format_precip_label(0.8, "amount"), "0.8")
        self.assertEqual(Visualizer._format_precip_label(1.2, "intensity"), "1.2")

    def test_hourly_temperature_labels_every_available_value(self):
        data = make_weather()
        with patch.object(Axes, "annotate", autospec=True, return_value=None) as annotate:
            Visualizer.draw_hourly_temp_chart(data)
        temperature_labels = [call.args[1] for call in annotate.call_args_list if "°" in call.args[1]]
        self.assertEqual(len(temperature_labels), 24 + 23)

    def test_daily_temperature_labels_both_ends_of_every_bar(self):
        data = make_weather()
        with patch.object(Axes, "annotate", autospec=True, return_value=None) as annotate:
            Visualizer.draw_daily_temp_chart(data)
        temperature_labels = [call.args[1] for call in annotate.call_args_list if "°" in call.args[1]]
        self.assertEqual(len(temperature_labels), 15 * 2)

    def test_rain_chart_labels_probability_and_amount_for_each_signal(self):
        data = make_weather().model_copy(deep=True)
        data.hourly[2].pop = 30
        data.hourly[3].pop = 20
        data.hourly[3].precip = 0.5
        data.hourly[4].pop = 60
        data.hourly[4].precip = 0.8

        with patch.object(Axes, "annotate", autospec=True, return_value=None) as annotate:
            Visualizer.draw_hourly_rain_chart(data)
        labels = [call.args[1] for call in annotate.call_args_list]
        self.assertEqual(sum(label.endswith("%") for label in labels), 3)
        # Amount labels omit unit; sub-0.5 mm bars are not annotated (clutter).
        self.assertFalse(any(" mm" in label for label in labels))
        self.assertIn("0.5", labels)
        self.assertIn("0.8", labels)
        self.assertNotIn("0", labels)

    def test_rain_probability_and_amount_panels_share_time_ticks(self):
        data = make_weather()
        with patch.object(Axes, "set_xticklabels", autospec=True, return_value=None) as labels:
            Visualizer.draw_hourly_rain_chart(data)
        time_labels = [
            tuple(call.args[1])
            for call in labels.call_args_list
            if call.args[1] and call.args[1][0] == "现在"
        ]
        self.assertGreaterEqual(len(time_labels), 2)
        self.assertEqual(time_labels[0], time_labels[1])
        self.assertEqual(time_labels[0][1], "11:00")


class ChartRenderingTests(unittest.TestCase):
    def test_all_updated_charts_render_as_square_pngs(self):
        data = make_weather().model_copy(deep=True)
        data.hourly[3].pop = 65
        data.hourly[3].precip = 0.8
        renderers = (
            Visualizer.draw_hourly_temp_chart,
            Visualizer.draw_hourly_rain_chart,
            Visualizer.draw_daily_temp_chart,
        )
        for renderer in renderers:
            with self.subTest(renderer=renderer.__name__):
                png = renderer(data)
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertEqual(struct.unpack(">II", png[16:24]), (1080, 1080))

    def test_cache_namespace_is_bumped_for_new_rendering(self):
        self.assertTrue(chart_cache_key(make_weather(), "temp").startswith("chart:v9:"))


class AutoChartDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rich.reset()

    async def test_chart_preparation_failure_falls_back_without_raising(self):
        data = make_weather()
        handlers = WeatherHandlers(deps=None)

        with patch(
            "core.handlers.weather.prepare_chart",
            new=AsyncMock(side_effect=RuntimeError("cache unavailable")),
        ):
            selected = await handlers.prepare_auto_chart(data, "default")

        self.assertIsNone(selected)

    async def test_chart_is_prepared_for_weather_views_but_not_indices(self):
        data = make_weather()
        handlers = WeatherHandlers(deps=None)
        prepared = PreparedChart(
            chart_type="temp",
            caption="逐小时温度",
            file_id="cached-file-id",
        )

        with patch(
            "core.handlers.weather.prepare_chart", new=AsyncMock(return_value=prepared)
        ) as prepare:
            selected = await handlers.prepare_auto_chart(data, "default")
            self.assertEqual(selected, prepared)
            prepare.assert_awaited_once_with(data, "temp")

            selected = await handlers.prepare_auto_chart(data, "indices")
            self.assertIsNone(selected)
            prepare.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
