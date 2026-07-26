import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import settings
from domain.models import (
    AirQuality,
    DailyForecast,
    HourlyForecast,
    LifeIndex,
    MinutelyPrecipitation,
    WeatherData,
)
from services.fusion import WeatherFusionService


START = datetime(2026, 7, 25, 20, tzinfo=timezone(timedelta(hours=8)))


def qweather_data():
    return WeatherData(
        source="qweather",
        location_name="北京",
        coords="116.4,39.9",
        now_temp=30,
        now_feels_like=33,
        now_text="多云",
        now_icon="101",
        now_precip=0,
        now_precip_kind="amount",
        now_precip_source="qweather",
        minutely=[
            MinutelyPrecipitation(
                time=START,
                precip=0,
                probability=None,
                source="qweather",
            )
        ],
        hourly=[
            HourlyForecast(
                time=START,
                temp=30,
                text="多云",
                icon="101",
                precip=0,
                precip_kind="amount",
                precip_source="qweather",
            )
        ],
        daily=[
            DailyForecast(
                date=START.replace(tzinfo=None),
                temp_min=24,
                temp_max=33,
                text_day="多云",
                icon_day="101",
                text_night="多云",
                icon_night="151",
                precip=0,
                precip_kind="amount",
                precip_source="qweather",
            )
        ],
        air_quality=AirQuality(aqi=0, category="优", source="qweather"),
        indices=[LifeIndex(type="3", name="穿衣", category="炎热", source="qweather")],
        summary="和风摘要",
        provider_summaries={"qweather": "和风摘要"},
    )


def caiyun_data():
    return WeatherData(
        source="caiyun",
        location_name="Current Location",
        coords="116.4,39.9",
        now_temp=31,
        now_feels_like=36,
        now_text="小雨",
        now_icon="🌧️",
        now_precip=2,
        now_precip_kind="intensity",
        now_precip_source="caiyun",
        now_radiation=200,
        minutely=[
            MinutelyPrecipitation(
                time=START,
                precip=2,
                probability=0.9,
                source="caiyun",
            )
        ],
        hourly=[
            HourlyForecast(
                time=START,
                temp=31,
                feels_like=36,
                feels_like_source="caiyun",
                text="小雨",
                icon="🌧️",
                precip=4,
                precip_kind="intensity",
                precip_source="caiyun",
                visibility=8,
            ),
            HourlyForecast(
                time=START + timedelta(hours=1),
                temp=30,
                feels_like=35,
                feels_like_source="caiyun",
                text="小雨",
                icon="🌧️",
                precip=3,
                precip_kind="intensity",
                precip_source="caiyun",
            ),
        ],
        daily=[
            DailyForecast(
                date=START.replace(tzinfo=None),
                temp_min=25,
                temp_max=34,
                text_day="小雨",
                icon_day="🌧️",
                text_night="小雨",
                icon_night="🌧️",
                precip=1,
                precip_kind="intensity",
                precip_source="caiyun",
                precip_day_probability=60,
            ),
            DailyForecast(
                date=(START + timedelta(days=1)).replace(tzinfo=None),
                temp_min=25,
                temp_max=32,
                text_day="小雨",
                icon_day="🌧️",
                text_night="阴",
                icon_night="☁️",
            ),
        ],
        air_quality=AirQuality(aqi=100, category="良", pm2p5=40, source="caiyun"),
        indices=[
            LifeIndex(type="3", name="穿衣", category="热", source="caiyun"),
            LifeIndex(type="2", name="洗车", category="不宜", source="caiyun"),
        ],
        summary="彩云摘要",
        provider_summaries={"caiyun": "彩云摘要"},
    )


class FusionMergeTests(unittest.TestCase):
    def test_qweather_zeroes_and_minutely_data_are_never_overwritten(self):
        merged = WeatherFusionService._merge_weather(
            qweather_data(),
            caiyun_data(),
            "full",
        )

        self.assertEqual(merged.now_precip, 0)
        self.assertEqual(merged.now_precip_source, "qweather")
        self.assertEqual(merged.minutely[0].source, "qweather")
        self.assertEqual(merged.minutely[0].precip, 0)
        self.assertEqual(merged.air_quality.aqi, 0)
        self.assertEqual(merged.hourly[0].precip, 0)
        self.assertEqual(merged.hourly[0].precip_kind, "amount")

        self.assertEqual(merged.hourly[0].feels_like, 36)
        self.assertEqual(merged.hourly[0].visibility, 8)
        self.assertEqual(len(merged.hourly), 2)
        self.assertEqual(merged.daily[0].precip, 0)
        self.assertEqual(merged.daily[0].precip_day_probability, 60)
        self.assertEqual(len(merged.daily), 2)
        self.assertEqual({index.type for index in merged.indices}, {"2", "3"})
        self.assertEqual(merged.now_radiation, 200)


class FakeQWeather:
    def __init__(self, result):
        self.result = result
        self.refresh_values = []
        self.q_started = asyncio.Event()
        self.cy_started = None

    async def get_geo_location(self, location):
        return {"id": "101010100", "lon": "116.4", "lat": "39.9", "name": "北京", "adm1": "北京"}

    async def get_weather(self, location, *, profile, refresh_qweather, loc_info):
        self.refresh_values.append((profile, refresh_qweather, loc_info["id"]))
        self.q_started.set()
        if self.cy_started is not None:
            await self.cy_started.wait()
        return self.result


class FakeCaiyun:
    def __init__(self, result):
        self.result = result
        self.cy_started = asyncio.Event()
        self.q_started = None
        self.calls = []

    async def get_weather(self, coords):
        self.calls.append(coords)
        self.cy_started.set()
        if self.q_started is not None:
            await self.q_started.wait()
        return self.result


class FusionRequestTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_enable = settings.enable_caiyun_api
        self.old_token = settings.caiyun_api_token
        settings.enable_caiyun_api = True
        settings.caiyun_api_token = "test-token"

    async def asyncTearDown(self):
        settings.enable_caiyun_api = self.old_enable
        settings.caiyun_api_token = self.old_token

    async def test_sources_start_concurrently_and_refresh_only_targets_qweather(self):
        service = WeatherFusionService.__new__(WeatherFusionService)
        qweather = FakeQWeather(qweather_data())
        caiyun = FakeCaiyun(caiyun_data())
        qweather.cy_started = caiyun.cy_started
        caiyun.q_started = qweather.q_started
        service.qweather = qweather
        service.caiyun = caiyun

        result = await asyncio.wait_for(
            service.get_fused_weather(
                "北京",
                profile="hourly",
                refresh_qweather=True,
            ),
            timeout=1,
        )

        self.assertIsNotNone(result)
        self.assertEqual(qweather.refresh_values, [("hourly", True, "101010100")])
        self.assertEqual(caiyun.calls, ["116.4,39.9"])

    async def test_qweather_weather_failure_uses_caiyun_with_geo_name(self):
        service = WeatherFusionService.__new__(WeatherFusionService)
        service.qweather = FakeQWeather(None)
        service.caiyun = FakeCaiyun(caiyun_data())

        result = await service.get_fused_weather("北京", profile="full")

        self.assertIsNotNone(result)
        self.assertEqual(result.location_name, "北京, 北京")
        self.assertEqual(result.coords, "116.4,39.9")
        self.assertEqual(result.source, "caiyun")

    async def test_service_closes_both_provider_clients(self):
        class Closable:
            def __init__(self):
                self.closed = False

            async def aclose(self):
                self.closed = True

        service = WeatherFusionService.__new__(WeatherFusionService)
        service.qweather = Closable()
        service.caiyun = Closable()

        await service.aclose()

        self.assertTrue(service.qweather.closed)
        self.assertTrue(service.caiyun.closed)


if __name__ == "__main__":
    unittest.main()
