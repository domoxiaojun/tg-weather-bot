import asyncio
import os
import unittest


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.qweather import QWeatherAdapter
from core.config import settings


class QWeatherProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_hourly_profile_requests_components_concurrently_and_forces_refresh(self):
        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        expected_calls = 5
        all_started = asyncio.Event()
        calls = []

        async def fake_cached_request(
            cache_key,
            endpoint,
            params,
            *,
            ttl,
            unavailable_ttl=None,
            allow_data_unavailable=False,
            force_refresh=False,
        ):
            calls.append((endpoint, force_refresh))
            if len(calls) == expected_calls:
                all_started.set()
            await all_started.wait()
            if endpoint == "/v7/weather/now":
                return {
                    "updateTime": "2026-07-25T20:00+08:00",
                    "now": {
                        "obsTime": "2026-07-25T20:00+08:00",
                        "temp": "30",
                        "text": "多云",
                        "icon": "101",
                    },
                }
            if endpoint == f"/v7/weather/{settings.qweather_hourly_hours}":
                return {
                    "hourly": [
                        {
                            "fxTime": "2026-07-25T21:00+08:00",
                            "temp": "29",
                            "text": "多云",
                            "icon": "101",
                        }
                    ]
                }
            return adapter._unavailable_marker(endpoint)

        adapter._cached_request = fake_cached_request
        result = await asyncio.wait_for(
            adapter.get_weather(
                "北京",
                profile="hourly",
                refresh_qweather=True,
                loc_info={
                    "id": "101010100",
                    "lon": "116.4",
                    "lat": "39.9",
                    "name": "北京",
                    "adm1": "北京",
                },
            ),
            timeout=1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(len(calls), expected_calls)
        self.assertTrue(all(force for _, force in calls))
        endpoints = {endpoint for endpoint, _ in calls}
        self.assertEqual(
            endpoints,
            {
                "/v7/weather/now",
                f"/v7/weather/{settings.qweather_hourly_hours}",
                "/airquality/v1/current/39.9/116.4",
                "/airquality/v1/hourly/39.9/116.4",
                "/weatheralert/v1/current/39.9/116.4",
            },
        )
        self.assertEqual(len(result.hourly), 2)
        self.assertTrue(all(hour.precip is None for hour in result.hourly))
        self.assertTrue(all(hour.pop is None for hour in result.hourly))

    async def test_invalid_profile_is_rejected_before_network_calls(self):
        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        with self.assertRaises(ValueError):
            await adapter.get_weather("北京", profile="unsupported")


if __name__ == "__main__":
    unittest.main()
