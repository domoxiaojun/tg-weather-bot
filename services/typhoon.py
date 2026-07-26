"""Tropical cyclone threat assessment.

QWeather returns wind-circle radii per quadrant (30/50/64 knots ≈ Beaufort
7/10/12), which answers "will I actually feel this storm" far better than raw
distance to the centre. Distance is kept only as a coarse fallback for storms
that report no radii.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, atan2, cos, degrees, radians, sin, sqrt
from typing import List, Optional

from loguru import logger

from core.config import settings
from domain.models import TropicalStorm, TyphoonPoint

EARTH_RADIUS_KM = 6371.0

STORM_TYPE_LABELS = {
    "TD": "热带低压",
    "TS": "热带风暴",
    "STS": "强热带风暴",
    "TY": "台风",
    "STY": "强台风",
    "SuperTY": "超强台风",
}

# Wind circle → (Beaufort label, alert level). The level feeds the existing
# quiet-hours exemption logic: 红色/橙色 are life-safety and bypass it.
WIND_CIRCLE_TIERS = (
    ("radius64", "12级以上", "红色"),
    ("radius50", "10级", "橙色"),
    ("radius30", "7级", "黄色"),
)


def storm_type_label(raw: str) -> str:
    return STORM_TYPE_LABELS.get(raw, raw or "热带气旋")


def distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    rlon1, rlat1, rlon2, rlat2 = map(radians, (lon1, lat1, lon2, lat2))
    h = sin((rlat2 - rlat1) / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin((rlon2 - rlon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(min(1.0, sqrt(h)))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    rlat1, rlat2 = radians(lat1), radians(lat2)
    dlon = radians(lon2 - lon1)
    y = sin(dlon) * cos(rlat2)
    x = cos(rlat1) * sin(rlat2) - sin(rlat1) * cos(rlat2) * cos(dlon)
    return (degrees(atan2(y, x)) + 360) % 360


@dataclass
class StormThreat:
    """How a storm affects one location."""
    storm: TropicalStorm
    distance_km: float
    level: str          # 红色 / 橙色 / 黄色 / "" (watch only)
    wind_label: str     # e.g. "10级风圈"
    inside_circle: bool
    closest_point: Optional[TyphoonPoint]
    closest_time: Optional[datetime]

    @property
    def key(self) -> str:
        """Dedup key: re-alerts when the storm escalates, not on every check."""
        if self.inside_circle:
            return f"typhoon:{self.storm.id}:{self.wind_label}"
        bucket = int(self.distance_km // 100) * 100
        return f"typhoon:{self.storm.id}:watch{bucket}"


def _circle_hit(point: TyphoonPoint, lon: float, lat: float):
    """Innermost wind circle containing the location, if any."""
    gap = distance_km(point.lon, point.lat, lon, lat)
    heading = bearing_deg(point.lon, point.lat, lon, lat)
    for attribute, wind_label, level in WIND_CIRCLE_TIERS:
        radius = getattr(point, attribute, None)
        if radius is None:
            continue
        reach = radius.for_bearing(heading)
        if reach is not None and gap <= reach:
            return wind_label, level, gap
    return None


def assess_storm(storm: TropicalStorm, lon: float, lat: float) -> Optional[StormThreat]:
    """Assess one storm against a location, or None when it is irrelevant."""
    points: List[TyphoonPoint] = []
    if storm.now is not None:
        points.append(storm.now)
    points.extend(storm.forecast)
    if not points:
        return None

    best_hit = None
    for point in points:
        hit = _circle_hit(point, lon, lat)
        if hit is None:
            continue
        wind_label, level, gap = hit
        rank = [tier[1] for tier in WIND_CIRCLE_TIERS].index(wind_label)
        if best_hit is None or rank < best_hit[0]:
            best_hit = (rank, wind_label, level, gap, point)

    if best_hit is not None:
        _rank, wind_label, level, gap, point = best_hit
        return StormThreat(
            storm=storm,
            distance_km=gap,
            level=level,
            wind_label=f"{wind_label}风圈",
            inside_circle=True,
            closest_point=point,
            closest_time=point.time,
        )

    # No wind circle reaches us (or none reported): fall back to proximity.
    closest = min(points, key=lambda p: distance_km(p.lon, p.lat, lon, lat))
    gap = distance_km(closest.lon, closest.lat, lon, lat)
    if gap > settings.typhoon_watch_distance_km:
        return None
    return StormThreat(
        storm=storm,
        distance_km=gap,
        level="",
        wind_label="",
        inside_circle=False,
        closest_point=closest,
        closest_time=closest.time,
    )


def assess_storms(storms: List[TropicalStorm], lon: float, lat: float) -> List[StormThreat]:
    """Threatening storms, most severe first."""
    threats = []
    for storm in storms:
        try:
            threat = assess_storm(storm, lon, lat)
        except Exception as error:  # noqa: BLE001 - one bad storm must not break the round
            logger.warning(f"Storm assessment failed for {storm.id}: {error}")
            continue
        if threat is not None:
            threats.append(threat)

    severity = {"红色": 0, "橙色": 1, "黄色": 2, "": 3}
    threats.sort(key=lambda t: (severity.get(t.level, 9), t.distance_km))
    return threats


def hours_until(point_time: Optional[datetime]) -> Optional[float]:
    """Hours from now to a forecast point, or None if it has no timestamp."""
    if point_time is None:
        return None
    now = datetime.now(point_time.tzinfo) if point_time.tzinfo else datetime.now()
    delta = (point_time - now).total_seconds() / 3600
    return delta if delta > 0 else None


def format_threat_summary(threat: StormThreat) -> str:
    """One-line human summary used in headings and captions."""
    storm = threat.storm
    label = storm_type_label(storm.now.type if storm.now else "")
    if threat.inside_circle:
        return f"{storm.display_name} · 你在{threat.wind_label}内（{label}）"
    return f"{storm.display_name} · 最近约 {threat.distance_km:.0f}km（{label}）"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
