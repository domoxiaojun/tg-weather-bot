import json
import os
import unittest
from datetime import datetime, timedelta, timezone


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.qweather import QWeatherAdapter
from domain.models import DailyForecast, WeatherData
from services.llm import LLMService
from utils.formatter import format_realtime_weather, format_weather_response


LOCAL_TZ = timezone(timedelta(hours=8))


def make_day(day: int, text: str) -> DailyForecast:
    return DailyForecast(
        date=datetime(2026, 7, day),
        temp_min=23,
        temp_max=32,
        text_day=text,
        icon_day="305",
        text_night=f"{text}夜间",
        icon_night="307",
        precip=1,
        precip_kind="amount",
        uv_index="4",
    )


def make_weather(daily: list[DailyForecast]) -> WeatherData:
    return WeatherData(
        update_time=datetime(2026, 7, 26, 6, 41, tzinfo=LOCAL_TZ),
        location_name="潮安, 广东省",
        coords="116.68,23.46",
        now_temp=25,
        now_feels_like=21,
        now_text="小雨",
        now_icon="305",
        daily=daily,
    )


class DailyForecastSelectionTests(unittest.TestCase):
    def test_default_view_selects_update_dates_forecast_instead_of_first_item(self):
        data = make_weather([make_day(25, "昨日中雨"), make_day(26, "今日小雨")])

        selected = data.get_current_daily_forecast()
        rendered = format_weather_response(data)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.date.date(), datetime(2026, 7, 26).date())
        self.assertIn("今日详情", rendered)
        self.assertIn("今日小雨", rendered)
        self.assertNotIn("昨日中雨", rendered)

    def test_daily_view_filters_stale_records_before_applying_day_offset(self):
        data = make_weather(
            [
                make_day(25, "昨日中雨"),
                make_day(26, "今日小雨"),
                make_day(27, "明日多云"),
            ]
        )

        today = format_weather_response(data, view_type="daily", days=1, start_day=0)
        tomorrow = format_weather_response(data, view_type="daily", days=1, start_day=1)

        self.assertIn("今日小雨", today)
        self.assertNotIn("昨日中雨", today)
        self.assertIn("明日多云", tomorrow)
        self.assertNotIn("今日小雨", tomorrow)

        chart_dates, _, _ = data.get_daily_temp_plot_data()
        self.assertEqual([item.day for item in chart_dates], [26, 27])

    def test_past_only_data_is_never_labeled_as_today(self):
        data = make_weather([make_day(25, "昨日中雨")])

        self.assertIsNone(data.get_current_daily_forecast())
        rendered = format_weather_response(data)
        self.assertNotIn("今日详情", rendered)
        self.assertIn("暂无 07\\-26 的今日预报", rendered)
        self.assertIn("已停止把过期预报显示为今日", rendered)

    def test_future_fallback_is_labeled_as_nearest_forecast(self):
        data = make_weather([make_day(27, "明日多云")])

        rendered = format_weather_response(data)

        self.assertIn("最近预报", rendered)
        self.assertNotIn("今日详情", rendered)


class WarningPresentationTests(unittest.TestCase):
    def setUp(self):
        self.adapter = QWeatherAdapter.__new__(QWeatherAdapter)

    def test_qweather_color_codes_are_localized_and_gray_is_unclassified(self):
        alerts = self.adapter._map_alerts(
            {
                "alerts": [
                    {
                        "headline": "潮州市气象台发布暴雨黄色预警",
                        "eventType": {"name": "暴雨"},
                        "color": {"code": "yellow"},
                    },
                    {
                        "headline": "台风防御提醒",
                        "eventType": {"name": "台风"},
                        "color": {"code": "gray"},
                        "severity": {"code": "unknown"},
                    },
                ]
            }
        )

        self.assertEqual(alerts[0].level, "黄色")
        self.assertEqual(alerts[1].level, "")

        data = make_weather([make_day(26, "今日小雨")])
        data.alerts = alerts
        rendered = format_realtime_weather(data)

        self.assertEqual(rendered.count("黄色"), 1)
        self.assertNotIn("yellow", rendered)
        self.assertNotIn("gray", rendered)

    def test_level_is_appended_when_the_title_does_not_already_contain_it(self):
        alert = self.adapter._map_alerts(
            {
                "alerts": [
                    {
                        "headline": "雷雨大风预警",
                        "eventType": {"name": "雷雨大风"},
                        "color": {"code": "yellow"},
                    }
                ]
            }
        )[0]
        data = make_weather([make_day(26, "今日小雨")])
        data.alerts = [alert]

        rendered = format_realtime_weather(data)

        self.assertIn("\\(黄色\\)", rendered)


class LLMForecastConsistencyTests(unittest.TestCase):
    def test_llm_payload_uses_the_same_current_day_and_omits_stale_days(self):
        data = make_weather([make_day(25, "昨日中雨"), make_day(26, "今日小雨")])
        service = LLMService.__new__(LLMService)

        payload = json.loads(service._format_weather_data(data))

        self.assertEqual(payload["daily_forecast"][0]["date"], "07月26日")
        self.assertEqual(payload["daily_forecast"][0]["day_weather"], "今日小雨")
        self.assertEqual(
            payload["risk_signals"]["today_weather_text"],
            "今日小雨/今日小雨夜间",
        )
        self.assertNotIn("昨日中雨", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
