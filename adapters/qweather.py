import asyncio
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

import httpx
from loguru import logger

from adapters.base import WeatherAdapter
from core.config import settings
from domain.models import (
    AirQuality,
    HistoricalDaySummary,
    DailyForecast,
    HourlyForecast,
    LifeIndex,
    MinutelyPrecipitation,
    TideExtreme,
    TideForecast,
    TideStation,
    TropicalStorm,
    TyphoonPoint,
    TyphoonWindRadius,
    WarningAlert,
    WeatherData,
    normalize_warning_level,
)


WeatherProfile = Literal["full", "hourly", "daily", "rain", "indices"]

# Grid weather (numerical model, 3-5 km) is requested instead of city weather
# when the geocoded city sits this far from the coordinates the user supplied.
# Grid responses reuse the city field names but omit vis, feelsLike and pop —
# those simply stay unset rather than being estimated.
GRID_FALLBACK_HOURS = {"24h": "24h", "72h": "72h", "168h": "72h"}
GRID_FALLBACK_DAYS = {"3d": "3d", "7d": "7d", "10d": "7d", "15d": "7d", "30d": "7d"}


class QWeatherAdapter(WeatherAdapter):
    """Adapter for QWeather using full-path endpoints and header auth."""

    _UNAVAILABLE_MARKER = "__qweather_data_unavailable__"

    def __init__(self):
        from services.qweather_auth import build_signer

        self.api_key = settings.qweather_api_key
        self.base_url = settings.qweather_api_host.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0, http2=True)
        # JWT when credentials are configured, otherwise the long-lived API key.
        self._jwt_signer = build_signer()
        if self._jwt_signer is None:
            logger.info("QWeather auth: API key")

    def _auth_headers(self) -> Dict[str, str]:
        if self._jwt_signer is not None:
            try:
                return {"Authorization": f"Bearer {self._jwt_signer.token()}"}
            except Exception as error:
                # Never lose weather data over a signing hiccup if a key exists.
                logger.error(f"QWeather JWT signing failed: {error}")
                if not self.api_key:
                    raise
        return {"X-QW-Api-Key": self.api_key or ""}

    async def aclose(self):
        await self.client.aclose()

    @classmethod
    def _is_unavailable_marker(cls, value: Any) -> bool:
        return isinstance(value, dict) and value.get(cls._UNAVAILABLE_MARKER) is True

    @staticmethod
    def _is_data_not_available_error(data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        error = data.get("error")
        if not isinstance(error, dict):
            return False
        error_type = str(error.get("type") or "")
        error_title = str(error.get("title") or "")
        return "data-not-available" in error_type or error_title.lower() == "data not available"

    @classmethod
    def _unavailable_marker(cls, endpoint: str) -> Dict[str, Any]:
        return {cls._UNAVAILABLE_MARKER: True, "endpoint": endpoint}

    async def _request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        allow_data_unavailable: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Request QWeather using the new full-path API style."""
        if not endpoint.startswith("/"):
            raise ValueError(f"QWeather endpoint must start with '/': {endpoint}")

        request_params = dict(params or {})
        request_params.setdefault("lang", "zh")
        headers = self._auth_headers()
        url = f"{self.base_url}{endpoint}"

        try:
            response = await self.client.get(url, params=request_params, headers=headers)
            try:
                data = response.json()
            except ValueError:
                data = None

            if not response.is_success:
                if allow_data_unavailable and self._is_data_not_available_error(data):
                    logger.debug(
                        "QWeather optional data unavailable: {} {}",
                        response.status_code,
                        endpoint,
                    )
                    return self._unavailable_marker(endpoint)
                logger.warning(
                    "QWeather API HTTP error: {} {} - {}",
                    response.status_code,
                    endpoint,
                    data if data is not None else response.text[:200],
                )
                return None

            if not isinstance(data, dict):
                logger.warning(
                    "QWeather API returned non-JSON success body: {} - {}",
                    endpoint,
                    response.text[:200],
                )
                return None

            if endpoint.startswith(("/v7/", "/geo/")):
                if data.get("code") == "200":
                    logger.debug(f"QWeather API Success: {endpoint}")
                    return data
                logger.warning(f"QWeather API Error: {endpoint} - {data.get('code')} - {data}")
                return None

            logger.debug(f"QWeather API Success: {endpoint}")
            return data
        except Exception as e:
            logger.error(f"QWeather API Request Failed: {endpoint} - {e}")
            return None

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            match = re.search(r"-?\d+(?:\.\d+)?", str(value))
            return float(match.group(0)) if match else default

    @classmethod
    def _to_int(cls, value: Any, default: int = 0) -> int:
        return int(round(cls._to_float(value, float(default))))

    @classmethod
    def _optional_float(cls, value: Any) -> Optional[float]:
        """Parse an optional numeric field; unparseable values are missing, never 0."""
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            match = re.search(r"-?\d+(?:\.\d+)?", str(value))
            return float(match.group(0)) if match else None

    @classmethod
    def _optional_int(cls, value: Any) -> Optional[int]:
        parsed = cls._optional_float(value)
        return int(round(parsed)) if parsed is not None else None

    @staticmethod
    def _as_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            for key in ("name", "text", "category", "code"):
                if value.get(key):
                    return str(value[key])
            return ""
        return str(value)

    @staticmethod
    def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.debug(f"Failed to parse QWeather datetime: {value}")
            return None

    @classmethod
    def _coord_location(cls, lon: Any, lat: Any) -> str:
        return f"{cls._to_float(lon):.2f},{cls._to_float(lat):.2f}"

    @staticmethod
    def _zone_or_none(tz_name: Optional[str]):
        if not tz_name:
            return None
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(str(tz_name))
        except Exception:
            return None

    @staticmethod
    def _distance_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Great-circle distance, used to detect a far-away geo match."""
        from math import asin, cos, radians, sin, sqrt

        lon1, lat1, lon2, lat2 = map(radians, (lon1, lat1, lon2, lat2))
        h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
        return 2 * 6371.0 * asin(min(1.0, sqrt(h)))

    @classmethod
    def _parse_coords(cls, location: str) -> Optional[tuple]:
        parts = str(location).split(",")
        if len(parts) != 2:
            return None
        try:
            return float(parts[0]), float(parts[1])
        except ValueError:
            return None

    @staticmethod
    def _seconds_until_local_midnight(tz_name: Optional[str]) -> int:
        """Indices are per-location daily data; expire them at the location's midnight."""
        tzinfo = None
        if tz_name:
            try:
                from zoneinfo import ZoneInfo

                tzinfo = ZoneInfo(tz_name)
            except Exception:
                logger.debug(f"Unknown QWeather timezone: {tz_name}")
        now = datetime.now(tzinfo) if tzinfo else datetime.now()
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(60, int((next_midnight - now).total_seconds()))

    _GEO_CACHE_TTL = 30 * 24 * 3600

    @classmethod
    def _geo_cache_key(cls, location: str) -> str:
        """Normalize coordinate-style locations so nearby lookups share one cache entry."""
        normalized = location.strip().lower()
        parts = normalized.split(",")
        if len(parts) == 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
            except ValueError:
                pass
            else:
                return f"geo:{lon:.2f},{lat:.2f}"
        return f"geo:{normalized}"

    async def get_geo_location(self, location: str) -> Optional[Dict[str, Any]]:
        """Resolve location string to Location ID and coordinates."""
        from utils.cache import cache

        cache_key = self._geo_cache_key(location)
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            logger.debug(f"地理位置缓存命中: {location}")
            return cached

        async def resolve_location() -> Optional[Dict[str, Any]]:
            data = await self._request("/geo/v2/city/lookup", {"location": location})
            if data and data.get("location"):
                return data["location"][0]

            if "," in location:
                simple_loc = location.split(",")[0].strip()
                if simple_loc:
                    logger.debug(f"Retrying geo lookup with simplified name: '{simple_loc}'")
                    data = await self._request("/geo/v2/city/lookup", {"location": simple_loc})
                    if data and data.get("location"):
                        return data["location"][0]
            return None

        resolved = await cache.get_or_set(cache_key, resolve_location, ttl=self._GEO_CACHE_TTL)
        return resolved if isinstance(resolved, dict) else None

    async def get_geo_candidates(self, location: str, limit: int = 4) -> list:
        """Return multiple geo matches so callers can disambiguate same-name cities."""
        from utils.cache import cache

        cache_key = f"{self._geo_cache_key(location)}:multi"

        async def resolve_candidates() -> Optional[list]:
            data = await self._request("/geo/v2/city/lookup", {"location": location})
            records = data.get("location") if data else None
            if not isinstance(records, list):
                return None
            return [
                {key: record.get(key) for key in ("id", "name", "adm1", "adm2", "lon", "lat", "tz")}
                for record in records[:10]
                if isinstance(record, dict)
            ]

        candidates = await cache.get_or_set(cache_key, resolve_candidates, ttl=self._GEO_CACHE_TTL)
        if not isinstance(candidates, list):
            return []
        return candidates[:limit]

    async def _cached_request(
        self,
        cache_key: str,
        endpoint: str,
        params: Optional[Dict[str, Any]],
        *,
        ttl: int,
        unavailable_ttl: Optional[int] = None,
        allow_data_unavailable: bool = False,
        force_refresh: bool = False,
    ) -> Optional[Dict[str, Any]]:
        from utils.cache import cache

        def resolve_ttl(value: Dict[str, Any]) -> int:
            if unavailable_ttl is not None and self._is_unavailable_marker(value):
                return unavailable_ttl
            return ttl

        value = await cache.get_or_set(
            cache_key,
            lambda: self._request(
                endpoint,
                params,
                allow_data_unavailable=allow_data_unavailable,
            ),
            ttl=resolve_ttl,
            force_refresh=force_refresh,
        )
        return value if isinstance(value, dict) else None

    def _map_daily(self, daily_data: Optional[Dict[str, Any]]) -> List[DailyForecast]:
        daily_list: List[DailyForecast] = []
        if not daily_data:
            return daily_list

        for d in daily_data.get("daily", []):
            try:
                forecast_date = datetime.strptime(d["fxDate"], "%Y-%m-%d")
                temp_min = self._optional_float(d.get("tempMin"))
                temp_max = self._optional_float(d.get("tempMax"))
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Skipping malformed QWeather daily record: {e}")
                continue
            if temp_min is None or temp_max is None:
                logger.warning(f"Skipping QWeather daily record missing temperature: {d.get('fxDate')}")
                continue
            daily_list.append(
                DailyForecast(
                    date=forecast_date,
                    temp_min=temp_min,
                    temp_max=temp_max,
                    text_day=d.get("textDay", ""),
                    icon_day=d.get("iconDay", ""),
                    text_night=d.get("textNight", ""),
                    icon_night=d.get("iconNight", ""),
                    precip=self._optional_float(d.get("precip")),
                    precip_kind="amount" if d.get("precip") not in (None, "") else None,
                    precip_source="qweather" if d.get("precip") not in (None, "") else None,
                    sunrise=d.get("sunrise"),
                    sunset=d.get("sunset"),
                    moon_phase=d.get("moonPhase"),
                    moon_rise=d.get("moonrise"),
                    moon_set=d.get("moonset"),
                    humidity=self._optional_int(d.get("humidity")),
                    vis=self._optional_float(d.get("vis")),
                    uv_index=d.get("uvIndex"),
                    pressure=self._optional_float(d.get("pressure")),
                    cloud=self._optional_int(d.get("cloud")),
                    wind_dir_day=d.get("windDirDay"),
                    wind_scale_day=d.get("windScaleDay"),
                    wind_speed_day=self._optional_float(d.get("windSpeedDay")),
                    wind_dir_night=d.get("windDirNight"),
                    wind_scale_night=d.get("windScaleNight"),
                    wind_speed_night=self._optional_float(d.get("windSpeedNight")),
                    field_sources={
                        "temperature": "qweather",
                        "weather": "qweather",
                    },
                )
            )
        return daily_list

    def _map_hourly(
        self,
        hourly_data: Optional[Dict[str, Any]],
        now_data: Dict[str, Any],
    ) -> List[HourlyForecast]:
        hourly_list: List[HourlyForecast] = []
        if hourly_data:
            for h in hourly_data.get("hourly", []):
                fx_time = self._parse_datetime(h.get("fxTime"))
                temp = self._optional_float(h.get("temp"))
                if fx_time is None or temp is None:
                    logger.warning(
                        f"Skipping QWeather hourly record with missing time/temp: {h.get('fxTime')}"
                    )
                    continue
                humidity = self._optional_int(h.get("humidity"))
                wind_speed = self._optional_float(h.get("windSpeed"))
                dew = self._optional_float(h.get("dew"))
                precip = self._optional_float(h.get("precip"))
                hourly_list.append(
                    HourlyForecast(
                        time=fx_time,
                        temp=temp,
                        text=h.get("text", ""),
                        icon=h.get("icon", ""),
                        pop=self._optional_float(h.get("pop")),
                        precip=precip,
                        precip_kind="amount" if precip is not None else None,
                        precip_source="qweather" if precip is not None else None,
                        wind_dir=h.get("windDir", ""),
                        wind_scale=h.get("windScale", ""),
                        wind_speed=wind_speed,
                        humidity=humidity,
                        pressure=self._optional_float(h.get("pressure")),
                        cloud=self._optional_int(h.get("cloud")),
                        dew=dew,
                        uv_index=self._optional_float(h.get("uvIndex")),
                        visibility=self._optional_float(h.get("vis")),
                        field_sources={
                            "temperature": "qweather",
                            "weather": "qweather",
                        },
                    )
                )

        if hourly_list and now_data:
            try:
                now = now_data["now"]
                current_obs_time = self._parse_datetime(now.get("obsTime"))
                current_temp = self._optional_float(now.get("temp"))
                if current_obs_time and current_temp is not None:
                    current_hour_time = current_obs_time.replace(minute=0, second=0, microsecond=0)
                    if hourly_list[0].time > current_hour_time:
                        current_precip = self._optional_float(now.get("precip"))
                        hourly_list.insert(
                            0,
                            HourlyForecast(
                                time=current_hour_time,
                                temp=current_temp,
                                feels_like=self._optional_float(now.get("feelsLike")),
                                feels_like_estimated=False,
                                feels_like_source="qweather",
                                text=now.get("text", ""),
                                icon=now.get("icon", ""),
                                pop=None,
                                precip=current_precip,
                                precip_kind="amount" if current_precip is not None else None,
                                precip_source="qweather" if current_precip is not None else None,
                                wind_dir=now.get("windDir", ""),
                                wind_scale=now.get("windScale", ""),
                                wind_speed=(
                                    self._optional_float(now.get("windSpeed"))
                                ),
                                humidity=self._optional_int(now.get("humidity")),
                                pressure=self._optional_float(now.get("pressure")),
                                cloud=self._optional_int(now.get("cloud")),
                                dew=self._optional_float(now.get("dew")),
                                visibility=self._optional_float(now.get("vis")),
                                field_sources={
                                    "temperature": "qweather",
                                    "weather": "qweather",
                                },
                            ),
                        )
            except Exception as e:
                logger.warning(f"Failed to prepend current hour data: {e}")

        return hourly_list

    def _map_minutely(self, minutely_data: Optional[Dict[str, Any]]) -> List[MinutelyPrecipitation]:
        minutely_list: List[MinutelyPrecipitation] = []
        if not minutely_data:
            return minutely_list

        for item in minutely_data.get("minutely", []):
            fx_time = self._parse_datetime(item.get("fxTime"))
            if fx_time is None:
                logger.warning("Skipping QWeather minutely record with unparseable fxTime")
                continue
            minutely_list.append(
                MinutelyPrecipitation(
                    time=fx_time,
                    precip=self._to_float(item.get("precip")),
                    probability=None,
                    precip_type=item.get("type"),
                    precip_kind="amount",
                    interval_minutes=5,
                    source="qweather",
                )
            )
        return minutely_list

    def _map_alerts(self, warning_data: Optional[Dict[str, Any]]) -> List[WarningAlert]:
        alerts: List[WarningAlert] = []
        if not warning_data:
            return alerts

        for item in warning_data.get("alerts", []):
            event_type = item.get("eventType") or {}
            color = self._as_text(item.get("color"))
            severity = self._as_text(item.get("severity"))
            description = item.get("description") or ""
            instruction = item.get("instruction") or ""
            text = "\n".join(part for part in (description, instruction) if part)
            alerts.append(
                WarningAlert(
                    title=item.get("headline") or item.get("title") or self._as_text(event_type) or "天气预警",
                    type=self._as_text(event_type),
                    level=normalize_warning_level(color if color else severity),
                    text=text,
                    pub_time=self._parse_datetime(item.get("issuedTime")) or datetime.now(),
                    source="QWeather",
                    status=self._as_text(item.get("messageType")),
                    alert_id=item.get("id"),
                    expire_time=self._parse_datetime(item.get("expireTime")),
                )
            )
        return alerts

    def _map_air_quality(self, air_data: Optional[Dict[str, Any]]) -> Optional[AirQuality]:
        if not air_data:
            return None

        indexes = air_data.get("indexes") or []
        if not indexes:
            return None

        preferred_codes = {"cn-mee", "cn_mep", "chn", "china", "cn"}
        selected = next((i for i in indexes if str(i.get("code", "")).lower() in preferred_codes), None)
        selected = selected or next((i for i in indexes if str(i.get("code", "")).lower() != "qaqi"), None)
        selected = selected or indexes[0]

        primary = selected.get("primaryPollutant") or {}
        health = selected.get("health") or {}
        advice = health.get("advice") or {}

        pollutants: dict[str, Optional[float]] = {
            "pm2p5": None,
            "pm10": None,
            "o3": None,
            "so2": None,
            "no2": None,
            "co": None,
        }
        for pollutant in air_data.get("pollutants", []):
            code = re.sub(
                r"[^a-z0-9]",
                "",
                str(pollutant.get("code") or pollutant.get("name") or "").lower(),
            )
            if code in {"pm25", "pm2p5"}:
                target = "pm2p5"
            elif code in pollutants:
                target = code
            else:
                continue
            concentration = pollutant.get("concentration") or {}
            pollutants[target] = self._optional_float(concentration.get("value"))

        raw_aqi = selected.get("aqi")
        if raw_aqi in (None, ""):
            raw_aqi = selected.get("aqiDisplay")

        return AirQuality(
            aqi=self._optional_int(raw_aqi),
            category=self._as_text(selected.get("category") or selected.get("level")),
            primary=self._as_text(primary),
            **pollutants,
            description=self._as_text(advice.get("generalPopulation") or health.get("effect")),
            source="qweather",
            field_sources={
                "aqi": "qweather",
                **({key: "qweather" for key, value in pollutants.items() if value is not None}),
            },
        )

    def _map_hourly_air_quality(
        self,
        air_data: Optional[Dict[str, Any]],
    ) -> Dict[tuple, AirQuality]:
        if not air_data:
            return {}
        records = air_data.get("hours") or air_data.get("hourly") or []
        mapped: Dict[tuple, AirQuality] = {}
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            forecast_time = self._parse_datetime(
                record.get("forecastTime") or record.get("fxTime") or record.get("time")
            )
            air_quality = self._map_air_quality(record)
            if forecast_time is None or air_quality is None:
                continue
            if forecast_time.tzinfo is not None and forecast_time.utcoffset() is not None:
                key = ("utc", int(forecast_time.timestamp() // 3600))
            else:
                key = (
                    "local",
                    forecast_time.year,
                    forecast_time.month,
                    forecast_time.day,
                    forecast_time.hour,
                )
            mapped[key] = air_quality
        return mapped

    def _map_daily_air_quality(
        self,
        air_data: Optional[Dict[str, Any]],
    ) -> Dict[date, AirQuality]:
        if not air_data:
            return {}
        records = air_data.get("days") or air_data.get("daily") or []
        mapped = {}
        for record in records if isinstance(records, list) else []:
            if not isinstance(record, dict):
                continue
            forecast_time = self._parse_datetime(
                record.get("forecastStartTime")
                or record.get("forecastDate")
                or record.get("fxDate")
            )
            air_quality = self._map_air_quality(record)
            if forecast_time is not None and air_quality is not None:
                mapped[forecast_time.date()] = air_quality
        return mapped

    @classmethod
    def _map_solar_radiation(cls, solar_data: Optional[Dict[str, Any]]) -> Dict[tuple, float]:
        """GHI per hour, keyed like the hourly forecast for fill-only merging.

        Response shape (verified against the live API): forecasts[] with
        forecastTime, ghi, dhi, dni and solarAngle, all W/m². The published
        docs call the direct component "ni"; the API actually returns "dni".
        Only ghi is used here.
        """
        if not isinstance(solar_data, dict) or cls._is_unavailable_marker(solar_data):
            return {}
        mapped: Dict[tuple, float] = {}
        for raw in solar_data.get("forecasts") or []:
            if not isinstance(raw, dict):
                continue
            moment = cls._parse_datetime(raw.get("forecastTime"))
            ghi = cls._optional_float(raw.get("ghi"))
            if moment is None or ghi is None:
                continue
            if moment.tzinfo is not None and moment.utcoffset() is not None:
                key = ("utc", int(moment.timestamp() // 3600))
            else:
                key = ("local", moment.year, moment.month, moment.day, moment.hour)
            # Several sub-hourly samples can share an hour; keep the strongest.
            mapped[key] = max(ghi, mapped.get(key, ghi))
        return mapped

    def _map_history(self, history_data: Optional[Dict[str, Any]]) -> Optional[HistoricalDaySummary]:
        """Map Time Machine's weatherDaily block (field names differ from /v7/weather)."""
        if not history_data or self._is_unavailable_marker(history_data):
            return None
        daily = history_data.get("weatherDaily")
        if not isinstance(daily, dict):
            return None
        raw_date = str(daily.get("date") or "")
        try:
            parsed_date = datetime.strptime(raw_date[:10], "%Y-%m-%d")
        except ValueError:
            parsed_date = self._parse_datetime(raw_date)
            if parsed_date is None:
                logger.debug(f"Unparseable historical date: {raw_date}")
                return None
        return HistoricalDaySummary(
            date=parsed_date,
            temp_max=self._optional_float(daily.get("tempMax")),
            temp_min=self._optional_float(daily.get("tempMin")),
            humidity=self._optional_int(daily.get("humidity")),
            precip=self._optional_float(daily.get("precip")),
            pressure=self._optional_float(daily.get("pressure")),
        )

    @staticmethod
    def _map_air_stations(air_data: Optional[Dict[str, Any]]) -> List[str]:
        """Nearby station names, already present in the current AQI response."""
        if not isinstance(air_data, dict):
            return []
        names = []
        for station in air_data.get("stations") or []:
            if isinstance(station, dict):
                name = str(station.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
        return names[:5]

    def _map_indices(self, indices_data: Optional[Dict[str, Any]]) -> List[LifeIndex]:
        if not indices_data:
            return []
        indices: List[LifeIndex] = []
        for i in indices_data.get("daily", []):
            try:
                indices.append(
                    LifeIndex(
                        type=i["type"],
                        name=i["name"],
                        category=i["category"],
                        text=i.get("text", ""),
                        date=self._parse_datetime(i.get("date")),
                        value=str(i.get("level")) if i.get("level") not in (None, "") else None,
                        source="qweather",
                    )
                )
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(f"Skipping malformed QWeather index record: {e}")
        return indices

    # ------------------------------------------------------------------ #
    # Tides (GeoAPI POI type=TSTA, then /v7/ocean/tide)
    # ------------------------------------------------------------------ #

    async def get_tide_stations(self, lon: float, lat: float, limit: int = 5) -> List[TideStation]:
        """Nearest tide stations. /v7/ocean/tide needs a station id, not a city."""
        coord = self._coord_location(lon, lat)
        data = await self._cached_request(
            f"qw:poi:tsta:{coord}",
            "/geo/v2/poi/lookup",
            {"location": coord, "type": "TSTA", "number": 10},
            ttl=self._GEO_CACHE_TTL,
            unavailable_ttl=86400,
            allow_data_unavailable=True,
        )
        if not data or self._is_unavailable_marker(data):
            return []

        # GeoAPI returns city matches under "location"; POI lookup is documented
        # as "poi". Accept either so a naming difference cannot silently break.
        records = data.get("poi") or data.get("location") or []
        stations = []
        for raw in records if isinstance(records, list) else []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            station_lon = self._optional_float(raw.get("lon"))
            station_lat = self._optional_float(raw.get("lat"))
            distance = (
                self._distance_km(lon, lat, station_lon, station_lat)
                if station_lon is not None and station_lat is not None
                else None
            )
            stations.append(
                TideStation(
                    id=str(raw["id"]),
                    name=str(raw.get("name") or raw["id"]),
                    lon=station_lon,
                    lat=station_lat,
                    distance_km=distance,
                )
            )
        stations.sort(key=lambda s: s.distance_km if s.distance_km is not None else 1e9)
        return stations[:limit]

    async def get_tide(self, station: TideStation, target_date=None) -> Optional[TideForecast]:
        """Tide table for one station and date (up to 10 days ahead)."""
        target_date = target_date or datetime.now().date()
        stamp = target_date.strftime("%Y%m%d")
        data = await self._cached_request(
            f"qw:tide:{station.id}:{stamp}",
            "/v7/ocean/tide",
            {"location": station.id, "date": stamp},
            ttl=43200,
            unavailable_ttl=21600,
            allow_data_unavailable=True,
        )
        if not data or self._is_unavailable_marker(data):
            return None

        extremes = []
        for raw in data.get("tideTable") or []:
            if not isinstance(raw, dict):
                continue
            moment = self._parse_datetime(raw.get("fxTime"))
            height = self._optional_float(raw.get("height"))
            if moment is None or height is None:
                continue
            extremes.append(
                TideExtreme(
                    time=moment,
                    height=height,
                    is_high=str(raw.get("type") or "").upper().startswith("H"),
                )
            )

        hourly = []
        for raw in data.get("tideHourly") or []:
            if not isinstance(raw, dict):
                continue
            moment = self._parse_datetime(raw.get("fxTime"))
            height = self._optional_float(raw.get("height"))
            if moment is not None and height is not None:
                hourly.append((moment, height))

        if not extremes and not hourly:
            return None
        return TideForecast(
            station=station,
            date=datetime.combine(target_date, datetime.min.time()),
            extremes=extremes,
            hourly=hourly,
        )

    # ------------------------------------------------------------------ #
    # Tropical cyclones (basin NP only, per QWeather)
    # ------------------------------------------------------------------ #

    @classmethod
    def _map_wind_radius(cls, raw: Any) -> Optional[TyphoonWindRadius]:
        if not isinstance(raw, dict):
            return None
        radius = TyphoonWindRadius(
            ne=cls._optional_float(raw.get("neRadius")),
            se=cls._optional_float(raw.get("seRadius")),
            sw=cls._optional_float(raw.get("swRadius")),
            nw=cls._optional_float(raw.get("nwRadius")),
        )
        if all(value is None for value in (radius.ne, radius.se, radius.sw, radius.nw)):
            return None
        return radius

    @classmethod
    def _map_storm_point(cls, raw: Any) -> Optional[TyphoonPoint]:
        if not isinstance(raw, dict):
            return None
        lat = cls._optional_float(raw.get("lat"))
        lon = cls._optional_float(raw.get("lon"))
        if lat is None or lon is None:
            return None
        # Observed points use "time"/"pubTime"; forecast points use "fxTime".
        stamp = raw.get("fxTime") or raw.get("time") or raw.get("pubTime")
        return TyphoonPoint(
            time=cls._parse_datetime(stamp),
            lat=lat,
            lon=lon,
            type=cls._as_text(raw.get("type")),
            pressure=cls._optional_float(raw.get("pressure")),
            wind_speed=cls._optional_float(raw.get("windSpeed")),
            move_dir=raw.get("moveDir") or None,
            move_speed=cls._optional_float(raw.get("moveSpeed")),
            radius30=cls._map_wind_radius(raw.get("windRadius30")),
            radius50=cls._map_wind_radius(raw.get("windRadius50")),
            radius64=cls._map_wind_radius(raw.get("windRadius64")),
        )

    async def get_storm_list(self, basin: str = "NP", year: Optional[str] = None) -> List[dict]:
        """Storms for a basin. QWeather currently only supports NP (西北太平洋)."""
        year = year or str(datetime.now().year)
        data = await self._cached_request(
            f"qw:storm-list:{basin}:{year}",
            "/v7/tropical/storm-list",
            {"basin": basin, "year": year},
            ttl=3600,
            unavailable_ttl=3600,
            allow_data_unavailable=True,
        )
        if not data or self._is_unavailable_marker(data):
            return []
        storms = []
        for raw in data.get("storm") or []:
            if not isinstance(raw, dict) or not raw.get("id"):
                continue
            storms.append({
                "id": str(raw["id"]),
                "name": str(raw.get("name") or ""),
                "basin": str(raw.get("basin") or basin),
                "year": str(raw.get("year") or year),
                "is_active": str(raw.get("isActive") or "0") == "1",
            })
        return storms

    async def get_storm_detail(self, storm_meta: dict) -> Optional[TropicalStorm]:
        """Current position + history + forecast track for one storm."""
        storm_id = storm_meta["id"]
        track_data, forecast_data = await asyncio.gather(
            self._cached_request(
                f"qw:storm-track:{storm_id}",
                "/v7/tropical/storm-track",
                {"stormid": storm_id},
                ttl=1800,
                unavailable_ttl=3600,
                allow_data_unavailable=True,
            ),
            self._cached_request(
                f"qw:storm-forecast:{storm_id}",
                "/v7/tropical/storm-forecast",
                {"stormid": storm_id},
                ttl=1800,
                unavailable_ttl=3600,
                allow_data_unavailable=True,
            ),
            return_exceptions=True,
        )

        def usable(value):
            return value if isinstance(value, dict) and not self._is_unavailable_marker(value) else None

        track_data = usable(track_data)
        forecast_data = usable(forecast_data)
        if track_data is None and forecast_data is None:
            return None

        now_point = self._map_storm_point((track_data or {}).get("now"))
        track = [
            point
            for point in (self._map_storm_point(raw) for raw in (track_data or {}).get("track") or [])
            if point is not None
        ]
        forecast = [
            point
            for point in (
                self._map_storm_point(raw) for raw in (forecast_data or {}).get("forecast") or []
            )
            if point is not None
        ]
        if now_point is None and track:
            now_point = track[-1]
        if now_point is None and forecast:
            now_point = forecast[0]
        if now_point is None:
            return None

        # storm-track carries its own isActive; trust it over the list snapshot.
        is_active = storm_meta.get("is_active", True)
        if track_data and track_data.get("isActive") is not None:
            is_active = str(track_data.get("isActive")) == "1"

        return TropicalStorm(
            id=storm_id,
            # The forecast endpoint does not return a name, so it comes from the list.
            name=storm_meta.get("name", ""),
            basin=storm_meta.get("basin", ""),
            year=storm_meta.get("year", ""),
            is_active=is_active,
            now=now_point,
            track=track,
            forecast=forecast,
        )

    async def get_active_storms(self, basin: str = "NP") -> List[TropicalStorm]:
        """Every currently active storm in the basin, with tracks resolved."""
        metas = [meta for meta in await self.get_storm_list(basin) if meta["is_active"]]
        if not metas:
            return []
        details = await asyncio.gather(
            *(self.get_storm_detail(meta) for meta in metas), return_exceptions=True
        )
        storms = []
        for meta, detail in zip(metas, details):
            if isinstance(detail, Exception):
                logger.error(f"Storm detail failed for {meta['id']}: {type(detail).__name__}")
                continue
            if detail is not None and detail.is_active:
                storms.append(detail)
        return storms

    async def get_weather(
        self,
        location: str,
        *,
        profile: WeatherProfile = "full",
        refresh_qweather: bool = False,
        loc_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[WeatherData]:
        profile_components = {
            "full": {
                "minutely", "air", "air_hourly", "air_daily", "warning",
                "daily", "hourly", "indices", "history", "solar",
            },
            "hourly": {"air", "air_hourly", "warning", "hourly"},
            "daily": {"air_daily", "warning", "daily"},
            "rain": {"minutely", "air", "warning", "hourly"},
            "indices": {"air", "warning", "indices"},
        }
        if profile not in profile_components:
            raise ValueError(f"Unsupported QWeather profile: {profile}")

        resolved_location = loc_info or await self.get_geo_location(location)
        if not resolved_location:
            logger.warning(f"Could not resolve location: {location}")
            return None

        loc_id = resolved_location["id"]
        lon = resolved_location["lon"]
        lat = resolved_location["lat"]
        loc_name = resolved_location.get("name", location)
        adm1 = resolved_location.get("adm1")
        if adm1:
            loc_name = f"{loc_name}, {adm1}"

        # City lookup snaps coordinates to the nearest supported city. When that
        # city is far away, reporting its weather under its name would be wrong,
        # so switch the weather components to the grid (numerical model) API and
        # keep the user's own coordinates.
        requested_coords = self._parse_coords(location)
        grid_location = None
        if settings.enable_grid_weather and requested_coords is not None:
            try:
                offset_km = self._distance_km(
                    requested_coords[0], requested_coords[1], float(lon), float(lat)
                )
            except (TypeError, ValueError):
                offset_km = 0.0
            if offset_km > settings.grid_weather_distance_km:
                grid_location = self._coord_location(*requested_coords)
                lon, lat = requested_coords
                loc_name = f"{resolved_location.get('name', location)} 附近"
                logger.info(
                    "Using grid weather: nearest city {} is {:.0f}km away",
                    resolved_location.get("name"),
                    offset_km,
                )

        coords = f"{lon},{lat}"
        coord_location = grid_location or self._coord_location(lon, lat)

        seconds_until_midnight = self._seconds_until_local_midnight(resolved_location.get("tz"))
        components = profile_components[profile]

        requests: dict[str, Any] = {}
        if grid_location:
            requests["now"] = self._cached_request(
                f"qw:grid-now:{grid_location}",
                "/v7/grid-weather/now",
                {"location": grid_location},
                ttl=600,
                force_refresh=refresh_qweather,
            )
        else:
            requests["now"] = self._cached_request(
                f"qw:now:{loc_id}",
                "/v7/weather/now",
                {"location": loc_id},
                ttl=600,
                force_refresh=refresh_qweather,
            )
        if "minutely" in components and settings.qweather_enable_minutely:
            requests["minutely"] = self._cached_request(
                f"qw:minutely:{coord_location}",
                "/v7/minutely/5m",
                {"location": coord_location},
                ttl=300,
                unavailable_ttl=3600,
                allow_data_unavailable=True,
                force_refresh=refresh_qweather,
            )
        if "air" in components:
            requests["air"] = self._cached_request(
                f"qw:air:v1:{coord_location}",
                f"/airquality/v1/current/{lat}/{lon}",
                None,
                ttl=3600,
                unavailable_ttl=21600,
                allow_data_unavailable=True,
                force_refresh=refresh_qweather,
            )
        if "air_hourly" in components:
            requests["air_hourly"] = self._cached_request(
                f"qw:air-hourly:v1:{coord_location}",
                f"/airquality/v1/hourly/{lat}/{lon}",
                None,
                ttl=3600,
                unavailable_ttl=21600,
                allow_data_unavailable=True,
                force_refresh=refresh_qweather,
            )
        if "air_daily" in components:
            requests["air_daily"] = self._cached_request(
                f"qw:air-daily:v1:{coord_location}",
                f"/airquality/v1/daily/{lat}/{lon}",
                None,
                ttl=43200,
                unavailable_ttl=21600,
                allow_data_unavailable=True,
                force_refresh=refresh_qweather,
            )
        if "warning" in components:
            requests["warning"] = self._cached_request(
                f"qw:warning:v1:{coord_location}",
                f"/weatheralert/v1/current/{lat}/{lon}",
                {"localTime": "true"},
                # Warnings are the only life-safety data here; keep them fresh
                # enough that a red alert is not delayed by half an hour.
                ttl=300,
                unavailable_ttl=300,
                allow_data_unavailable=True,
                force_refresh=refresh_qweather,
            )
        if "daily" in components:
            if grid_location:
                grid_days = GRID_FALLBACK_DAYS.get(settings.qweather_daily_days, "7d")
                requests["daily"] = self._cached_request(
                    f"qw:grid-daily:{grid_days}:{grid_location}",
                    f"/v7/grid-weather/{grid_days}",
                    {"location": grid_location},
                    ttl=43200,
                    force_refresh=refresh_qweather,
                )
            else:
                requests["daily"] = self._cached_request(
                    f"qw:daily:{settings.qweather_daily_days}:{loc_id}",
                    f"/v7/weather/{settings.qweather_daily_days}",
                    {"location": loc_id},
                    ttl=43200,
                    force_refresh=refresh_qweather,
                )
        if "hourly" in components:
            if grid_location:
                grid_hours = GRID_FALLBACK_HOURS.get(settings.qweather_hourly_hours, "72h")
                requests["hourly"] = self._cached_request(
                    f"qw:grid-hourly:{grid_hours}:{grid_location}",
                    f"/v7/grid-weather/{grid_hours}",
                    {"location": grid_location},
                    ttl=21600,
                    force_refresh=refresh_qweather,
                )
            else:
                requests["hourly"] = self._cached_request(
                    f"qw:hourly:{settings.qweather_hourly_hours}:{loc_id}",
                    f"/v7/weather/{settings.qweather_hourly_hours}",
                    {"location": loc_id},
                    ttl=21600,
                    force_refresh=refresh_qweather,
                )
        if "indices" in components:
            # Indices are city-scoped only, so they always use the resolved city.
            requests["indices"] = self._cached_request(
                f"qw:indices:{settings.qweather_indices_days}:{settings.qweather_indices_types}:{loc_id}",
                f"/v7/indices/{settings.qweather_indices_days}",
                {"location": loc_id, "type": settings.qweather_indices_types},
                ttl=seconds_until_midnight,
                force_refresh=refresh_qweather,
            )
        if "solar" in components and settings.enable_solar_radiation:
            requests["solar"] = self._cached_request(
                f"qw:solar:{coord_location}",
                f"/solarradiation/v1/forecast/{lat}/{lon}",
                {"hours": 24, "interval": 60, "localTime": "true"},
                ttl=3600,
                unavailable_ttl=21600,
                allow_data_unavailable=True,
                force_refresh=refresh_qweather,
            )
        if "history" in components and settings.enable_history_comparison:
            # Yesterday's summary powers "warmer/cooler than yesterday" in the
            # AI report. LocationID only, and today is not available.
            history_date = (
                datetime.now(self._zone_or_none(resolved_location.get("tz"))) - timedelta(days=1)
            ).strftime("%Y%m%d")
            requests["history"] = self._cached_request(
                f"qw:history:{loc_id}:{history_date}",
                "/v7/historical/weather",
                {"location": loc_id, "date": history_date},
                ttl=86400,
                unavailable_ttl=21600,
                allow_data_unavailable=True,
                force_refresh=False,
            )

        keys = list(requests)
        values = await asyncio.gather(*requests.values(), return_exceptions=True)
        payloads: dict[str, Optional[Dict[str, Any]]] = {}
        for key, value in zip(keys, values):
            if isinstance(value, Exception):
                logger.error(f"QWeather component failed: component={key} type={type(value).__name__}")
                payloads[key] = None
            else:
                payloads[key] = value

        now_data = payloads.get("now")
        if not now_data or not isinstance(now_data.get("now"), dict):
            return None

        minutely_data = payloads.get("minutely")
        air_data = payloads.get("air")
        hourly_air_data = payloads.get("air_hourly")
        daily_air_data = payloads.get("air_daily")
        warning_data = payloads.get("warning")
        daily_data = payloads.get("daily")
        hourly_data = payloads.get("hourly")
        indices_data = payloads.get("indices")
        history_data = payloads.get("history")
        solar_data = payloads.get("solar")

        now_weather = now_data["now"]
        daily_list = self._map_daily(daily_data)
        hourly_list = self._map_hourly(hourly_data, now_data)
        minutely_list = self._map_minutely(minutely_data)
        alerts_list = self._map_alerts(warning_data)
        aqi_obj = self._map_air_quality(air_data)
        indices_list = self._map_indices(indices_data)
        yesterday = self._map_history(history_data)
        air_stations = self._map_air_stations(air_data)

        hourly_air = self._map_hourly_air_quality(hourly_air_data)
        for hour in hourly_list:
            if hour.time.tzinfo is not None and hour.time.utcoffset() is not None:
                air_key = ("utc", int(hour.time.timestamp() // 3600))
            else:
                air_key = ("local", hour.time.year, hour.time.month, hour.time.day, hour.time.hour)
            forecast_air = hourly_air.get(air_key)
            if forecast_air is not None:
                hour.aqi = forecast_air.aqi
                hour.pm2p5 = forecast_air.pm2p5
                if forecast_air.aqi is not None:
                    hour.field_sources["aqi"] = "qweather"
                if forecast_air.pm2p5 is not None:
                    hour.field_sources["pm2p5"] = "qweather"

        # Solar radiation fills radiation only where nothing provided it, in
        # line with the fill-only fusion rule (Caiyun may already have set it).
        solar_by_hour = self._map_solar_radiation(solar_data)
        if solar_by_hour:
            filled = 0
            for hour in hourly_list:
                if hour.radiation is not None:
                    continue
                if hour.time.tzinfo is not None and hour.time.utcoffset() is not None:
                    solar_key = ("utc", int(hour.time.timestamp() // 3600))
                else:
                    solar_key = ("local", hour.time.year, hour.time.month, hour.time.day, hour.time.hour)
                value = solar_by_hour.get(solar_key)
                if value is not None:
                    hour.radiation = value
                    hour.field_sources["radiation"] = "qweather"
                    filled += 1
            if filled:
                logger.debug(f"Solar radiation filled {filled} hourly slots")

        daily_air = self._map_daily_air_quality(daily_air_data)
        for day in daily_list:
            forecast_air = daily_air.get(day.date.date())
            if forecast_air is not None:
                day.aqi = forecast_air.aqi
                day.pm2p5 = forecast_air.pm2p5
                if forecast_air.aqi is not None:
                    day.field_sources["aqi"] = "qweather"
                if forecast_air.pm2p5 is not None:
                    day.field_sources["pm2p5"] = "qweather"

        now_temp = self._optional_float(now_weather.get("temp"))
        if now_temp is None:
            logger.warning(f"QWeather current response missing temperature for {loc_id}")
            return None

        now_precip = self._optional_float(now_weather.get("precip"))
        minutely_summary = minutely_data.get("summary") if minutely_data else ""
        summary = f"当前 {now_weather.get('text', '')}，温度 {now_temp}°C。"
        if minutely_summary:
            summary = f"{summary}\n{minutely_summary}"

        is_raining = (now_precip or 0) > 0 or any(item.precip > 0 for item in minutely_list[:6])
        update_time = (
            self._parse_datetime(now_data.get("updateTime"))
            or self._parse_datetime(now_weather.get("obsTime"))
            or datetime.now()
        )

        attributions: list[str] = []
        for payload in payloads.values():
            if not isinstance(payload, dict):
                continue
            for attribution in (payload.get("metadata") or {}).get("attributions", []):
                if isinstance(attribution, dict):
                    name = str(attribution.get("name") or "").strip()
                    url = str(attribution.get("url") or "").strip()
                    text = " ".join(part for part in (name, url) if part)
                else:
                    text = str(attribution).strip()
                if text and text not in attributions:
                    attributions.append(text)

        return WeatherData(
            source="qweather",
            update_time=update_time,
            location_name=loc_name,
            coords=coords,
            now_temp=now_temp,
            now_feels_like=self._optional_float(now_weather.get("feelsLike")),
            now_text=now_weather.get("text", ""),
            now_icon=now_weather.get("icon", ""),
            now_wind_dir=now_weather.get("windDir") or "",
            now_wind_scale=now_weather.get("windScale") or "",
            now_wind_speed=self._optional_float(now_weather.get("windSpeed")),
            now_humidity=self._optional_int(now_weather.get("humidity")),
            now_precip=now_precip,
            now_precip_kind="amount" if now_precip is not None else None,
            now_precip_source="qweather" if now_precip is not None else None,
            now_pressure=self._optional_float(now_weather.get("pressure")),
            now_vis=self._optional_float(now_weather.get("vis")),
            now_cloud=self._optional_int(now_weather.get("cloud")),
            summary=summary,
            provider_summaries={"qweather": summary},
            field_sources={
                "now_temp": "qweather",
                "now_text": "qweather",
            },
            daily=daily_list,
            hourly=hourly_list,
            minutely=minutely_list,
            air_quality=aqi_obj,
            alerts=alerts_list,
            indices=indices_list,
            is_raining=is_raining,
            attributions=attributions,
            yesterday=yesterday,
            air_stations=air_stations,
        )
