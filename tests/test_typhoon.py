"""Tropical cyclone assessment tests.

Field names follow the official docs: storm-list gives id/name/isActive,
storm-track gives now + track[], storm-forecast gives forecast[] with
fxTime/lat/lon/type/pressure/windSpeed and no name.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from adapters.qweather import QWeatherAdapter
from core.config import settings
from domain.models import TropicalStorm, TyphoonPoint, TyphoonWindRadius
from services.typhoon import (
    assess_storm,
    assess_storms,
    bearing_deg,
    distance_km,
    format_threat_summary,
    storm_type_label,
)
from services.visualizer import Visualizer
from utils.rich_formatter import build_typhoon_push_blocks

TZ = timezone(timedelta(hours=8))
BASE = datetime(2026, 7, 26, 16, tzinfo=TZ)


def point(hours: float, lat: float, lon: float, **kwargs) -> TyphoonPoint:
    return TyphoonPoint(
        time=BASE + timedelta(hours=hours),
        lat=lat,
        lon=lon,
        type=kwargs.pop("type", "TY"),
        pressure=kwargs.pop("pressure", 960.0),
        wind_speed=kwargs.pop("wind_speed", 140.0),
        move_dir=kwargs.pop("move_dir", "西北"),
        move_speed=kwargs.pop("move_speed", 18.0),
        **kwargs,
    )


def storm(**kwargs) -> TropicalStorm:
    now = kwargs.pop(
        "now",
        point(
            0,
            22.0,
            128.0,
            radius30=TyphoonWindRadius(ne=300, se=280, sw=250, nw=260),
            radius50=TyphoonWindRadius(ne=150, se=140, sw=120, nw=130),
        ),
    )
    return TropicalStorm(
        id=kwargs.pop("id", "NP2618"),
        name=kwargs.pop("name", "海燕"),
        basin="NP",
        year="2026",
        now=now,
        track=kwargs.pop("track", [point(-6 * i, 20.0 + i * 0.3, 132.0 + i * 0.8) for i in range(3, 0, -1)]),
        forecast=kwargs.pop("forecast", [point(6 * i, 22.0 + i, 128.0 - i * 1.4) for i in range(1, 5)]),
        **kwargs,
    )


class GeometryTests(unittest.TestCase):
    def test_bearing_cardinal_directions(self):
        self.assertAlmostEqual(bearing_deg(120.0, 30.0, 120.0, 31.0), 0.0, places=1)
        self.assertAlmostEqual(bearing_deg(120.0, 30.0, 121.0, 30.0), 90.0, delta=0.5)
        self.assertAlmostEqual(bearing_deg(120.0, 30.0, 120.0, 29.0), 180.0, places=1)
        self.assertAlmostEqual(bearing_deg(120.0, 30.0, 119.0, 30.0), 270.0, delta=0.5)

    def test_wind_radius_quadrant_selection(self):
        radius = TyphoonWindRadius(ne=100, se=200, sw=300, nw=400)
        self.assertEqual(radius.for_bearing(45), 100)    # NE
        self.assertEqual(radius.for_bearing(135), 200)   # SE
        self.assertEqual(radius.for_bearing(225), 300)   # SW
        self.assertEqual(radius.for_bearing(315), 400)   # NW
        self.assertEqual(radius.for_bearing(360), 100)   # wraps to NE

    def test_storm_type_labels(self):
        self.assertEqual(storm_type_label("TY"), "台风")
        self.assertEqual(storm_type_label("SuperTY"), "超强台风")
        self.assertEqual(storm_type_label(""), "热带气旋")
        self.assertEqual(storm_type_label("XX"), "XX")


class ThreatAssessmentTests(unittest.TestCase):
    def test_inside_inner_circle_is_the_more_severe_level(self):
        # ~60km east of the centre: inside the 50kt (10级) circle.
        threat = assess_storm(storm(), 128.6, 22.0)
        self.assertIsNotNone(threat)
        self.assertTrue(threat.inside_circle)
        self.assertEqual(threat.wind_label, "10级风圈")
        self.assertEqual(threat.level, "橙色")
        self.assertIn("10级风圈", format_threat_summary(threat))

    def test_between_circles_reports_the_outer_one(self):
        # ~200km away: outside 50kt, inside 30kt (7级).
        threat = assess_storm(storm(), 130.0, 22.0)
        self.assertTrue(threat.inside_circle)
        self.assertEqual(threat.wind_label, "7级风圈")
        self.assertEqual(threat.level, "黄色")

    def test_far_location_is_a_distance_watch_not_a_circle_hit(self):
        # ~400km east: beyond the 300km 30kt circle but inside the watch range.
        threat = assess_storm(storm(), 131.9, 22.0)
        self.assertIsNotNone(threat)
        self.assertFalse(threat.inside_circle)
        self.assertEqual(threat.level, "")
        self.assertTrue(threat.key.startswith("typhoon:NP2618:watch"))
        self.assertAlmostEqual(threat.distance_km, 402, delta=15)

    def test_location_beyond_watch_range_is_ignored(self):
        # Shanghai is ~1200km from this fixture storm and its whole track.
        self.assertIsNone(assess_storm(storm(), 121.47, 31.23))

    def test_very_far_location_is_ignored(self):
        old = settings.typhoon_watch_distance_km
        settings.typhoon_watch_distance_km = 300
        try:
            self.assertIsNone(assess_storm(storm(), 100.0, 10.0))
        finally:
            settings.typhoon_watch_distance_km = old

    def test_forecast_points_can_trigger_even_if_now_is_far(self):
        # Centre starts far away but the track passes close by later.
        far_now = point(0, 10.0, 150.0, radius50=TyphoonWindRadius(ne=120, se=120, sw=120, nw=120))
        approaching = storm(
            now=far_now,
            forecast=[point(24, 22.0, 128.0, radius50=TyphoonWindRadius(ne=120, se=120, sw=120, nw=120))],
            track=[],
        )
        threat = assess_storm(approaching, 128.4, 22.0)
        self.assertTrue(threat.inside_circle)
        self.assertEqual(threat.closest_point.lat, 22.0)

    def test_storm_without_radii_falls_back_to_distance(self):
        bare = storm(now=point(0, 30.9, 121.4), forecast=[], track=[])
        threat = assess_storm(bare, 121.47, 31.23)
        self.assertIsNotNone(threat)
        self.assertFalse(threat.inside_circle)
        self.assertLess(threat.distance_km, 60)

    def test_storm_with_no_points_returns_none(self):
        empty = TropicalStorm(id="NP0000", name="空", now=None, track=[], forecast=[])
        self.assertIsNone(assess_storm(empty, 121.0, 31.0))

    def test_dedup_key_escalates_with_severity_and_distance(self):
        near = assess_storm(storm(), 128.6, 22.0)
        mid = assess_storm(storm(), 130.0, 22.0)
        far = assess_storm(storm(), 131.9, 22.0)
        self.assertNotEqual(near.key, mid.key)
        self.assertNotEqual(mid.key, far.key)
        # Same bucket → same key, so a stationary storm does not re-alert.
        self.assertEqual(assess_storm(storm(), 128.6, 22.0).key, near.key)

    def test_assess_storms_sorts_most_severe_first(self):
        close = storm(id="NP1", name="近", now=point(0, 22.0, 128.0,
                      radius50=TyphoonWindRadius(ne=200, se=200, sw=200, nw=200)), forecast=[], track=[])
        distant = storm(id="NP2", name="远", now=point(0, 25.0, 132.0), forecast=[], track=[])
        threats = assess_storms([distant, close], 128.4, 22.0)
        self.assertEqual(threats[0].storm.id, "NP1")


class StormMappingTests(unittest.TestCase):
    def setUp(self):
        self.adapter = QWeatherAdapter.__new__(QWeatherAdapter)

    def test_maps_track_point_with_wind_radii(self):
        raw = {
            "time": "2026-07-26T16:00+08:00",
            "lat": "22.0",
            "lon": "128.0",
            "type": "TY",
            "pressure": "960",
            "windSpeed": "140",
            "moveSpeed": "18",
            "moveDir": "西北",
            "move360": "315",
            "windRadius30": {"neRadius": "300", "seRadius": "280", "swRadius": "250", "nwRadius": "260"},
        }
        mapped = self.adapter._map_storm_point(raw)
        self.assertEqual(mapped.lat, 22.0)
        self.assertEqual(mapped.wind_speed, 140)
        self.assertEqual(mapped.radius30.ne, 300)
        self.assertIsNone(mapped.radius50)

    def test_forecast_point_uses_fx_time(self):
        mapped = self.adapter._map_storm_point(
            {"fxTime": "2026-07-27T04:00+08:00", "lat": "23", "lon": "127"}
        )
        self.assertEqual(mapped.time.hour, 4)

    def test_point_without_coordinates_is_dropped(self):
        self.assertIsNone(self.adapter._map_storm_point({"lat": "", "lon": ""}))
        self.assertIsNone(self.adapter._map_storm_point(None))

    def test_all_empty_radii_becomes_none(self):
        self.assertIsNone(self.adapter._map_wind_radius({"neRadius": "", "seRadius": None}))


class TyphoonRenderingTests(unittest.TestCase):
    def test_push_blocks_include_position_and_forecast_table(self):
        threat = assess_storm(storm(), 128.6, 22.0)
        blocks = build_typhoon_push_blocks(threat, "上海, 上海市")
        rendered = str(blocks)
        self.assertEqual(blocks[0]["type"], "heading")
        self.assertIn("海燕", rendered)
        self.assertIn("10级风圈", rendered)
        self.assertIn("中心气压", rendered)
        self.assertIn("预测路径", rendered)

    def test_track_chart_renders_png(self):
        png = Visualizer.draw_typhoon_track_chart(storm(), 121.47, 31.23)
        self.assertIsNotNone(png)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_track_chart_without_points_returns_none(self):
        empty = TropicalStorm(id="NP0", name="空", now=None, track=[], forecast=[])
        self.assertIsNone(Visualizer.draw_typhoon_track_chart(empty))


class BasinConfigTests(unittest.TestCase):
    def test_basin_default_is_northwest_pacific(self):
        # QWeather only supports NP today; anything else would return nothing.
        self.assertEqual(settings.typhoon_basin, "NP")

    def test_distance_helper_matches_service_helper(self):
        adapter_km = QWeatherAdapter._distance_km(121.47, 31.23, 128.0, 22.0)
        service_km = distance_km(121.47, 31.23, 128.0, 22.0)
        self.assertAlmostEqual(adapter_km, service_km, places=6)


if __name__ == "__main__":
    unittest.main()
