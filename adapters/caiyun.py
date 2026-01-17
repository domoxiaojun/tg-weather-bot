import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List
from loguru import logger

from core.config import settings
from adapters.base import WeatherAdapter
from domain.models import (
    WeatherData, MinutelyPrecipitation, WarningAlert, 
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
    
    def _get_skycon_info(self, skycon: str) -> tuple[str, str]:
        return SKYCON_MAP.get(skycon, (skycon, "❓"))

    async def get_weather(self, location: str) -> Optional[WeatherData]:
        """
        Get Caiyun Data.
        Location MUST be 'lon,lat' format for Caiyun.
        """
        if "," not in location:
            logger.warning(f"Caiyun requires coordinates (lon,lat), got: {location}")
            return None
        
        clean_location = location.replace(" ", "")
        
        # Request full data: alert, daily(15d), hourly(48h)
        url = f"https://api.caiyunapp.com/v2.6/{self.token}/{clean_location}/weather.json"
        params = {
            "alert": "true",
            "dailysteps": "15",
            "hourlysteps": "48",
            "unit": "metric:v2" 
        }
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "ok":
                logger.warning(f"Caiyun API Error: {url} - {data.get('error')}")
                return None
            else:
                logger.info("Caiyun API Success")
            
            result = data["result"]
            realtime = result["realtime"]
            
            # --- 1. Realtime Data ---
            skycon = realtime["skycon"]
            text, icon = self._get_skycon_info(skycon)
            
            # --- 2. Minutely Data ---
            minutely_list = []
            minutely_data = result.get("minutely", {})
            probs = minutely_data.get("probability", [])
            precips = minutely_data.get("precipitation_2h", [])
            now_dt = datetime.now()
            
            for i, p in enumerate(precips):
                t = now_dt + timedelta(minutes=i)
                prob = probs[i] if i < len(probs) else 0.0
                minutely_list.append(MinutelyPrecipitation(time=t, precip=p, probability=prob))
                
            # --- 3. Alerts ---
            alerts_list = []
            if "alert" in result and "content" in result["alert"]:
                 for a in result["alert"]["content"]:
                     alerts_list.append(WarningAlert(
                         title=a["title"],
                         type=a["code"],
                         level=a["status"],
                         text=a["description"],
                         pub_time=datetime.fromtimestamp(a["pubtimestamp"]),
                         source="Caiyun"
                     ))

            # --- 4. Air Quality ---
            air_quality = None
            if "air_quality" in realtime:
                aq = realtime["air_quality"]
                aqi_val = int(aq.get("aqi", {}).get("chn", 0)) # Default to China standard
                desc = aq.get("description", {}).get("chn", "")
                pm25 = float(aq.get("pm25", 0))
                
                # Simple category mapping if description is empty
                category = desc
                if not category:
                    if aqi_val <= 50: category = "优"
                    elif aqi_val <= 100: category = "良"
                    elif aqi_val <= 150: category = "轻度污染"
                    elif aqi_val <= 200: category = "中度污染"
                    elif aqi_val <= 300: category = "重度污染"
                    else: category = "严重污染"

                air_quality = AirQuality(
                    aqi=aqi_val,
                    category=category,
                    primary="PM2.5" if pm25 > 75 else "", # Simple inference
                    pm2p5=pm25,
                    description=desc
                )

            # --- 5. Hourly Forecast ---
            hourly_list = []
            hourly_data = result.get("hourly", {})
            if "temperature" in hourly_data:
                temps = hourly_data["temperature"]
                skycons = hourly_data.get("skycon", [])
                winds = hourly_data.get("wind", [])
                precips_h = hourly_data.get("precipitation", [])
                
                for i in range(len(temps)):
                    # Updated Parsing for ISO string
                    try:
                        h_time = datetime.fromisoformat(temps[i]["datetime"])
                    except ValueError:
                         # Fallback for manual replacement if python version < 3.11 or diff format
                         h_time = datetime.fromisoformat(temps[i]["datetime"].replace("Z", "+00:00"))
                         
                    h_temp = temps[i]["value"]
                    h_skycon = skycons[i]["value"] if i < len(skycons) else "CLEAR_DAY"
                    h_text, h_icon = self._get_skycon_info(h_skycon)
                    
                    # Wind
                    w_dir = ""
                    w_speed = ""
                    if i < len(winds):
                        w_dir = str(winds[i].get("direction", ""))
                        w_speed = str(winds[i].get("speed", ""))
                        
                    # Precip
                    h_precip = 0.0
                    if i < len(precips_h):
                        h_precip = precips_h[i].get("value", 0.0)

                    hourly_list.append(HourlyForecast(
                        time=h_time,
                        temp=h_temp,
                        text=h_text,
                        icon=h_icon,
                        precip=h_precip,
                        wind_dir=w_dir,
                        wind_scale=w_speed, # Caiyun gives speed in km/h usually, distinct from scale
                    ))

            # --- 6. Daily Forecast ---
            daily_list = []
            daily_data = result.get("daily", {})
            if "temperature" in daily_data:
                temps = daily_data["temperature"]
                skycons = daily_data.get("skycon", [])
                astros = daily_data.get("astro", [])
                precips_d = daily_data.get("precipitation", [])
                humidities = daily_data.get("humidity", [])
                
                for i in range(len(temps)):
                    d_date_str = temps[i]["date"]
                    # Fix: Handle potential full ISO string in daily date
                    if "T" in d_date_str:
                         d_date = datetime.fromisoformat(d_date_str).replace(tzinfo=None)
                    else:
                         d_date = datetime.strptime(d_date_str, "%Y-%m-%d")
                    
                    t_max = temps[i]["max"]
                    t_min = temps[i]["min"]
                    
                    s_day = skycons[i]["value"] if i < len(skycons) else "CLEAR_DAY"
                    s_night = skycons[i]["value"] # Caiyun mostly gives one skycon per day in basic array, daily.skycon_08h_20h and 20h_32h are separate
                    
                    # Try to get day/night split if available
                    if "skycon_08h_20h" in daily_data:
                         s_day = daily_data["skycon_08h_20h"][i]["value"]
                    if "skycon_20h_32h" in daily_data:
                         s_night = daily_data["skycon_20h_32h"][i]["value"]
                    
                    txt_d, icon_d = self._get_skycon_info(s_day)
                    txt_n, icon_n = self._get_skycon_info(s_night)
                    
                    d_sunrise = None
                    d_sunset = None
                    if i < len(astros):
                        d_sunrise = astros[i].get("sunrise", {}).get("time")
                        d_sunset = astros[i].get("sunset", {}).get("time")
                        
                    d_precip = 0.0
                    if i < len(precips_d):
                        d_precip = precips_d[i].get("max", 0.0) or precips_d[i].get("avg", 0.0)

                    d_humidity = None
                    if i < len(humidities):
                        d_humidity = int(humidities[i].get("avg", 0) * 100) # Caiyun gives 0-1

                    daily_list.append(DailyForecast(
                        date=d_date,
                        temp_min=t_min,
                        temp_max=t_max,
                        text_day=txt_d,
                        icon_day=icon_d,
                        text_night=txt_n,
                        icon_night=icon_n,
                        precip=d_precip,
                        sunrise=d_sunrise,
                        sunset=d_sunset,
                        humidity=d_humidity
                    ))

            # --- 7. Life Indices ---
            indices_list = []
            life_index = realtime.get("life_index", {})
            
            # Map Caiyun keys to our common Types
            # 1: Sport, 2: Car, 3: Dress, 5: UV, 8: Comfort, 9: Flu
            index_map = {
                "ultraviolet": ("5", "紫外线"),
                "comfort": ("8", "舒适度"),
                "carWashing": ("2", "洗车"),
                "dressing": ("3", "穿衣"),
                "coldRisk": ("9", "感冒"), 
            }
            
            for key, (type_id, name) in index_map.items():
                if key in life_index:
                    val = life_index[key]
                    indices_list.append(LifeIndex(
                        type=type_id,
                        name=name,
                        category=val.get("desc", ""), # Caiyun desc is usually the category/level
                        text=val.get("desc", "")      # Caiyun doesn't have detailed text separate from desc sometimes
                    ))

            return WeatherData(
                source="caiyun",
                location_name="Current Location",
                coords=location,
                now_temp=realtime["temperature"],
                now_feels_like=realtime.get("apparent_temperature", realtime["temperature"]),
                now_text=text,
                now_icon=icon,
                now_wind_dir=str(realtime["wind"]["direction"]),
                now_wind_scale=str(realtime["wind"]["speed"]),
                now_humidity=int(realtime["humidity"] * 100),
                now_precip=realtime["precipitation"]["local"]["intensity"],
                summary=result.get("forecast_keypoint", ""),
                
                minutely=minutely_list,
                hourly=hourly_list,
                daily=daily_list,
                alerts=alerts_list,
                air_quality=air_quality,
                indices=indices_list,
                
                is_raining=(minutely_data.get("probability", [0])[0] > 0.3)
            )

        except Exception as e:
            logger.error(f"Caiyun API Request Failed: {e}", exc_info=True)
            return None
