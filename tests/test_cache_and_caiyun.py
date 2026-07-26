import asyncio
import os
import time
import unittest

import httpx


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.caiyun import CaiyunAdapter
from core.config import settings
from utils.cache import CacheManager, cache


class RecordingClient:
    def __init__(self, payload=None, status_code=200, delay=0.0):
        self.payload = payload or {}
        self.status_code = status_code
        self.delay = delay
        self.calls = []
        self.closed = False

    async def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if self.delay:
            await asyncio.sleep(self.delay)
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(
            self.status_code,
            json=self.payload,
            request=request,
        )

    async def aclose(self):
        self.closed = True


def caiyun_payload():
    hour = "2026-07-25T20:00+08:00"
    day = "2026-07-25"
    return {
        "status": "ok",
        "timezone": "Asia/Shanghai",
        "server_time": 1784980800,
        "result": {
            "forecast_keypoint": "未来两小时天气平稳",
            "realtime": {
                "temperature": 30,
                "apparent_temperature": 35,
                "humidity": 0.65,
                "cloudrate": 0.4,
                "skycon": "CLEAR_DAY",
                "visibility": 12,
                "dswrf": 520,
                "pressure": 100200,
                "wind": {"speed": 10, "direction": 180},
                "precipitation": {"local": {"intensity": 0.2}},
                "air_quality": {
                    "aqi": {"chn": 48},
                    "description": {"chn": "优"},
                    "pm25": 18,
                    "pm10": 35,
                    "o3": 80,
                    "so2": 5,
                    "no2": 20,
                    "co": 0.6,
                },
            },
            # Even if an unexpected block appears, this package is not used as
            # a minute-level source by the adapter.
            "minutely": {
                "probability": [0.9],
                "precipitation_2h": [1.2],
            },
            "hourly": {
                "temperature": [{"datetime": hour, "value": 29}],
                "apparent_temperature": [{"datetime": hour, "value": 34}],
                "skycon": [{"datetime": hour, "value": "LIGHT_RAIN"}],
                "precipitation": [{"datetime": hour, "value": 1.5, "probability": 0.7}],
                "wind": [{"datetime": hour, "speed": 12, "direction": 190}],
                "humidity": [{"datetime": hour, "value": 0.75}],
                "pressure": [{"datetime": hour, "value": 100000}],
                "cloudrate": [{"datetime": hour, "value": 0.8}],
                "visibility": [{"datetime": hour, "value": 8}],
                "dswrf": [{"datetime": hour, "value": 200}],
                "air_quality": {
                    "aqi": [{"datetime": hour, "value": {"chn": 52}}],
                    "pm25": [{"datetime": hour, "value": 22}],
                },
            },
            "daily": {
                "temperature": [{"date": day, "min": 25, "max": 33, "avg": 29}],
                "skycon_08h_20h": [{"date": day, "value": "PARTLY_CLOUDY_DAY"}],
                "skycon_20h_32h": [{"date": day, "value": "LIGHT_RAIN"}],
                "precipitation": [{"date": day, "avg": 0.5, "probability": 0.6}],
                "precipitation_08h_20h": [{"date": day, "avg": 0.2, "probability": 0.4}],
                "precipitation_20h_32h": [{"date": day, "avg": 0.8, "probability": 0.75}],
                "humidity": [{"date": day, "avg": 0.7}],
                "pressure": [{"date": day, "avg": 100100}],
                "cloudrate": [{"date": day, "avg": 0.6}],
                "visibility": [{"date": day, "avg": 10}],
                "dswrf": [{"date": day, "avg": 300}],
                "astro": [{"date": day, "sunrise": {"time": "05:10"}, "sunset": {"time": "19:10"}}],
                "life_index": {
                    "dressing": [{"date": day, "index": "3", "desc": "炎热"}],
                    "carWashing": [{"date": day, "index": "4", "desc": "不宜"}],
                    "coldRisk": [{"date": day, "index": "1", "desc": "低发"}],
                    "ultraviolet": [{"date": day, "index": "4", "desc": "强"}],
                },
            },
            "alert": {
                "content": [
                    {
                        "title": "高温红色预警",
                        "code": "0104",
                        "status": "预警中",
                        "description": "注意防暑",
                        "pubtimestamp": 1784980800,
                        "source": "测试气象台",
                        "alertId": "alert-1",
                    }
                ]
            },
        },
    }


class CacheSingleFlightTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_waiters_share_factory_and_cancellation_isolated(self):
        manager = CacheManager()
        manager._redis_unavailable_until = float("inf")
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"value": 42}

        first = asyncio.create_task(manager.get_or_set("shared", factory, ttl=60))
        await started.wait()
        second = asyncio.create_task(manager.get_or_set("shared", factory, ttl=60))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()

        self.assertEqual(await second, {"value": 42})
        self.assertEqual(calls, 1)
        self.assertEqual(await manager.get("shared"), {"value": 42})
        await manager.close()

    async def test_expired_value_runs_factory_again(self):
        manager = CacheManager()
        manager._redis_unavailable_until = float("inf")
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            return calls

        self.assertEqual(await manager.get_or_set("ttl", factory, ttl=60), 1)
        expires_at, raw = manager._memory_cache["ttl"]
        manager._memory_cache["ttl"] = (time.monotonic() - 1, raw)
        self.assertEqual(await manager.get_or_set("ttl", factory, ttl=60), 2)
        await manager.close()


class CaiyunCachingAndMappingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await cache.cancel_inflight()
        cache._memory_cache.clear()
        cache.redis = None
        cache._redis_unavailable_until = float("inf")
        self.original_settings = {
            "caiyun_cache_ttl_seconds": settings.caiyun_cache_ttl_seconds,
            "caiyun_failure_cooldown_seconds": settings.caiyun_failure_cooldown_seconds,
            "caiyun_hourly_steps": settings.caiyun_hourly_steps,
            "caiyun_daily_steps": settings.caiyun_daily_steps,
        }
        settings.caiyun_cache_ttl_seconds = 3600
        settings.caiyun_failure_cooldown_seconds = 60
        settings.caiyun_hourly_steps = 72
        settings.caiyun_daily_steps = 15

    async def asyncTearDown(self):
        await cache.cancel_inflight()
        cache._memory_cache.clear()
        for key, value in self.original_settings.items():
            setattr(settings, key, value)

    async def test_twenty_concurrent_calls_use_one_paid_request_and_exact_contract(self):
        adapter = CaiyunAdapter()
        adapter.token = "test-caiyun-token"
        client = RecordingClient(caiyun_payload(), delay=0.01)
        await adapter.client.aclose()
        adapter.client = client

        results = await asyncio.gather(
            *(adapter.get_weather("116.40001,39.90001") for _ in range(20))
        )

        self.assertEqual(len(client.calls), 1)
        url, params = client.calls[0]
        self.assertTrue(url.endswith("/116.400010,39.900010/weather"))
        self.assertNotIn(".json", url)
        self.assertEqual(
            params,
            {"alert": "true", "dailysteps": "15", "hourlysteps": "72", "unit": "metric:v2"},
        )
        self.assertTrue(all(result is not None for result in results))

        weather = results[0]
        self.assertEqual(weather.now_feels_like, 35)
        self.assertEqual(weather.now_pressure, 1002)
        self.assertEqual(weather.now_precip_kind, "intensity")
        self.assertEqual(weather.minutely, [])
        self.assertEqual(weather.hourly[0].feels_like, 34)
        self.assertEqual(weather.hourly[0].precip_kind, "intensity")
        self.assertEqual(weather.hourly[0].pressure, 1000)
        self.assertEqual(weather.hourly[0].aqi, 52)
        self.assertEqual(weather.daily[0].precip_day_probability, 40)
        self.assertEqual({item.type for item in weather.indices}, {"2", "3", "5", "9"})
        self.assertEqual(weather.alerts[0].level, "红色")
        self.assertEqual(weather.alerts[0].status, "预警中")
        await adapter.aclose()

    async def test_coordinate_aliases_share_rounded_cache_key(self):
        adapter = CaiyunAdapter()
        adapter.token = "test-caiyun-token"
        client = RecordingClient(caiyun_payload())
        await adapter.client.aclose()
        adapter.client = client

        first = await adapter.get_weather("116.40001,39.90001")
        second = await adapter.get_weather("116.40002,39.90002")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(len(client.calls), 1)
        await adapter.aclose()

    async def test_http_failure_sets_cooldown_and_avoids_immediate_retry(self):
        adapter = CaiyunAdapter()
        adapter.token = "test-caiyun-token"
        client = RecordingClient({"error": "temporary"}, status_code=500)
        await adapter.client.aclose()
        adapter.client = client

        self.assertIsNone(await adapter.get_weather("116.4,39.9"))
        self.assertIsNone(await adapter.get_weather("116.4,39.9"))
        self.assertEqual(len(client.calls), 1)
        cooldown_keys = [key for key in cache._memory_cache if key.endswith(":cooldown")]
        self.assertEqual(len(cooldown_keys), 1)
        await adapter.aclose()

    async def test_authentication_failure_uses_one_hour_cooldown(self):
        adapter = CaiyunAdapter()
        adapter.token = "invalid-test-token"
        client = RecordingClient({"error": "unauthorized"}, status_code=401)
        await adapter.client.aclose()
        adapter.client = client

        self.assertIsNone(await adapter.get_weather("116.4,39.9"))

        cooldown_key = next(key for key in cache._memory_cache if key.endswith(":cooldown"))
        expires_at, _ = cache._memory_cache[cooldown_key]
        self.assertIsNotNone(expires_at)
        self.assertGreater(expires_at - time.monotonic(), 3500)
        await adapter.aclose()


if __name__ == "__main__":
    unittest.main()
