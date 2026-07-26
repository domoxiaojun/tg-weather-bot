"""Tide and solar radiation tests.

Verified field names: tide returns tideTable[]{fxTime,height,type H/L} plus
tideHourly[]{fxTime,height} and needs a tide-station id from POI lookup
(type=TSTA, array key "poi", verified live). Solar radiation returns
forecasts[]{forecastTime,ghi,dhi,dni,solarAngle} — the published docs call the
direct component "ni" but the live API returns "dni"; only ghi is consumed.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.qweather import QWeatherAdapter
from core.config import settings
from domain.models import HourlyForecast, TideExtreme, TideForecast, TideStation
from services.visualizer import Visualizer
from utils.rich_formatter import build_tide_blocks

TZ = timezone(timedelta(hours=8))


class SolarRadiationMappingTests(unittest.TestCase):
    def test_maps_ghi_keyed_by_hour(self):
        payload = {
            "forecasts": [
                {"forecastTime": "2026-07-26T12:00+08:00", "ghi": "820", "dhi": "120", "ni": "700"},
                {"forecastTime": "2026-07-26T13:00+08:00", "ghi": "760"},
            ]
        }
        mapped = QWeatherAdapter._map_solar_radiation(payload)
        self.assertEqual(len(mapped), 2)
        self.assertIn(820.0, mapped.values())

    def test_sub_hourly_samples_keep_the_strongest(self):
        payload = {
            "forecasts": [
                {"forecastTime": "2026-07-26T12:00+08:00", "ghi": "500"},
                {"forecastTime": "2026-07-26T12:30+08:00", "ghi": "900"},
            ]
        }
        mapped = QWeatherAdapter._map_solar_radiation(payload)
        self.assertEqual(len(mapped), 1)
        self.assertEqual(list(mapped.values())[0], 900.0)

    def test_entries_without_ghi_or_time_are_skipped(self):
        payload = {
            "forecasts": [
                {"forecastTime": "2026-07-26T12:00+08:00"},
                {"ghi": "500"},
                {"forecastTime": "bad", "ghi": "500"},
            ]
        }
        self.assertEqual(QWeatherAdapter._map_solar_radiation(payload), {})

    def test_missing_or_unavailable_payload(self):
        self.assertEqual(QWeatherAdapter._map_solar_radiation(None), {})
        self.assertEqual(QWeatherAdapter._map_solar_radiation({}), {})
        marker = QWeatherAdapter._unavailable_marker("/solarradiation/v1/forecast/1/1")
        self.assertEqual(QWeatherAdapter._map_solar_radiation(marker), {})

    def test_radiation_is_fill_only_and_never_overwrites(self):
        """Mirrors the fusion rule: an existing value wins over the new source."""
        solar = QWeatherAdapter._map_solar_radiation({
            "forecasts": [{"forecastTime": "2026-07-26T12:00+08:00", "ghi": "820"}]
        })
        hour_with_value = HourlyForecast(
            time=datetime(2026, 7, 26, 12, tzinfo=TZ), temp=30, text="晴", icon="100", radiation=111.0
        )
        hour_without = HourlyForecast(
            time=datetime(2026, 7, 26, 12, tzinfo=TZ), temp=30, text="晴", icon="100"
        )
        for hour in (hour_with_value, hour_without):
            key = ("utc", int(hour.time.timestamp() // 3600))
            value = solar.get(key)
            if hour.radiation is None and value is not None:
                hour.radiation = value
        self.assertEqual(hour_with_value.radiation, 111.0)
        self.assertEqual(hour_without.radiation, 820.0)


class FakeAdapter(QWeatherAdapter):
    """Adapter with the HTTP layer replaced by canned payloads."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.requested = []

    async def _cached_request(self, cache_key, endpoint, params, **kwargs):
        self.requested.append((endpoint, params))
        return self.payloads.get(endpoint)


class TideAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_station_lookup_uses_tsta_and_sorts_by_distance(self):
        adapter = FakeAdapter({
            "/geo/v2/poi/lookup": {
                "poi": [
                    {"id": "P66981", "name": "远站", "lon": "123.00", "lat": "31.00"},
                    {"id": "P00001", "name": "近站", "lon": "121.50", "lat": "31.20"},
                ]
            }
        })
        stations = await adapter.get_tide_stations(121.47, 31.23)
        self.assertEqual([s.name for s in stations], ["近站", "远站"])
        self.assertEqual(adapter.requested[0][1]["type"], "TSTA")
        self.assertIsNotNone(stations[0].distance_km)

    async def test_no_stations_returns_empty(self):
        adapter = FakeAdapter({"/geo/v2/poi/lookup": None})
        self.assertEqual(await adapter.get_tide_stations(121.47, 31.23), [])

    async def test_tide_maps_extremes_and_curve(self):
        adapter = FakeAdapter({
            "/v7/ocean/tide": {
                "tideTable": [
                    {"fxTime": "2026-07-26T03:20+08:00", "height": "1.20", "type": "L"},
                    {"fxTime": "2026-07-26T09:40+08:00", "height": "4.60", "type": "H"},
                ],
                "tideHourly": [
                    {"fxTime": f"2026-07-26T{hour:02d}:00+08:00", "height": str(2 + hour % 3)}
                    for hour in range(24)
                ],
            }
        })
        station = TideStation(id="P66981", name="测试站", lon=121.5, lat=31.2)
        forecast = await adapter.get_tide(station, datetime(2026, 7, 26).date())
        self.assertIsNotNone(forecast)
        self.assertEqual(len(forecast.extremes), 2)
        self.assertFalse(forecast.extremes[0].is_high)
        self.assertTrue(forecast.extremes[1].is_high)
        self.assertEqual(len(forecast.hourly), 24)
        self.assertEqual(adapter.requested[-1][1]["location"], "P66981")
        self.assertEqual(adapter.requested[-1][1]["date"], "20260726")

    async def test_tide_without_any_data_is_none(self):
        adapter = FakeAdapter({"/v7/ocean/tide": {"tideTable": [], "tideHourly": []}})
        station = TideStation(id="P1", name="站")
        self.assertIsNone(await adapter.get_tide(station, datetime(2026, 7, 26).date()))

    async def test_tide_skips_malformed_entries(self):
        adapter = FakeAdapter({
            "/v7/ocean/tide": {
                "tideTable": [
                    {"fxTime": "bad", "height": "1.0", "type": "H"},
                    {"fxTime": "2026-07-26T09:40+08:00", "height": "", "type": "H"},
                    {"fxTime": "2026-07-26T10:40+08:00", "height": "3.3", "type": "H"},
                ],
                "tideHourly": [],
            }
        })
        forecast = await adapter.get_tide(TideStation(id="P1", name="站"), datetime(2026, 7, 26).date())
        self.assertEqual(len(forecast.extremes), 1)
        self.assertEqual(forecast.extremes[0].height, 3.3)


def sample_forecast() -> TideForecast:
    day = datetime(2026, 7, 26)
    hourly = [(day.replace(hour=hour), 2.5 + 1.8 * ((hour % 12) / 12)) for hour in range(24)]
    return TideForecast(
        station=TideStation(id="P66981", name="吴淞口", lon=121.5, lat=31.4, distance_km=12.0),
        date=day,
        extremes=[
            TideExtreme(time=day.replace(hour=3, minute=0), height=1.2, is_high=False),
            TideExtreme(time=day.replace(hour=11, minute=0), height=4.3, is_high=True),
        ],
        hourly=hourly,
    )


class TideRenderingTests(unittest.TestCase):
    def test_blocks_contain_station_and_extreme_table(self):
        blocks = build_tide_blocks(sample_forecast())
        rendered = str(blocks)
        self.assertEqual(blocks[0]["type"], "heading")
        self.assertIn("吴淞口", rendered)
        self.assertIn("高潮", rendered)
        self.assertIn("低潮", rendered)

    def test_blocks_handle_missing_extremes(self):
        forecast = sample_forecast()
        forecast.extremes = []
        self.assertIn("没有高低潮数据", str(build_tide_blocks(forecast)))

    def test_tide_chart_renders_png(self):
        png = Visualizer.draw_tide_chart(sample_forecast())
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_tide_chart_needs_a_curve(self):
        forecast = sample_forecast()
        forecast.hourly = forecast.hourly[:2]
        self.assertIsNone(Visualizer.draw_tide_chart(forecast))


class ConfigTests(unittest.TestCase):
    def test_tide_command_is_enabled_by_default(self):
        # Solar radiation and history have no flag: they only fill existing
        # fields and degrade silently, so a switch would be dead config.
        self.assertTrue(settings.enable_tide)


if __name__ == "__main__":
    unittest.main()
