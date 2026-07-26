import asyncio
from typing import Optional

from loguru import logger

from adapters.caiyun import CaiyunAdapter
from adapters.qweather import QWeatherAdapter, WeatherProfile
from core.config import settings
from domain.models import WeatherData


class WeatherFusionService:
    """QWeather-first router with globally cached Caiyun enrichment."""

    def __init__(self):
        self.qweather = QWeatherAdapter()
        self.caiyun = CaiyunAdapter()

    async def aclose(self):
        await asyncio.gather(
            self.qweather.aclose(),
            self.caiyun.aclose(),
            return_exceptions=True,
        )

    @property
    def caiyun_enabled(self) -> bool:
        return settings.enable_caiyun_api

    @staticmethod
    def _hour_key(value):
        """Build a timezone-safe key for matching provider hourly records."""
        if value.tzinfo is not None and value.utcoffset() is not None:
            return ("utc", int(value.timestamp() // 3600))
        return ("local", value.year, value.month, value.day, value.hour)

    @classmethod
    def _enrich_hourly_feels_like(cls, qweather_hours, caiyun_hours) -> int:
        """Fill missing QWeather feels-like values with native Caiyun values only."""
        native_caiyun = {
            cls._hour_key(hour.time): hour
            for hour in caiyun_hours
            if hour.feels_like is not None and not hour.feels_like_estimated
        }
        enriched = 0
        for hour in qweather_hours:
            if hour.feels_like is not None:
                continue
            caiyun_hour = native_caiyun.get(cls._hour_key(hour.time))
            if caiyun_hour is None:
                continue
            hour.feels_like = caiyun_hour.feels_like
            hour.feels_like_estimated = False
            hour.feels_like_source = "caiyun"
            enriched += 1
        return enriched

    @classmethod
    def _merge_hourly(cls, qweather_hours, caiyun_hours) -> int:
        """Merge hourly records by timestamp while preserving valid zeroes."""
        enriched_feels_like = cls._enrich_hourly_feels_like(
            qweather_hours,
            caiyun_hours,
        )
        caiyun_by_hour = {
            cls._hour_key(hour.time): hour
            for hour in caiyun_hours
        }
        qweather_keys = set()
        optional_fields = (
            "pop",
            "wind_speed",
            "wind_direction_degrees",
            "humidity",
            "pressure",
            "cloud",
            "visibility",
            "radiation",
            "aqi",
            "pm2p5",
        )
        for hour in qweather_hours:
            key = cls._hour_key(hour.time)
            qweather_keys.add(key)
            caiyun_hour = caiyun_by_hour.get(key)
            if caiyun_hour is None:
                continue
            for field in optional_fields:
                if getattr(hour, field) is None and getattr(caiyun_hour, field) is not None:
                    setattr(hour, field, getattr(caiyun_hour, field))
                    hour.field_sources[field] = "caiyun"
            if hour.precip is None and caiyun_hour.precip is not None:
                hour.precip = caiyun_hour.precip
                hour.precip_kind = caiyun_hour.precip_kind
                hour.precip_source = "caiyun"
                hour.field_sources["precip"] = "caiyun"
            if not hour.text and caiyun_hour.text:
                hour.text = caiyun_hour.text
                hour.icon = caiyun_hour.icon
                hour.field_sources["weather"] = "caiyun"

        for caiyun_hour in caiyun_hours:
            if cls._hour_key(caiyun_hour.time) not in qweather_keys:
                qweather_hours.append(caiyun_hour.model_copy(deep=True))
        qweather_hours.sort(key=lambda hour: cls._hour_key(hour.time))
        return enriched_feels_like

    @staticmethod
    def _merge_daily(qweather_days, caiyun_days) -> None:
        caiyun_by_date = {day.date.date(): day for day in caiyun_days}
        qweather_dates = set()
        optional_fields = (
            "temp_avg",
            "precip_probability",
            "precip_day",
            "precip_day_probability",
            "precip_night",
            "precip_night_probability",
            "humidity",
            "vis",
            "pressure",
            "cloud",
            "radiation",
            "wind_speed_day",
            "wind_direction_day_degrees",
            "wind_speed_night",
            "wind_direction_night_degrees",
            "aqi",
            "pm2p5",
        )
        for day in qweather_days:
            date_key = day.date.date()
            qweather_dates.add(date_key)
            caiyun_day = caiyun_by_date.get(date_key)
            if caiyun_day is None:
                continue
            for field in optional_fields:
                if getattr(day, field) is None and getattr(caiyun_day, field) is not None:
                    setattr(day, field, getattr(caiyun_day, field))
                    day.field_sources[field] = "caiyun"
            if day.precip is None and caiyun_day.precip is not None:
                day.precip = caiyun_day.precip
                day.precip_kind = caiyun_day.precip_kind
                day.precip_source = "caiyun"
                day.field_sources["precip"] = "caiyun"

        for caiyun_day in caiyun_days:
            if caiyun_day.date.date() not in qweather_dates:
                qweather_days.append(caiyun_day.model_copy(deep=True))
        qweather_days.sort(key=lambda day: day.date)

    @staticmethod
    def _merge_realtime(qweather_data: WeatherData, caiyun_data: WeatherData) -> None:
        optional_fields = (
            "now_feels_like",
            "now_wind_speed",
            "now_wind_direction_degrees",
            "now_humidity",
            "now_pressure",
            "now_vis",
            "now_cloud",
            "now_radiation",
        )
        for field in optional_fields:
            if getattr(qweather_data, field) is None and getattr(caiyun_data, field) is not None:
                setattr(qweather_data, field, getattr(caiyun_data, field))
                qweather_data.field_sources[field] = "caiyun"
        if qweather_data.now_precip is None and caiyun_data.now_precip is not None:
            qweather_data.now_precip = caiyun_data.now_precip
            qweather_data.now_precip_kind = caiyun_data.now_precip_kind
            qweather_data.now_precip_source = "caiyun"
            qweather_data.field_sources["now_precip"] = "caiyun"
        if not qweather_data.now_text and caiyun_data.now_text:
            qweather_data.now_text = caiyun_data.now_text
            qweather_data.now_icon = caiyun_data.now_icon
            qweather_data.field_sources["now_text"] = "caiyun"

    @staticmethod
    def _merge_air_quality(qweather_data: WeatherData, caiyun_data: WeatherData) -> None:
        if qweather_data.air_quality is None:
            if caiyun_data.air_quality is not None:
                qweather_data.air_quality = caiyun_data.air_quality.model_copy(deep=True)
            return
        if caiyun_data.air_quality is None:
            return
        qweather_air = qweather_data.air_quality
        caiyun_air = caiyun_data.air_quality
        for field in ("aqi", "pm2p5", "pm10", "o3", "so2", "no2", "co"):
            if getattr(qweather_air, field) is None and getattr(caiyun_air, field) is not None:
                setattr(qweather_air, field, getattr(caiyun_air, field))
                qweather_air.field_sources[field] = "caiyun"
        if not qweather_air.category and caiyun_air.category:
            qweather_air.category = caiyun_air.category
            qweather_air.field_sources["category"] = "caiyun"
        if not qweather_air.description and caiyun_air.description:
            qweather_air.description = caiyun_air.description
            qweather_air.field_sources["description"] = "caiyun"

    @staticmethod
    def _geo_location_name(loc_info: dict, fallback: str) -> str:
        name = loc_info.get("name") or fallback
        adm1 = loc_info.get("adm1")
        return f"{name}, {adm1}" if adm1 else name

    @classmethod
    def _merge_weather(
        cls,
        qweather_data: WeatherData,
        caiyun_data: WeatherData,
        profile: WeatherProfile,
    ) -> WeatherData:
        """Merge native provider values without replacing valid QWeather fields."""
        # The current Caiyun package has no minutely entitlement. QWeather
        # remains the sole minute-level source even if an unexpected block is
        # present in a provider response.

        cls._merge_realtime(qweather_data, caiyun_data)

        if profile in {"full", "hourly", "rain", "indices"}:
            cls._merge_air_quality(qweather_data, caiyun_data)

        if not qweather_data.alerts and caiyun_data.alerts:
            qweather_data.alerts = caiyun_data.alerts

        if profile in {"full", "hourly", "rain"}:
            if not qweather_data.hourly and caiyun_data.hourly:
                qweather_data.hourly = [hour.model_copy(deep=True) for hour in caiyun_data.hourly]
            elif qweather_data.hourly and caiyun_data.hourly:
                enriched = cls._merge_hourly(
                    qweather_data.hourly,
                    caiyun_data.hourly,
                )
                if enriched:
                    logger.info(f"Fusion: added native Caiyun feels-like to {enriched} hours")

        if profile in {"full", "daily"}:
            if not qweather_data.daily and caiyun_data.daily:
                qweather_data.daily = [day.model_copy(deep=True) for day in caiyun_data.daily]
            elif qweather_data.daily and caiyun_data.daily:
                cls._merge_daily(qweather_data.daily, caiyun_data.daily)

        if profile in {"full", "indices"} and caiyun_data.indices:
            existing_types = {index.type for index in qweather_data.indices}
            qweather_data.indices.extend(
                index.model_copy(deep=True)
                for index in caiyun_data.indices
                if index.type not in existing_types
            )

        if caiyun_data.summary:
            if qweather_data.summary:
                qweather_data.summary = (
                    f"{qweather_data.summary}\n彩云提示：{caiyun_data.summary}"
                )
            else:
                qweather_data.summary = caiyun_data.summary

        qweather_data.provider_summaries.update(caiyun_data.provider_summaries)
        if qweather_data.timezone is None:
            qweather_data.timezone = caiyun_data.timezone

        qweather_data.source = "fusion"
        return qweather_data

    async def get_fused_weather(
        self,
        location: str,
        *,
        profile: WeatherProfile = "full",
        refresh_qweather: bool = False,
    ) -> Optional[WeatherData]:
        """Fetch QWeather and globally cached Caiyun data for one location."""
        logger.info(f"Fusion: resolving QWeather location for '{location}'")
        loc_info = await self.qweather.get_geo_location(location)

        if not loc_info:
            # Caiyun has no geocoder in the purchased package. It can only be a
            # final fallback when the caller already supplied coordinates.
            if self.caiyun_enabled and settings.caiyun_api_token and "," in location:
                caiyun_only = await self.caiyun.get_weather(location)
                if caiyun_only is not None:
                    logger.warning("Fusion: QWeather Geo failed; returning Caiyun coordinate fallback")
                return caiyun_only
            logger.warning(f"Fusion: could not resolve location '{location}'")
            return None

        coords = f"{loc_info['lon']},{loc_info['lat']}"
        qweather_task = asyncio.create_task(
            self.qweather.get_weather(
                location,
                profile=profile,
                refresh_qweather=refresh_qweather,
                loc_info=loc_info,
            )
        )

        caiyun_task = None
        if self.caiyun_enabled and settings.caiyun_api_token:
            caiyun_task = asyncio.create_task(self.caiyun.get_weather(coords))

        if caiyun_task is None:
            try:
                return await qweather_task
            except Exception as error:
                logger.error(f"Fusion: QWeather failed with {type(error).__name__}")
                return None

        qweather_result, caiyun_result = await asyncio.gather(
            qweather_task,
            caiyun_task,
            return_exceptions=True,
        )
        if isinstance(qweather_result, Exception):
            logger.error(f"Fusion: QWeather failed with {type(qweather_result).__name__}")
            qweather_data = None
        else:
            qweather_data = qweather_result

        if isinstance(caiyun_result, Exception):
            logger.error(f"Fusion: Caiyun failed with {type(caiyun_result).__name__}")
            caiyun_data = None
        else:
            caiyun_data = caiyun_result

        if qweather_data is None:
            if caiyun_data is not None:
                caiyun_data.location_name = self._geo_location_name(loc_info, location)
                caiyun_data.coords = coords
                logger.warning("Fusion: QWeather weather failed; returning native Caiyun fallback")
            return caiyun_data

        if caiyun_data is None:
            return qweather_data

        return self._merge_weather(qweather_data, caiyun_data, profile)
