import asyncio
import base64
import json
import os
import time
import unittest
from datetime import datetime

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import Settings, settings
from domain.models import DailyForecast, WeatherData
from services.external_api import MAX_HEADERS, ExternalWeatherApi
from services.weather_card import _rows
from services.llm import WeatherReportResult


class _Weather:
    def __init__(self, data):
        self.data = data
        self.calls = []

    async def get_fused_weather(self, location, profile):
        self.calls.append((location, profile))
        return self.data if location == "潮安" else None


class _LLM:
    provider = object()

    async def generate_weather_report(self, data):
        return "📝 <b>今天</b> 有雨，出门带伞。"

    async def generate_weather_report_result(self, data):
        return WeatherReportResult(True, text="📝 <b>今天</b> 有雨，出门带伞。")


class _FailingLLM(_LLM):
    async def generate_weather_report_result(self, data):
        return WeatherReportResult(False, error="report_failed")


class _SlowWeather(_Weather):
    def __init__(self, data, delay=0.2):
        super().__init__(data)
        self.delay = delay
        self.started = asyncio.Event()

    async def get_fused_weather(self, location, profile):
        self.started.set()
        await asyncio.sleep(self.delay)
        return await super().get_fused_weather(location, profile)


def make_weather():
    stamp = datetime(2026, 8, 25, 10, 46)
    return WeatherData(
        location_name="潮安, 广东省",
        coords="116.68,23.46",
        update_time=stamp,
        now_temp=30,
        now_text="中雨",
        now_icon="306",
        summary="降雨还将持续 120 分钟",
        daily=[
            DailyForecast(
                date=stamp,
                temp_min=24,
                temp_max=29,
                text_day="中雨",
                icon_day="306",
                text_night="雷阵雨",
                icon_night="302",
                precip=6.1,
            )
        ],
    )


class ExternalApiTests(unittest.IsolatedAsyncioTestCase):
    async def _request(self, api, request: str) -> tuple[int, bytes]:
        server = await asyncio.start_server(
            api._handle_client,
            "127.0.0.1",
            0,
            limit=MAX_HEADERS,
        )
        api._server = server
        api._stopping = False
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(request.encode("ascii"))
        await writer.drain()
        raw = await reader.read()
        writer.close()
        await writer.wait_closed()
        await api.stop()
        header, body = raw.split(b"\r\n\r\n", 1)
        return int(header.split()[1]), body

    async def test_weather_payload_contains_rich_card_and_report(self):
        weather = _Weather(make_weather())
        api = ExternalWeatherApi(weather, _LLM())
        status, payload = await api._weather_payload("潮安", {"report", "image"})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["card"]["format"], "telegram-rich-blocks")
        self.assertIn("fallback_text", payload["card"])
        self.assertEqual(payload["report"]["available"], True)
        self.assertTrue(payload["image"]["base64"])
        json.dumps(payload, ensure_ascii=False)
        self.assertEqual(base64.b64decode(payload["image"]["base64"])[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(weather.calls, [("潮安", "full")])

    async def test_card_endpoint_uses_compact_profile(self):
        weather = _Weather(make_weather())
        api = ExternalWeatherApi(weather, _LLM())
        original = settings.weather_api_token
        settings.weather_api_token = "secret-token"
        try:
            status, body = await self._request(
                api,
                "GET /v1/weather/card.png?city=%E6%BD%AE%E5%AE%89 HTTP/1.1\r\n"
                "Authorization: Bearer secret-token\r\n\r\n",
            )
        finally:
            settings.weather_api_token = original
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(weather.calls, [("潮安", "card")])

    async def test_image_only_json_uses_compact_profile(self):
        weather = _Weather(make_weather())
        api = ExternalWeatherApi(weather, _LLM())
        status, payload = await api._weather_payload("潮安", {"image"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["image"]["base64"])
        self.assertEqual(weather.calls, [("潮安", "card")])

    async def test_real_http_auth_and_request_validation(self):
        weather = _Weather(make_weather())
        api = ExternalWeatherApi(weather, _LLM())
        original = settings.weather_api_token
        settings.weather_api_token = "secret-token"
        try:
            status, body = await self._request(api, "GET /healthz HTTP/1.1\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(body)["ok"])

            status, body = await self._request(api, "GET /v1/weather?city=%E6%BD%AE%E5%AE%89 HTTP/1.1\r\n\r\n")
            self.assertEqual(status, 401)
            self.assertEqual(json.loads(body)["error"], "unauthorized")

            status, body = await self._request(
                api,
                "GET /v1/weather?city=%E6%BD%AE%E5%AE%89&include=unknown HTTP/1.1\r\n"
                "Authorization: Bearer secret-token\r\n\r\n",
            )
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body)["error"], "invalid_include")
        finally:
            settings.weather_api_token = original

    async def test_oversized_headers_are_rejected(self):
        api = ExternalWeatherApi(_Weather(make_weather()), _LLM())
        original = settings.weather_api_token
        settings.weather_api_token = "secret-token"
        try:
            status, body = await self._request(
                api,
                "GET /healthz HTTP/1.1\r\nX-Filler: " + ("a" * MAX_HEADERS) + "\r\n\r\n",
            )
        finally:
            settings.weather_api_token = original
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"], "headers_too_large")

    async def test_report_failure_is_not_marked_available(self):
        api = ExternalWeatherApi(_Weather(make_weather()), _FailingLLM())
        status, payload = await api._weather_payload("潮安", {"report"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["report"], {"available": False, "error": "report_failed", "text": None})

    def test_card_rows_preserve_precipitation_kind(self):
        weather = make_weather()
        weather.daily[0].precip_kind = "intensity"
        self.assertIn(("降水", "6.1mm/h"), _rows(weather))

    async def test_request_timeout_and_shutdown_drain(self):
        weather = _SlowWeather(make_weather(), delay=0.2)
        api = ExternalWeatherApi(weather, _LLM())
        original_timeout = settings.weather_api_timeout_seconds
        original_token = settings.weather_api_token
        settings.weather_api_timeout_seconds = 0.05
        settings.weather_api_token = "secret-token"
        try:
            started = time.perf_counter()
            status, body = await self._request(
                api,
                "GET /v1/weather?city=%E6%BD%AE%E5%AE%89 HTTP/1.1\r\n"
                "Authorization: Bearer secret-token\r\n\r\n",
            )
            self.assertEqual(status, 408)
            self.assertEqual(json.loads(body)["error"], "request_timeout")
            self.assertLess(time.perf_counter() - started, 1.0)
        finally:
            settings.weather_api_timeout_seconds = original_timeout
            settings.weather_api_token = original_token

    async def test_shutdown_waits_for_active_request(self):
        weather = _SlowWeather(make_weather(), delay=0.05)
        api = ExternalWeatherApi(weather, _LLM())
        original_token = settings.weather_api_token
        settings.weather_api_token = "secret-token"
        server = await asyncio.start_server(api._handle_client, "127.0.0.1", 0, limit=MAX_HEADERS)
        api._server = server
        api._stopping = False
        port = server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(
                b"GET /v1/weather?city=%E6%BD%AE%E5%AE%89 HTTP/1.1\r\n"
                b"Authorization: Bearer secret-token\r\n\r\n"
            )
            await writer.drain()
            await weather.started.wait()
            await api.stop()
            raw = await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            settings.weather_api_token = original_token
        self.assertEqual(int(raw.split(b" ", 2)[1]), 200)
        self.assertFalse(api._active_requests)

    async def test_unknown_city_is_not_found(self):
        api = ExternalWeatherApi(_Weather(make_weather()), _LLM())
        status, payload = await api._weather_payload("北京", set())
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "city_not_found")

    def test_bearer_auth_uses_constant_time_token_comparison(self):
        original = settings.weather_api_token
        settings.weather_api_token = "secret-token"
        try:
            self.assertTrue(api_authorized({"authorization": "Bearer secret-token"}))
            self.assertTrue(api_authorized({"x-weather-api-key": "secret-token"}))
            self.assertFalse(api_authorized({"authorization": "Bearer wrong"}))
        finally:
            settings.weather_api_token = original

    def test_probability_threshold_101_disables_probability_only_alerts(self):
        self.assertEqual(Settings.validate_rain_min_pop(101), 101)
        with self.assertRaises(ValueError):
            Settings.validate_rain_min_pop(102)


def api_authorized(headers):
    return ExternalWeatherApi._authorized(headers)


if __name__ == "__main__":
    asyncio.run(ExternalApiTests().test_weather_payload_contains_rich_card_and_report())
