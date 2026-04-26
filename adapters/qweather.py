import re
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from adapters.base import WeatherAdapter
from core.config import settings
from domain.models import (
    AirQuality,
    DailyForecast,
    HourlyForecast,
    LifeIndex,
    MinutelyPrecipitation,
    WarningAlert,
    WeatherData,
)


class QWeatherAdapter(WeatherAdapter):
    """Adapter for QWeather using full-path endpoints and header auth."""

    def __init__(self):
        self.api_key = settings.qweather_api_key
        self.base_url = settings.qweather_api_host.rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0, http2=True)

    async def _request(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Request QWeather using the new full-path API style."""
        if not endpoint.startswith("/"):
            raise ValueError(f"QWeather endpoint must start with '/': {endpoint}")

        request_params = dict(params or {})
        request_params.setdefault("lang", "zh")
        headers = {"X-QW-Api-Key": self.api_key}
        url = f"{self.base_url}{endpoint}"

        try:
            response = await self.client.get(url, params=request_params, headers=headers)
            try:
                data = response.json()
            except ValueError:
                data = {"raw": response.text}

            if not response.is_success:
                logger.warning(
                    "QWeather API HTTP error: {} {} - {}",
                    response.status_code,
                    endpoint,
                    data,
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

    async def get_geo_location(self, location: str) -> Optional[Dict[str, Any]]:
        """Resolve location string to Location ID and coordinates."""
        from utils.cache import cache

        cache_key = f"geo:{location.lower()}"
        cached = await cache.get(cache_key)
        if cached:
            logger.debug(f"地理位置缓存命中: {location}")
            return cached

        data = await self._request("/geo/v2/city/lookup", {"location": location})
        if data and data.get("location"):
            loc_data = data["location"][0]
            await cache.set(cache_key, loc_data, ttl=None)
            return loc_data

        if "," in location:
            simple_loc = location.split(",")[0].strip()
            if simple_loc:
                logger.debug(f"Retrying geo lookup with simplified name: '{simple_loc}'")
                data = await self._request("/geo/v2/city/lookup", {"location": simple_loc})
                if data and data.get("location"):
                    loc_data = data["location"][0]
                    await cache.set(cache_key, loc_data, ttl=None)
                    return loc_data

        return None

    def _map_daily(self, daily_data: Optional[Dict[str, Any]]) -> List[DailyForecast]:
        daily_list: List[DailyForecast] = []
        if not daily_data:
            return daily_list

        for d in daily_data.get("daily", []):
            daily_list.append(
                DailyForecast(
                    date=datetime.strptime(d["fxDate"], "%Y-%m-%d"),
                    temp_min=self._to_float(d.get("tempMin")),
                    temp_max=self._to_float(d.get("tempMax")),
                    text_day=d.get("textDay", ""),
                    icon_day=d.get("iconDay", ""),
                    text_night=d.get("textNight", ""),
                    icon_night=d.get("iconNight", ""),
                    precip=self._to_float(d.get("precip")),
                    sunrise=d.get("sunrise"),
                    sunset=d.get("sunset"),
                    moon_phase=d.get("moonPhase"),
                    moon_rise=d.get("moonrise"),
                    moon_set=d.get("moonset"),
                    humidity=self._to_int(d.get("humidity")),
                    vis=self._to_float(d.get("vis")),
                    uv_index=d.get("uvIndex", "N/A"),
                    wind_dir_day=d.get("windDirDay"),
                    wind_scale_day=d.get("windScaleDay"),
                    wind_dir_night=d.get("windDirNight"),
                    wind_scale_night=d.get("windScaleNight"),
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
                hourly_list.append(
                    HourlyForecast(
                        time=self._parse_datetime(h.get("fxTime")) or datetime.now(),
                        temp=self._to_float(h.get("temp")),
                        text=h.get("text", ""),
                        icon=h.get("icon", ""),
                        pop=self._to_float(h.get("pop")) if h.get("pop") not in (None, "") else None,
                        precip=self._to_float(h.get("precip")),
                        wind_dir=h.get("windDir", ""),
                        wind_scale=h.get("windScale", ""),
                        humidity=self._to_int(h.get("humidity")),
                        pressure=self._to_float(h.get("pressure")) if h.get("pressure") not in (None, "") else None,
                        cloud=self._to_int(h.get("cloud")) if h.get("cloud") not in (None, "") else None,
                        dew=self._to_float(h.get("dew")) if h.get("dew") not in (None, "") else None,
                    )
                )

        if hourly_list and now_data:
            try:
                now = now_data["now"]
                current_obs_time = self._parse_datetime(now.get("obsTime"))
                if current_obs_time:
                    current_hour_time = current_obs_time.replace(minute=0, second=0, microsecond=0)
                    if hourly_list[0].time > current_hour_time:
                        current_precip = self._to_float(now.get("precip"))
                        next_pop = hourly_list[0].pop if hourly_list[0].pop is not None else 0
                        current_pop = 100.0 if current_precip > 0 else next_pop
                        hourly_list.insert(
                            0,
                            HourlyForecast(
                                time=current_hour_time,
                                temp=self._to_float(now.get("temp")),
                                text=now.get("text", ""),
                                icon=now.get("icon", ""),
                                pop=current_pop,
                                precip=current_precip,
                                wind_dir=now.get("windDir", ""),
                                wind_scale=now.get("windScale", ""),
                                humidity=self._to_int(now.get("humidity")),
                                pressure=self._to_float(now.get("pressure")) if now.get("pressure") else None,
                                cloud=self._to_int(now.get("cloud")) if now.get("cloud") else None,
                                dew=self._to_float(now.get("dew")) if now.get("dew") else None,
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
            fx_time = self._parse_datetime(item.get("fxTime")) or datetime.now()
            minutely_list.append(
                MinutelyPrecipitation(
                    time=fx_time,
                    precip=self._to_float(item.get("precip")),
                    probability=None,
                    precip_type=item.get("type"),
                )
            )
        return minutely_list

    def _map_alerts(self, warning_data: Optional[Dict[str, Any]]) -> List[WarningAlert]:
        alerts: List[WarningAlert] = []
        if not warning_data:
            return alerts

        for item in warning_data.get("alerts", []):
            event_type = item.get("eventType") or {}
            color = item.get("color") or {}
            description = item.get("description") or ""
            instruction = item.get("instruction") or ""
            text = "\n".join(part for part in (description, instruction) if part)
            alerts.append(
                WarningAlert(
                    title=item.get("headline") or item.get("title") or self._as_text(event_type) or "天气预警",
                    type=self._as_text(event_type),
                    level=self._as_text(color) or self._as_text(item.get("severity")),
                    text=text,
                    pub_time=self._parse_datetime(item.get("issuedTime")) or datetime.now(),
                    source="QWeather",
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

        pm2p5 = 0.0
        for pollutant in air_data.get("pollutants", []):
            code = str(pollutant.get("code") or pollutant.get("name") or "").lower().replace(".", "")
            if code in {"pm25", "pm2p5"}:
                concentration = pollutant.get("concentration") or {}
                pm2p5 = self._to_float(concentration.get("value"))
                break

        return AirQuality(
            aqi=self._to_int(selected.get("aqi") or selected.get("aqiDisplay")),
            category=self._as_text(selected.get("category") or selected.get("level")),
            primary=self._as_text(primary),
            pm2p5=pm2p5,
            description=self._as_text(advice.get("generalPopulation") or health.get("effect")),
        )

    def _map_indices(self, indices_data: Optional[Dict[str, Any]]) -> List[LifeIndex]:
        if not indices_data:
            return []
        return [
            LifeIndex(
                type=i["type"],
                name=i["name"],
                category=i["category"],
                text=i.get("text", ""),
            )
            for i in indices_data.get("daily", [])
        ]

    async def get_weather(self, location: str) -> Optional[WeatherData]:
        from utils.cache import cache

        cache_key = (
            f"qweather:{location}:"
            f"{settings.qweather_daily_days}:{settings.qweather_hourly_hours}:"
            f"{settings.qweather_indices_types}:{settings.qweather_enable_minutely}"
        )
        cached = await cache.get(cache_key)
        if cached:
            logger.debug(f"Weather cache hit: {location}")
            return WeatherData(**cached)

        loc_info = await self.get_geo_location(location)
        if not loc_info:
            logger.warning(f"Could not resolve location: {location}")
            return None

        loc_id = loc_info["id"]
        lon = loc_info["lon"]
        lat = loc_info["lat"]
        loc_name = loc_info.get("name", location)
        adm1 = loc_info.get("adm1")
        if adm1:
            loc_name = f"{loc_name}, {adm1}"
        coords = f"{lon},{lat}"
        coord_location = self._coord_location(lon, lat)

        from utils.cache import cache as api_cache

        now = datetime.now()
        midnight = datetime.combine(now.date(), dtime(23, 59, 59))
        seconds_until_midnight = max(60, int((midnight - now).total_seconds()))

        now_key = f"qw:now:{loc_id}"
        now_data = await api_cache.get(now_key)
        if not now_data:
            now_data = await self._request("/v7/weather/now", {"location": loc_id})
            if now_data:
                await api_cache.set(now_key, now_data, ttl=600)

        minutely_data = None
        if settings.qweather_enable_minutely:
            minutely_key = f"qw:minutely:{coord_location}"
            minutely_data = await api_cache.get(minutely_key)
            if not minutely_data:
                minutely_data = await self._request("/v7/minutely/5m", {"location": coord_location})
                if minutely_data:
                    await api_cache.set(minutely_key, minutely_data, ttl=300)

        air_key = f"qw:air:v1:{coord_location}"
        air_data = await api_cache.get(air_key)
        if not air_data:
            air_data = await self._request(f"/airquality/v1/current/{lat}/{lon}")
            if air_data:
                await api_cache.set(air_key, air_data, ttl=3600)

        warning_key = f"qw:warning:v1:{coord_location}"
        warning_data = await api_cache.get(warning_key)
        if not warning_data:
            warning_data = await self._request(
                f"/weatheralert/v1/current/{lat}/{lon}",
                {"localTime": "true"},
            )
            if warning_data:
                await api_cache.set(warning_key, warning_data, ttl=1800)

        daily_key = f"qw:daily:{settings.qweather_daily_days}:{loc_id}"
        daily_data = await api_cache.get(daily_key)
        if not daily_data:
            daily_data = await self._request(f"/v7/weather/{settings.qweather_daily_days}", {"location": loc_id})
            if daily_data:
                await api_cache.set(daily_key, daily_data, ttl=43200)

        hourly_key = f"qw:hourly:{settings.qweather_hourly_hours}:{loc_id}"
        hourly_data = await api_cache.get(hourly_key)
        if not hourly_data:
            hourly_data = await self._request(f"/v7/weather/{settings.qweather_hourly_hours}", {"location": loc_id})
            if hourly_data:
                await api_cache.set(hourly_key, hourly_data, ttl=21600)

        indices_key = f"qw:indices:{settings.qweather_indices_types}:{loc_id}"
        indices_data = await api_cache.get(indices_key)
        if not indices_data:
            indices_data = await self._request(
                "/v7/indices/1d",
                {"location": loc_id, "type": settings.qweather_indices_types},
            )
            if indices_data:
                await api_cache.set(indices_key, indices_data, ttl=seconds_until_midnight)

        if not now_data:
            return None

        now_weather = now_data["now"]
        daily_list = self._map_daily(daily_data)
        hourly_list = self._map_hourly(hourly_data, now_data)
        minutely_list = self._map_minutely(minutely_data)
        alerts_list = self._map_alerts(warning_data)
        aqi_obj = self._map_air_quality(air_data)
        indices_list = self._map_indices(indices_data)

        now_precip = self._to_float(now_weather.get("precip"))
        minutely_summary = minutely_data.get("summary") if minutely_data else ""
        summary = f"当前 {now_weather['text']}，温度 {now_weather['temp']}°C。"
        if minutely_summary:
            summary = f"{summary}\n{minutely_summary}"

        is_raining = now_precip > 0 or any(item.precip > 0 for item in minutely_list[:6])
        update_time = (
            self._parse_datetime(now_data.get("updateTime"))
            or self._parse_datetime(now_weather.get("obsTime"))
            or datetime.now()
        )

        weather_obj = WeatherData(
            source="qweather",
            update_time=update_time,
            location_name=loc_name,
            coords=coords,
            now_temp=self._to_float(now_weather.get("temp")),
            now_feels_like=self._to_float(now_weather.get("feelsLike")),
            now_text=now_weather["text"],
            now_icon=now_weather["icon"],
            now_wind_dir=now_weather.get("windDir"),
            now_wind_scale=now_weather.get("windScale"),
            now_humidity=self._to_int(now_weather.get("humidity")),
            now_precip=now_precip,
            now_pressure=self._to_int(now_weather.get("pressure")),
            now_vis=self._to_float(now_weather.get("vis")),
            summary=summary,
            daily=daily_list,
            hourly=hourly_list,
            minutely=minutely_list,
            air_quality=aqi_obj,
            alerts=alerts_list,
            indices=indices_list,
            is_raining=is_raining,
        )

        await cache.set(cache_key, weather_obj.model_dump(mode="json"), ttl=600)
        logger.debug(f"Cached weather for {location} (TTL=10m)")
        return weather_obj
