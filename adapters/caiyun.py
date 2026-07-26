import httpx
from datetime import datetime, timezone
import time
from typing import Any, Optional, Dict, List
from zoneinfo import ZoneInfo
from loguru import logger

from core.config import settings
from adapters.base import WeatherAdapter
from domain.models import (
    WeatherData, WarningAlert,
    HourlyForecast, DailyForecast, AirQuality, LifeIndex
)

# Helper: Map Caiyun Skycon to Text & Icon
# Source: https://docs.caiyunapp.com/docs/tables/skycon/
SKYCON_MAP = {
    "CLEAR_DAY": ("晴", "☀️"),
    "CLEAR_NIGHT": ("晴", "🌙"),
    "PARTLY_CLOUDY_DAY": ("多云", "⛅"),
    "PARTLY_CLOUDY_NIGHT": ("多云", "☁️"),
    "CLOUDY": ("阴", "☁️"),
    "LIGHT_HAZE": ("轻度雾霾", "🌫️"),
    "MODERATE_HAZE": ("中度雾霾", "🌫️"),
    "HEAVY_HAZE": ("重度雾霾", "🌫️"),
    "LIGHT_RAIN": ("小雨", "🌧️"),
    "MODERATE_RAIN": ("中雨", "🌧️"),
    "HEAVY_RAIN": ("大雨", "🌧️"),
    "STORM_RAIN": ("暴雨", "⛈️"),
    "FOG": ("雾", "🌫️"),
    "LIGHT_SNOW": ("小雪", "🌨️"),
    "MODERATE_SNOW": ("中雪", "🌨️"),
    "HEAVY_SNOW": ("大雪", "🌨️"),
    "STORM_SNOW": ("暴雪", "❄️"),
    "DUST": ("浮尘", "🌪️"),
    "SAND": ("沙尘", "🌪️"),
    "WIND": ("大风", "🌬️"),
}

class CaiyunAdapter(WeatherAdapter):
    """
    Adapter for Caiyun Weather (Minute-level Expert & Comprehensive Data)
    API Docs: https://docs.caiyunapp.com/weather-api/v2.6/structure/realtime.html
    """
    
    def __init__(self):
        self.token = settings.caiyun_api_token
        self.client = httpx.AsyncClient(timeout=10.0, http2=True)

    async def aclose(self):
        await self.client.aclose()
    
    def _get_skycon_info(self, skycon: Optional[str]) -> tuple[str, str]:
        if not skycon:
            return "", ""
        return SKYCON_MAP.get(skycon, (skycon, "❓"))

    @staticmethod
    def _safe_float(value, default: float = 0.0) -> float:
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _optional_float(cls, value: Any) -> Optional[float]:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _optional_int(cls, value: Any) -> Optional[int]:
        parsed = cls._optional_float(value)
        return int(round(parsed)) if parsed is not None else None

    @staticmethod
    def _parse_datetime(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    @staticmethod
    def _series_map(items: Any, *, daily: bool = False) -> Dict[str, Dict[str, Any]]:
        mapped: Dict[str, Dict[str, Any]] = {}
        if not isinstance(items, list):
            return mapped
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_key = item.get("date") or item.get("datetime")
            if raw_key is None:
                continue
            key = str(raw_key)[:10] if daily else str(raw_key)
            mapped[key] = item
        return mapped

    @classmethod
    def _percent_value(cls, value: Any) -> Optional[int]:
        parsed = cls._optional_float(value)
        if parsed is None:
            return None
        return int(round(parsed * 100 if parsed <= 1 else parsed))

    @classmethod
    def _hpa_value(cls, value: Any) -> Optional[float]:
        parsed = cls._optional_float(value)
        return parsed / 100 if parsed is not None else None

    @classmethod
    def _probability_over(cls, value, threshold: float) -> bool:
        probability = cls._safe_float(value)
        if probability > 1:
            probability = probability / 100
        return probability > threshold

    @classmethod
    def _probability_pct(cls, value) -> Optional[float]:
        if value in (None, ""):
            return None
        probability = cls._safe_float(value)
        return probability * 100 if probability <= 1 else probability

    @staticmethod
    def _normalize_location(location: str) -> Optional[tuple[str, str]]:
        try:
            lon_text, lat_text = (part.strip() for part in location.split(",", 1))
            lon = float(lon_text)
            lat = float(lat_text)
        except (TypeError, ValueError):
            return None

        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None

        request_location = f"{lon:.6f},{lat:.6f}"
        cache_location = f"{lon:.4f},{lat:.4f}"
        return request_location, cache_location

    @staticmethod
    def _requires_long_cooldown(status_code: Optional[int], error_text: str = "") -> bool:
        if status_code in {401, 403}:
            return True
        normalized = error_text.lower()
        return any(
            marker in normalized
            for marker in ("token", "auth", "quota", "credit", "balance", "次数", "额度", "余额")
        )

    async def _set_failure_cooldown(
        self,
        key: str,
        *,
        status_code: Optional[int] = None,
        error_text: str = "",
    ) -> None:
        from utils.cache import cache

        ttl = (
            settings.caiyun_cache_ttl_seconds
            if self._requires_long_cooldown(status_code, error_text)
            else settings.caiyun_failure_cooldown_seconds
        )
        await cache.set(
            key,
            {"status_code": status_code, "failure": True},
            ttl=ttl,
        )

    async def _get_payload(
        self,
        request_location: str,
        cache_location: str,
    ) -> Optional[Dict[str, Any]]:
        from utils.cache import cache

        cache_key = (
            f"caiyun:weather:v3:{cache_location}:metric-v2:"
            f"h{settings.caiyun_hourly_steps}:d{settings.caiyun_daily_steps}:a1"
        )
        cooldown_key = f"{cache_key}:cooldown"

        cached = await cache.get(cache_key)
        if isinstance(cached, dict):
            logger.debug(f"Caiyun cache hit: {cache_location}")
            return cached

        if await cache.get(cooldown_key) is not None:
            logger.warning(f"Caiyun request skipped during failure cooldown: {cache_location}")
            return None

        async def fetch_payload() -> Optional[Dict[str, Any]]:
            # Another waiter may have established a cooldown before this loader
            # starts. Checking again avoids an unnecessary paid call.
            if await cache.get(cooldown_key) is not None:
                return None

            logger.info(f"Caiyun cache miss: {cache_location}")

            url = f"https://api.caiyunapp.com/v2.6/{self.token}/{request_location}/weather"
            params = {
                "alert": "true",
                "dailysteps": str(settings.caiyun_daily_steps),
                "hourlysteps": str(settings.caiyun_hourly_steps),
                "unit": "metric:v2",
            }
            started_at = time.perf_counter()
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as error:
                status_code = error.response.status_code
                await self._set_failure_cooldown(cooldown_key, status_code=status_code)
                logger.error(
                    f"Caiyun API HTTP failure: status={status_code} location={cache_location}"
                )
                return None
            except (httpx.RequestError, ValueError) as error:
                await self._set_failure_cooldown(cooldown_key)
                logger.error(
                    f"Caiyun API request failure: type={type(error).__name__} location={cache_location}"
                )
                return None
            except Exception as error:
                await self._set_failure_cooldown(cooldown_key)
                logger.error(
                    f"Caiyun API unexpected failure: type={type(error).__name__} location={cache_location}"
                )
                return None

            if not isinstance(data, dict) or data.get("status") != "ok":
                error_text = str(data.get("error", "")) if isinstance(data, dict) else "invalid response"
                await self._set_failure_cooldown(cooldown_key, error_text=error_text)
                logger.warning(f"Caiyun API returned non-ok status for {cache_location}")
                return None

            result = data.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("realtime"), dict):
                await self._set_failure_cooldown(cooldown_key)
                logger.warning(f"Caiyun API response missing required blocks for {cache_location}")
                return None

            elapsed = time.perf_counter() - started_at
            hourly_count = len(result.get("hourly", {}).get("temperature", []))
            daily_count = len(result.get("daily", {}).get("temperature", []))
            logger.info(
                "Caiyun API success: location={} hourly={} daily={} elapsed={:.2f}s",
                cache_location,
                hourly_count,
                daily_count,
                elapsed,
            )
            return data

        payload = await cache.get_or_set(
            cache_key,
            fetch_payload,
            ttl=settings.caiyun_cache_ttl_seconds,
        )
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _target_timezone(data: Dict[str, Any]):
        timezone_name = data.get("timezone")
        if timezone_name:
            try:
                return ZoneInfo(str(timezone_name))
            except Exception:
                pass
        return timezone.utc

    def _parse_alerts(self, result: Dict[str, Any], target_tz) -> List[WarningAlert]:
        alerts: List[WarningAlert] = []
        level_map = {
            "00": "白色",
            "01": "蓝色",
            "02": "黄色",
            "03": "橙色",
            "04": "红色",
        }
        content = (result.get("alert") or {}).get("content", [])
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            published = self._optional_float(item.get("pubtimestamp"))
            alerts.append(
                WarningAlert(
                    title=str(item.get("title") or ""),
                    type=code[:2] if len(code) >= 2 else code,
                    level=level_map.get(code[-2:], ""),
                    status=str(item.get("status") or ""),
                    text=str(item.get("description") or ""),
                    pub_time=(
                        datetime.fromtimestamp(published, tz=target_tz)
                        if published is not None
                        else datetime.now(tz=target_tz)
                    ),
                    source=str(item.get("source") or "Caiyun"),
                    alert_id=str(item.get("alertId") or "") or None,
                )
            )
        return alerts

    def _parse_air_quality(self, raw: Any) -> Optional[AirQuality]:
        if not isinstance(raw, dict):
            return None
        aqi_data = raw.get("aqi") or {}
        description_data = raw.get("description") or {}
        aqi = self._optional_int(aqi_data.get("chn") if isinstance(aqi_data, dict) else aqi_data)
        description = (
            str(description_data.get("chn") or "")
            if isinstance(description_data, dict)
            else str(description_data or "")
        )
        pollutant_values = {
            name: self._optional_float(raw.get(api_name))
            for name, api_name in {
                "pm2p5": "pm25",
                "pm10": "pm10",
                "o3": "o3",
                "so2": "so2",
                "no2": "no2",
                "co": "co",
            }.items()
        }
        if aqi is None and not any(value is not None for value in pollutant_values.values()):
            return None
        return AirQuality(
            aqi=aqi,
            category=description,
            primary="",
            description=description,
            source="caiyun",
            field_sources={
                **({"aqi": "caiyun"} if aqi is not None else {}),
                **({key: "caiyun" for key, value in pollutant_values.items() if value is not None}),
            },
            **pollutant_values,
        )

    def _parse_hourly(self, hourly_data: Any) -> List[HourlyForecast]:
        if not isinstance(hourly_data, dict):
            return []

        temperatures = hourly_data.get("temperature") or []
        series = {
            name: self._series_map(hourly_data.get(api_name, []))
            for name, api_name in {
                "skycon": "skycon",
                "wind": "wind",
                "precip": "precipitation",
                "humidity": "humidity",
                "feels_like": "apparent_temperature",
                "pressure": "pressure",
                "cloud": "cloudrate",
                "visibility": "visibility",
                "radiation": "dswrf",
            }.items()
        }
        air_quality = hourly_data.get("air_quality") or {}
        aqi_map = self._series_map(air_quality.get("aqi", [])) if isinstance(air_quality, dict) else {}
        pm25_map = self._series_map(air_quality.get("pm25", [])) if isinstance(air_quality, dict) else {}

        forecasts: List[HourlyForecast] = []
        for temperature in temperatures if isinstance(temperatures, list) else []:
            if not isinstance(temperature, dict):
                continue
            key = str(temperature.get("datetime") or "")
            forecast_time = self._parse_datetime(key)
            temp_value = self._optional_float(temperature.get("value"))
            if forecast_time is None or temp_value is None:
                continue

            skycon_item = series["skycon"].get(key, {})
            skycon = skycon_item.get("value")
            text, icon = self._get_skycon_info(str(skycon) if skycon else None)
            wind = series["wind"].get(key, {})
            precip_item = series["precip"].get(key, {})
            humidity_item = series["humidity"].get(key, {})
            feels_like_item = series["feels_like"].get(key, {})
            pressure_item = series["pressure"].get(key, {})
            cloud_item = series["cloud"].get(key, {})
            visibility_item = series["visibility"].get(key, {})
            radiation_item = series["radiation"].get(key, {})

            precip = self._optional_float(precip_item.get("value"))
            feels_like = self._optional_float(feels_like_item.get("value"))
            humidity = self._percent_value(humidity_item.get("value"))
            cloud = self._percent_value(cloud_item.get("value"))
            aqi_raw = (aqi_map.get(key, {}) or {}).get("value")
            if isinstance(aqi_raw, dict):
                aqi_raw = aqi_raw.get("chn")
            aqi = self._optional_int(aqi_raw)
            pm2p5 = self._optional_float((pm25_map.get(key, {}) or {}).get("value"))

            field_sources = {
                "temperature": "caiyun",
                **({"weather": "caiyun"} if skycon else {}),
                **({"feels_like": "caiyun"} if feels_like is not None else {}),
                **({"precip": "caiyun"} if precip is not None else {}),
                **({"aqi": "caiyun"} if aqi is not None else {}),
                **({"pm2p5": "caiyun"} if pm2p5 is not None else {}),
            }
            forecasts.append(
                HourlyForecast(
                    time=forecast_time,
                    temp=temp_value,
                    feels_like=feels_like,
                    feels_like_estimated=False,
                    feels_like_source="caiyun" if feels_like is not None else None,
                    text=text,
                    icon=icon,
                    pop=self._probability_pct(precip_item.get("probability")),
                    precip=precip,
                    precip_kind="intensity" if precip is not None else None,
                    precip_source="caiyun" if precip is not None else None,
                    wind_dir="",
                    wind_speed=self._optional_float(wind.get("speed")),
                    wind_direction_degrees=self._optional_float(wind.get("direction")),
                    humidity=humidity,
                    pressure=self._hpa_value(pressure_item.get("value")),
                    cloud=cloud,
                    visibility=self._optional_float(visibility_item.get("value")),
                    radiation=self._optional_float(radiation_item.get("value")),
                    aqi=aqi,
                    pm2p5=pm2p5,
                    field_sources=field_sources,
                )
            )
        return forecasts

    def _parse_daily(self, daily_data: Any) -> List[DailyForecast]:
        if not isinstance(daily_data, dict):
            return []

        temperatures = daily_data.get("temperature") or []
        series = {
            name: self._series_map(daily_data.get(api_name, []), daily=True)
            for name, api_name in {
                "skycon": "skycon",
                "skycon_day": "skycon_08h_20h",
                "skycon_night": "skycon_20h_32h",
                "astro": "astro",
                "precip": "precipitation",
                "precip_day": "precipitation_08h_20h",
                "precip_night": "precipitation_20h_32h",
                "humidity": "humidity",
                "pressure": "pressure",
                "cloud": "cloudrate",
                "visibility": "visibility",
                "radiation": "dswrf",
                "wind_day": "wind_08h_20h",
                "wind_night": "wind_20h_32h",
            }.items()
        }
        air_quality = daily_data.get("air_quality") or {}
        aqi_map = self._series_map(air_quality.get("aqi", []), daily=True) if isinstance(air_quality, dict) else {}
        pm25_map = self._series_map(air_quality.get("pm25", []), daily=True) if isinstance(air_quality, dict) else {}

        forecasts: List[DailyForecast] = []
        for temperature in temperatures if isinstance(temperatures, list) else []:
            if not isinstance(temperature, dict):
                continue
            key = str(temperature.get("date") or "")[:10]
            forecast_date = self._parse_datetime(key)
            temp_min = self._optional_float(temperature.get("min"))
            temp_max = self._optional_float(temperature.get("max"))
            if forecast_date is None or temp_min is None or temp_max is None:
                continue

            skycon_default = series["skycon"].get(key, {}).get("value")
            skycon_day = series["skycon_day"].get(key, {}).get("value") or skycon_default
            skycon_night = series["skycon_night"].get(key, {}).get("value") or skycon_default
            text_day, icon_day = self._get_skycon_info(str(skycon_day) if skycon_day else None)
            text_night, icon_night = self._get_skycon_info(str(skycon_night) if skycon_night else None)
            astro = series["astro"].get(key, {})
            precip = series["precip"].get(key, {})
            precip_day = series["precip_day"].get(key, {})
            precip_night = series["precip_night"].get(key, {})
            humidity = series["humidity"].get(key, {})
            pressure = series["pressure"].get(key, {})
            cloud = series["cloud"].get(key, {})
            visibility = series["visibility"].get(key, {})
            radiation = series["radiation"].get(key, {})
            wind_day = series["wind_day"].get(key, {})
            wind_night = series["wind_night"].get(key, {})
            aqi_raw = (aqi_map.get(key, {}) or {}).get("avg")
            if isinstance(aqi_raw, dict):
                aqi_raw = aqi_raw.get("chn")

            precip_value = self._optional_float(precip.get("avg"))
            forecasts.append(
                DailyForecast(
                    date=forecast_date.replace(tzinfo=None),
                    temp_min=temp_min,
                    temp_max=temp_max,
                    temp_avg=self._optional_float(temperature.get("avg")),
                    text_day=text_day,
                    icon_day=icon_day,
                    text_night=text_night,
                    icon_night=icon_night,
                    precip=precip_value,
                    precip_kind="intensity" if precip_value is not None else None,
                    precip_source="caiyun" if precip_value is not None else None,
                    precip_probability=self._probability_pct(precip.get("probability")),
                    precip_day=self._optional_float(precip_day.get("avg")),
                    precip_day_probability=self._probability_pct(precip_day.get("probability")),
                    precip_night=self._optional_float(precip_night.get("avg")),
                    precip_night_probability=self._probability_pct(precip_night.get("probability")),
                    sunrise=(astro.get("sunrise") or {}).get("time"),
                    sunset=(astro.get("sunset") or {}).get("time"),
                    humidity=self._percent_value(humidity.get("avg")),
                    pressure=self._hpa_value(pressure.get("avg")),
                    cloud=self._percent_value(cloud.get("avg")),
                    vis=self._optional_float(visibility.get("avg")),
                    radiation=self._optional_float(radiation.get("avg")),
                    wind_speed_day=self._optional_float(wind_day.get("avg", {}).get("speed") if isinstance(wind_day.get("avg"), dict) else wind_day.get("speed")),
                    wind_direction_day_degrees=self._optional_float(wind_day.get("avg", {}).get("direction") if isinstance(wind_day.get("avg"), dict) else wind_day.get("direction")),
                    wind_speed_night=self._optional_float(wind_night.get("avg", {}).get("speed") if isinstance(wind_night.get("avg"), dict) else wind_night.get("speed")),
                    wind_direction_night_degrees=self._optional_float(wind_night.get("avg", {}).get("direction") if isinstance(wind_night.get("avg"), dict) else wind_night.get("direction")),
                    aqi=self._optional_int(aqi_raw),
                    pm2p5=self._optional_float((pm25_map.get(key, {}) or {}).get("avg")),
                    field_sources={
                        "temperature": "caiyun",
                        **({"weather": "caiyun"} if skycon_day or skycon_night else {}),
                        **({"precip": "caiyun"} if precip_value is not None else {}),
                    },
                )
            )
        return forecasts

    def _parse_daily_indices(self, daily_data: Any) -> List[LifeIndex]:
        if not isinstance(daily_data, dict):
            return []
        life_index = daily_data.get("life_index") or {}
        if not isinstance(life_index, dict):
            return []
        index_map = {
            "ultraviolet": ("5", "紫外线指数"),
            "carWashing": ("2", "洗车指数"),
            "dressing": ("3", "穿衣指数"),
            "coldRisk": ("9", "感冒指数"),
        }
        indices: List[LifeIndex] = []
        for key, (type_id, name) in index_map.items():
            raw = life_index.get(key)
            item = raw[0] if isinstance(raw, list) and raw else raw
            if not isinstance(item, dict):
                continue
            indices.append(
                LifeIndex(
                    type=type_id,
                    name=name,
                    category=str(item.get("desc") or ""),
                    text=str(item.get("desc") or ""),
                    value=str(item.get("index")) if item.get("index") not in (None, "") else None,
                    date=self._parse_datetime(item.get("date")),
                    source="caiyun",
                )
            )
        return indices

    def _parse_payload(
        self,
        data: Dict[str, Any],
        location: str,
    ) -> Optional[WeatherData]:
        result = data.get("result") or {}
        realtime = result.get("realtime") or {}
        now_temp = self._optional_float(realtime.get("temperature"))
        if now_temp is None:
            return None

        skycon = realtime.get("skycon")
        text, icon = self._get_skycon_info(str(skycon) if skycon else None)
        local_precip = ((realtime.get("precipitation") or {}).get("local") or {})
        realtime_precip = self._optional_float(local_precip.get("intensity"))
        wind = realtime.get("wind") or {}
        hourly = self._parse_hourly(result.get("hourly"))
        daily = self._parse_daily(result.get("daily"))
        summary = str(result.get("forecast_keypoint") or "")
        target_tz = self._target_timezone(data)
        server_time = self._optional_float(data.get("server_time"))
        update_time = (
            datetime.fromtimestamp(server_time, tz=target_tz)
            if server_time is not None
            else datetime.now(tz=target_tz)
        )
        is_raining = (
            (realtime_precip or 0) > 0
            or any((item.precip or 0) > 0 for item in hourly[:3])
            or any((item.pop or 0) > 30 for item in hourly[:3])
        )

        return WeatherData(
            source="caiyun",
            update_time=update_time,
            location_name="Current Location",
            coords=location,
            now_temp=now_temp,
            now_feels_like=self._optional_float(realtime.get("apparent_temperature")),
            now_text=text,
            now_icon=icon,
            now_wind_dir="",
            now_wind_scale="",
            now_wind_speed=self._optional_float(wind.get("speed")),
            now_wind_direction_degrees=self._optional_float(wind.get("direction")),
            now_humidity=self._percent_value(realtime.get("humidity")),
            now_precip=realtime_precip,
            now_precip_kind="intensity" if realtime_precip is not None else None,
            now_precip_source="caiyun" if realtime_precip is not None else None,
            now_pressure=self._hpa_value(realtime.get("pressure")),
            now_vis=self._optional_float(realtime.get("visibility")),
            now_cloud=self._percent_value(realtime.get("cloudrate")),
            now_radiation=self._optional_float(realtime.get("dswrf")),
            summary=summary,
            provider_summaries={"caiyun": summary} if summary else {},
            field_sources={
                "now_temp": "caiyun",
                **({"now_feels_like": "caiyun"} if realtime.get("apparent_temperature") not in (None, "") else {}),
                **({"now_text": "caiyun"} if skycon else {}),
            },
            minutely=[],
            hourly=hourly,
            daily=daily,
            alerts=self._parse_alerts(result, target_tz),
            air_quality=self._parse_air_quality(realtime.get("air_quality")),
            indices=self._parse_daily_indices(result.get("daily")),
            is_raining=is_raining,
            timezone=str(data.get("timezone") or "") or None,
        )

    async def get_weather(self, location: str) -> Optional[WeatherData]:
        """
        Get Caiyun Data.
        Location MUST be 'lon,lat' format for Caiyun.
        """
        if not self.token:
            logger.warning("Caiyun API token is missing; skipping Caiyun request.")
            return None

        normalized_location = self._normalize_location(location)
        if normalized_location is None:
            logger.warning(f"Caiyun requires coordinates (lon,lat), got: {location}")
            return None

        clean_location, cache_location = normalized_location
        data = await self._get_payload(clean_location, cache_location)
        if data is None:
            return None

        try:
            return self._parse_payload(data, clean_location)
        except Exception as error:
            logger.error(
                f"Caiyun response mapping failed: type={type(error).__name__} location={cache_location}"
            )
            return None
