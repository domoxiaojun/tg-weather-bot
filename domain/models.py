from datetime import datetime
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

# --- Enums & Literals ---
WeatherSource = Literal["qweather", "caiyun", "fusion"]

# --- Core Models ---

class MinutelyPrecipitation(BaseModel):
    """Minute-level precipitation data"""
    time: datetime
    precip: float = Field(..., description="Precipitation in mm")
    probability: float = Field(0.0, description="Probability of precipitation (0-1)")

class HourlyForecast(BaseModel):
    """Hourly weather forecast"""
    time: datetime
    temp: float
    text: str
    icon: str
    pop: Optional[float] = Field(None, description="Probability of Precipitation (%)")
    precip: float = 0.0
    wind_dir: str = ""
    wind_scale: str = ""
    humidity: Optional[int] = None
    pressure: Optional[float] = None
    cloud: Optional[int] = None
    dew: Optional[float] = None

class DailyForecast(BaseModel):
    """Daily weather forecast"""
    date: datetime
    temp_min: float
    temp_max: float
    text_day: str
    icon_day: str
    text_night: str
    icon_night: str
    precip: float = 0.0
    humidity: Optional[int] = None
    vis: Optional[float] = None # Visibility
    uv_index: Optional[str] = None
    sunrise: Optional[str] = None
    sunset: Optional[str] = None
    moon_phase: Optional[str] = None
    moon_rise: Optional[str] = None
    moon_set: Optional[str] = None
    wind_dir_day: Optional[str] = None
    wind_scale_day: Optional[str] = None
    wind_dir_night: Optional[str] = None
    wind_scale_night: Optional[str] = None
    aqi: Optional[str] = None

class AirQuality(BaseModel):
    aqi: int
    category: str  # e.g., "Good", "Moderate"
    primary: str = ""
    pm2p5: float = 0.0
    description: str = ""

class WarningAlert(BaseModel):
    """Weather Warning Alert"""
    title: str
    type: str  # e.g., "Rainstorm"
    level: str  # e.g., "Red", "Orange"
    text: str
    pub_time: datetime
    source: str = "Unknown"

class LifeIndex(BaseModel):
    """Life Suggestion Index"""
    type: str  # e.g., "1" (Sport)
    name: str  # e.g., "运动指数"
    category: str # e.g., "适宜"
    text: str = ""


# --- Unified Weather Data ---

class WeatherData(BaseModel):
    """
    Unified Weather Data Model.
    This is what the UI (Bot) will consume, regardless of the source.
    """
    source: WeatherSource = "qweather"
    update_time: datetime = Field(default_factory=datetime.now)
    
    # Location
    location_name: str
    coords: str # "lon,lat"
    
    # Realtime
    now_temp: float
    now_feels_like: float
    now_text: str
    now_icon: str
    now_wind_dir: str = ""
    now_wind_scale: str = ""
    now_humidity: int = 0
    now_precip: float = 0.0
    now_pressure: Optional[int] = None # hPa (New)
    now_vis: Optional[float] = None # km (New)
    
    # Key Summary (The "Headline")
    summary: str = "" 
    
    # Air Quality
    air_quality: Optional[AirQuality] = None
    
    # Forecasts
    minutely: List[MinutelyPrecipitation] = Field(default_factory=list)
    hourly: List[HourlyForecast] = Field(default_factory=list)
    daily: List[DailyForecast] = Field(default_factory=list)
    
    # Alerts
    alerts: List[WarningAlert] = Field(default_factory=list)
    
    # Indices
    indices: List[LifeIndex] = Field(default_factory=list)

    
    # Context
    is_raining: bool = False
    
    def get_rain_plot_data(self):
        """Helper to get x, y lists for plotting"""
        if not self.minutely:
            return None, None
        return [m.time for m in self.minutely], [m.precip for m in self.minutely]

    def get_hourly_temp_plot_data(self):
        """返回逐小时温度数据 (times, temps, icons)"""
        if not self.hourly:
            return [], [], []
        times = [h.time for h in self.hourly]
        temps = [h.temp for h in self.hourly]
        icons = [h.icon for h in self.hourly]
        return times, temps, icons

    def get_daily_temp_plot_data(self):
        """返回逐日最高/最低温度数据 (dates, max_temps, min_temps)"""
        if not self.daily:
            return [], [], []
        dates = [d.date for d in self.daily]
        max_temps = [d.temp_max for d in self.daily]
        min_temps = [d.temp_min for d in self.daily]
        return dates, max_temps, min_temps

    def get_hourly_rain_plot_data(self):
        """返回逐小时降水概率和降水量 (times, pops, precips)"""
        if not self.hourly:
            return [], [], []
        times = [h.time for h in self.hourly]
        pops = [h.pop if h.pop is not None else 0.0 for h in self.hourly]
        precips = [h.precip for h in self.hourly]
        return times, pops, precips
