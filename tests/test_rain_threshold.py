"""Rain alert threshold tests.

The unit normalisation is the whole point: minutely precipitation is mm per
``interval_minutes`` (5 by default), hourly ``amount`` is mm over that hour and
``intensity`` is already mm/h. Comparing a threshold against the raw minutely
number would be a 12x error.
"""

import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import settings
from core.scheduler import (
    RainSignal,
    evaluate_rain,
    rain_rate_label,
    rate_mm_per_hour,
    will_rain_soon,
)
from domain.models import HourlyForecast, MinutelyPrecipitation, WeatherData


def weather(**kwargs) -> WeatherData:
    base = dict(
        location_name="北京",
        coords="116.4,39.9",
        now_temp=25,
        now_text="多云",
        now_icon="101",
        summary="s",
    )
    base.update(kwargs)
    return WeatherData(**base)


def minutely(rates_mm_per_5min, interval=5):
    now = datetime.now()
    return [
        MinutelyPrecipitation(
            time=now + timedelta(minutes=interval * index),
            precip=value,
            precip_kind="amount",
            interval_minutes=interval,
        )
        for index, value in enumerate(rates_mm_per_5min)
    ]


def hourly(precips, kind="amount", pops=None):
    now = datetime.now()
    return [
        HourlyForecast(
            time=now + timedelta(hours=index),
            temp=25,
            text="多云",
            icon="101",
            precip=value,
            precip_kind=kind,
            pop=None if pops is None else pops[index],
        )
        for index, value in enumerate(precips)
    ]


class UnitNormalisationTests(unittest.TestCase):
    def test_minutely_five_minute_accumulation_becomes_twelve_times(self):
        # 0.5mm in 5 minutes is 6mm/h, not 0.5mm/h.
        self.assertEqual(rate_mm_per_hour(0.5, "amount", 5), 6.0)

    def test_other_intervals_scale_correctly(self):
        self.assertEqual(rate_mm_per_hour(1.0, "amount", 10), 6.0)
        self.assertEqual(rate_mm_per_hour(1.0, "amount", 60), 1.0)

    def test_intensity_is_already_per_hour(self):
        self.assertEqual(rate_mm_per_hour(6.0, "intensity", 5), 6.0)

    def test_hourly_amount_without_interval_passes_through(self):
        self.assertEqual(rate_mm_per_hour(3.0, "amount"), 3.0)

    def test_none_stays_none(self):
        self.assertIsNone(rate_mm_per_hour(None, "amount", 5))

    def test_rate_labels_follow_the_chinese_short_duration_classes(self):
        self.assertEqual(rain_rate_label(20.0), "暴雨")
        self.assertEqual(rain_rate_label(10.0), "大雨")
        self.assertEqual(rain_rate_label(4.0), "中雨")
        self.assertEqual(rain_rate_label(0.5), "小雨")
        self.assertEqual(rain_rate_label(None), "")


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self._rate = settings.rain_alert_min_rate_mm_h
        self._pop = settings.rain_alert_min_pop_pct
        settings.rain_alert_min_rate_mm_h = 1.0
        settings.rain_alert_min_pop_pct = 60.0

    def tearDown(self):
        settings.rain_alert_min_rate_mm_h = self._rate
        settings.rain_alert_min_pop_pct = self._pop

    def test_trace_drizzle_no_longer_alerts(self):
        # 0.05mm/5min = 0.6mm/h, below the 1.0 floor.
        signal = evaluate_rain(weather(minutely=minutely([0.0, 0.05, 0.05])))
        self.assertFalse(signal.will_rain)
        self.assertAlmostEqual(signal.rate_mm_h, 0.6, places=6)

    def test_real_rain_alerts_with_a_label(self):
        # 0.5mm/5min = 6mm/h → 中雨
        signal = evaluate_rain(weather(minutely=minutely([0.1, 0.5, 0.3])))
        self.assertTrue(signal.will_rain)
        self.assertAlmostEqual(signal.rate_mm_h, 6.0, places=6)
        self.assertEqual(signal.label, "中雨")
        self.assertEqual(signal.reason, "分钟级降水")

    def test_threshold_is_configurable(self):
        data = weather(minutely=minutely([0.2]))  # 2.4mm/h
        settings.rain_alert_min_rate_mm_h = 1.0
        self.assertTrue(evaluate_rain(data).will_rain)
        settings.rain_alert_min_rate_mm_h = 8.0
        self.assertFalse(evaluate_rain(data).will_rain)

    def test_explicit_override_beats_the_setting(self):
        data = weather(minutely=minutely([0.2]))  # 2.4mm/h
        self.assertFalse(evaluate_rain(data, min_rate=8.0).will_rain)
        self.assertTrue(evaluate_rain(data, min_rate=1.0).will_rain)

    def test_is_raining_no_longer_bypasses_the_floor(self):
        # A measurable but tiny rate must not alert just because is_raining.
        data = weather(is_raining=True, now_precip=0.05, now_precip_kind="amount")
        self.assertFalse(evaluate_rain(data).will_rain)

    def test_is_raining_still_alerts_when_no_rate_is_measurable(self):
        data = weather(is_raining=True)
        signal = evaluate_rain(data)
        self.assertTrue(signal.will_rain)
        self.assertIsNone(signal.rate_mm_h)
        self.assertEqual(signal.reason, "实况")

    def test_high_probability_alone_still_alerts(self):
        data = weather(hourly=hourly([0.0, 0.0], pops=[80.0, 20.0]))
        signal = evaluate_rain(data)
        self.assertTrue(signal.will_rain)
        self.assertEqual(signal.reason, "降水概率")
        self.assertEqual(signal.pop, 80.0)

    def test_moderate_probability_below_the_floor_does_not_alert(self):
        data = weather(hourly=hourly([0.0, 0.0], pops=[40.0, 30.0]))
        self.assertFalse(evaluate_rain(data).will_rain)

    def test_hourly_intensity_is_not_multiplied(self):
        # Caiyun-style 2mm/h intensity stays 2mm/h.
        data = weather(hourly=hourly([2.0], kind="intensity"))
        signal = evaluate_rain(data)
        self.assertAlmostEqual(signal.rate_mm_h, 2.0, places=6)
        self.assertTrue(signal.will_rain)

    def test_minutely_wins_over_hourly_when_present(self):
        data = weather(
            minutely=minutely([0.0, 0.0]),          # 0mm/h
            hourly=hourly([5.0], pops=[90.0]),      # would alert
        )
        # Minutely data is authoritative for the near window.
        self.assertFalse(evaluate_rain(data).will_rain)

    def test_stale_and_far_future_minutely_points_are_ignored(self):
        now = datetime.now()
        data = weather(
            minutely=[
                MinutelyPrecipitation(
                    time=now - timedelta(minutes=30), precip=5.0,
                    precip_kind="amount", interval_minutes=5,
                ),
                MinutelyPrecipitation(
                    time=now + timedelta(hours=5), precip=5.0,
                    precip_kind="amount", interval_minutes=5,
                ),
            ]
        )
        self.assertFalse(evaluate_rain(data, minutes=30).will_rain)

    def test_no_data_at_all_does_not_alert(self):
        self.assertFalse(evaluate_rain(weather()).will_rain)

    def test_boolean_wrapper_matches_the_signal(self):
        data = weather(minutely=minutely([0.5]))
        self.assertEqual(will_rain_soon(data), evaluate_rain(data).will_rain)

    def test_signal_label_is_empty_without_a_rate(self):
        self.assertEqual(RainSignal(True).label, "")


if __name__ == "__main__":
    unittest.main()
