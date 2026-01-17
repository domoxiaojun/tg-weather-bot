import httpx
from datetime import datetime
from typing import Optional, Dict
from loguru import logger

from core.config import settings
from adapters.base import WeatherAdapter
from domain.models import (
    WeatherData, DailyForecast, HourlyForecast, AirQuality, WarningAlert, LifeIndex
)

class QWeatherAdapter(WeatherAdapter):
    """Adapter for HeFeng Weather (QWeather)"""
    
    GEO_URL = "https://geoapi.qweather.com/v2/"

    

    def __init__(self):
        self.api_key = settings.qweather_api_key
        self.base_url = settings.qweather_api_host
        self.client = httpx.AsyncClient(timeout=10.0, http2=True)

    async def _request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """Internal helper for API requests"""
        try:
            # Dynamic Base URL logic like in reference
            current_base_url = self.base_url
            if endpoint.startswith("geo/"): # If looking for location lookup, it uses geoapi
                # Note: QWeather GEO API is always on geoapi.qweather.com
                current_base_url = "https://geoapi.qweather.com/v2/"
                endpoint = endpoint.replace("geo/", "") # Remove prefix
            
            url = f"{current_base_url}{endpoint}"
            
            params["key"] = self.api_key
            params["lang"] = "zh"
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == "200":
                logger.debug(f"QWeather API Success: {endpoint}")
                return data
            else:
                logger.warning(f"QWeather API Error: {url} - {data.get('code')}")
                return None
        except Exception as e:
            logger.error(f"QWeather API Request Failed: {e}")
            return None


    async def get_geo_location(self, location: str) -> Optional[Dict]:
        """Resolve location string to Location ID and coords (with permanent cache)"""
        from utils.cache import cache
        
        cache_key = f"geo:{location.lower()}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"地理位置缓存命中: {location}")
            return cached
        
        url = "geo/city/lookup"
        data = await self._request(url, {"location": location})
        if data and data.get("location"):
            loc_data = data["location"][0]
            cache.set(cache_key, loc_data, ttl=None)  # 永久缓存
            return loc_data
            
        # Retry logic: "Shanghai, Shanghai" -> "Shanghai"
        if "," in location:
            simple_loc = location.split(",")[0].strip()
            if simple_loc:
                logger.debug(f"Retrying geo lookup with simplified name: '{simple_loc}'")
                data = await self._request(url, {"location": simple_loc})
                if data and data.get("location"):
                    loc_data = data["location"][0]
                    cache.set(cache_key, loc_data, ttl=None)
                    return loc_data

        return None

    async def get_weather(self, location: str) -> Optional[WeatherData]:
        from utils.cache import cache
        
        # 1. Check Cache (1 hour TTL)
        cache_key = f"qweather:{location}"
        cached = cache.get(cache_key)
        if cached:
            logger.debug(f"Weather cache hit: {location}")
            return WeatherData(**cached)
        
        # 2. Resolve Location
        loc_info = await self.get_geo_location(location)
        if not loc_info:
            logger.warning(f"Could not resolve location: {location}")
            return None
        
        loc_id = loc_info["id"]
        loc_name = f"{loc_info['name']}, {loc_info['adm1']}"
        coords = f"{loc_info['lon']},{loc_info['lat']}"

        # 2. Fetch Data in Parallel (could be improved with asyncio.gather)
        now_data = await self._request("weather/now", {"location": loc_id})
        daily_data = await self._request("weather/7d", {"location": loc_id})
        hourly_data = await self._request("weather/24h", {"location": loc_id})
        air_data = await self._request("air/now", {"location": loc_id})
        warning_data = await self._request("warning/now", {"location": loc_id})
        indices_data = await self._request("indices/1d", {"location": loc_id, "type": "1,2,3,5,9"}) # Sport, CarWash, Dress, UV, Flu



        if not now_data:
            return None

        # 3. Map to Unified Model
        now = now_data["now"]
        
        # Build Forecast Lists using Pydantic Models
        daily_list = []
        if daily_data:
            for d in daily_data.get("daily", []):
                daily_list.append(DailyForecast(
                    date=datetime.strptime(d["fxDate"], "%Y-%m-%d"),
                    temp_min=float(d["tempMin"]),
                    temp_max=float(d["tempMax"]),
                    text_day=d["textDay"],
                    icon_day=d["iconDay"],
                    text_night=d["textNight"],
                    icon_night=d["iconNight"],
                    precip=float(d["precip"]),
                    sunrise=d.get("sunrise"),
                    sunset=d.get("sunset"),
                    moon_phase=d.get("moonPhase"),
                    humidity=int(d.get("humidity", 0)),
                    vis=float(d.get("vis", 0)),
                    uv_index=d.get("uvIndex", "N/A"),
                    wind_dir_day=d.get("windDirDay"),
                    wind_scale_day=d.get("windScaleDay"),
                    wind_dir_night=d.get("windDirNight"),
                    wind_scale_night=d.get("windScaleNight")
                ))

        hourly_list = []
        if hourly_data:
            for h in hourly_data.get("hourly", []):
                hourly_list.append(HourlyForecast(
                    time=datetime.fromisoformat(h["fxTime"].replace("Z", "+00:00")),
                    temp=float(h["temp"]),
                    text=h["text"],
                    icon=h["icon"],
                    pop=float(h.get("pop", 0)),
                    precip=float(h.get("precip", 0)),
                    wind_dir=h.get("windDir"),
                    wind_scale=h.get("windScale"),
                    humidity=int(h.get("humidity", 0))
                ))
        
        # 补全当前小时 (如果 API 返回的是下一小时起)
        if hourly_list and now_data:
            try:
                # 获取当前观测时间
                obs_time_str = now_data["now"]["obsTime"].replace("Z", "+00:00")
                current_obs_time = datetime.fromisoformat(obs_time_str)
                # 向下取整到整点 (18:39 -> 18:00)
                current_hour_time = current_obs_time.replace(minute=0, second=0, microsecond=0)
                
                first_hourly_time = hourly_list[0].time
                
                # 如果第一个预报时间 晚于 当前整点时间 (说明当前小时缺失)
                if first_hourly_time > current_hour_time:
                    now = now_data["now"]
                    # 估算当前降水概率 POP
                    # 如果当前降水量 > 0，则 POP=100；否则沿用下一个小时的 POP (平滑过渡)
                    current_precip = float(now.get("precip", 0))
                    next_pop = hourly_list[0].pop if hourly_list[0].pop is not None else 0
                    current_pop = 100.0 if current_precip > 0 else next_pop
                    
                    hourly_list.insert(0, HourlyForecast(
                        time=current_hour_time,
                        temp=float(now["temp"]),
                        text=now["text"],
                        icon=now["icon"],
                        pop=current_pop,
                        precip=current_precip,
                        wind_dir=now.get("windDir", ""),
                        wind_scale=now.get("windScale", ""),
                        humidity=int(now.get("humidity", 0))
                    ))
            except Exception as e:
                logger.warning(f"Failed to prepend current hour data: {e}")
        
        # Alerts
        alerts_list = []
        if warning_data and "warning" in warning_data:
            for w in warning_data["warning"]:
                alerts_list.append(WarningAlert(
                    title=w["title"],
                    type=w["typeName"],
                    level=w["level"],
                    text=w["text"],
                    pub_time=datetime.fromisoformat(w["pubTime"].replace("Z", "+00:00")),
                    source="QWeather"
                ))

        # Air Quality
        aqi_obj = None
        if air_data and "now" in air_data:
            a = air_data["now"]
            aqi_obj = AirQuality(
                aqi=int(a["aqi"]),
                category=a["category"],
                primary=a.get("primary", ""),
                pm2p5=float(a.get("pm2p5", 0))
            )

        return WeatherData(
            source="qweather",
            location_name=loc_name,
            coords=coords,
            now_temp=float(now["temp"]),
            now_feels_like=float(now["feelsLike"]),
            now_text=now["text"],
            now_icon=now["icon"],
            now_wind_dir=now.get("windDir"),
            now_wind_scale=now.get("windScale"),
            now_humidity=int(now.get("humidity", 0)),
            now_precip=float(now.get("precip", 0)),
            now_pressure=int(now.get("pressure", 0)),
            now_vis=float(now.get("vis", 0)),
            summary=f"当前 {now['text']}，温度 {now['temp']}°C。",
            daily=daily_list,
            hourly=hourly_list,
            air_quality=aqi_obj,
            alerts=alerts_list,
            indices=[
                LifeIndex(
                    type=i["type"], 
                    name=i["name"], 
                    category=i["category"], 
                    text=i.get("text", "")
                ) for i in indices_data.get("daily", [])
            ] if indices_data else []
        )
        
        # Cache weather data for 1 hour (3600s)
        cache.set(cache_key, weather_obj.dict(), ttl=3600)
        logger.debug(f"Cached weather for {location} (TTL=1h)")
        
        return weather_obj
