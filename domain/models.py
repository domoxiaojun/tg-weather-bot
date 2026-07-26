from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

# --- Enums & Literals ---
WeatherSource = Literal["qweather", "caiyun", "fusion"]
WeatherProvider = Literal["qweather", "caiyun"]
FeelsLikeSource = WeatherProvider
PrecipitationKind = Literal["amount", "intensity"]


_WARNING_LEVEL_LABELS = {
    "white": "白色",
    "blue": "蓝色",
    "yellow": "黄色",
    "orange": "橙色",
    "red": "红色",
    "extreme": "极端",
    "severe": "严重",
    "moderate": "中等",
    "minor": "轻微",
}
_UNCLASSIFIED_WARNING_LEVELS = {
    "",
    "default",
    "gray",
    "grey",
    "none",
    "unknown",
    "未知",
    "未分级",
}


def normalize_warning_level(value: Any) -> str:
    """Convert provider alert enums to a stable Chinese display label."""
    if value is None:
        return ""
    text = str(value).strip()
    key = text.casefold()
    if key in _UNCLASSIFIED_WARNING_LEVELS:
        return ""
    return _WARNING_LEVEL_LABELS.get(key, text)

# --- Core Models ---

class MinutelyPrecipitation(BaseModel):
    """Minute-level precipitation data"""
    time: datetime
    precip: float = Field(..., description="Precipitation in mm")
    probability: Optional[float] = Field(None, description="Probability of precipitation (0-1)")
    precip_type: Optional[str] = Field(None, description="Precipitation type, e.g. rain or snow")
    precip_kind: PrecipitationKind = "amount"
    interval_minutes: Optional[int] = 5
    source: WeatherProvider = "qweather"

class HourlyForecast(BaseModel):
    """Hourly weather forecast"""
    time: datetime
    temp: float
    feels_like: Optional[float] = Field(None, description="Feels-like temperature in Celsius")
    feels_like_estimated: bool = Field(
        False,
        description="Legacy safety marker; estimated values must not be displayed",
    )
    feels_like_source: Optional[FeelsLikeSource] = None
    text: str
    icon: str
    pop: Optional[float] = Field(None, description="Probability of Precipitation (%)")
    precip: Optional[float] = None
    precip_kind: Optional[PrecipitationKind] = None
    precip_source: Optional[WeatherProvider] = None
    wind_dir: str = ""
    wind_scale: str = ""
    wind_speed: Optional[float] = Field(None, description="Wind speed in km/h")
    wind_direction_degrees: Optional[float] = None
    humidity: Optional[int] = None
    pressure: Optional[float] = None
    cloud: Optional[int] = None
    dew: Optional[float] = None
    uv_index: Optional[float] = None
    visibility: Optional[float] = None
    radiation: Optional[float] = Field(None, description="Downward shortwave radiation in W/m²")
    aqi: Optional[int] = None
    pm2p5: Optional[float] = None
    field_sources: Dict[str, WeatherProvider] = Field(default_factory=dict)

    @model_validator(mode="after")
    def discard_estimated_feels_like(self):
        """Never retain locally estimated feels-like values, including old cache data."""
        if self.feels_like_estimated:
            self.feels_like = None
            self.feels_like_estimated = False
            self.feels_like_source = None
        return self

class DailyForecast(BaseModel):
    """Daily weather forecast"""
    date: datetime
    temp_min: float
    temp_max: float
    text_day: str
    icon_day: str
    text_night: str
    icon_night: str
    temp_avg: Optional[float] = None
    precip: Optional[float] = None
    precip_kind: Optional[PrecipitationKind] = None
    precip_source: Optional[WeatherProvider] = None
    precip_probability: Optional[float] = None
    precip_day: Optional[float] = None
    precip_day_probability: Optional[float] = None
    precip_night: Optional[float] = None
    precip_night_probability: Optional[float] = None
    humidity: Optional[int] = None
    vis: Optional[float] = None # Visibility
    uv_index: Optional[str] = None
    pressure: Optional[float] = None
    cloud: Optional[int] = None
    radiation: Optional[float] = None
    sunrise: Optional[str] = None
    sunset: Optional[str] = None
    moon_phase: Optional[str] = None
    moon_rise: Optional[str] = None
    moon_set: Optional[str] = None
    wind_dir_day: Optional[str] = None
    wind_scale_day: Optional[str] = None
    wind_speed_day: Optional[float] = None
    wind_direction_day_degrees: Optional[float] = None
    wind_dir_night: Optional[str] = None
    wind_scale_night: Optional[str] = None
    wind_speed_night: Optional[float] = None
    wind_direction_night_degrees: Optional[float] = None
    aqi: Optional[int] = None
    pm2p5: Optional[float] = None
    field_sources: Dict[str, WeatherProvider] = Field(default_factory=dict)

class AirQuality(BaseModel):
    aqi: Optional[int] = None
    category: str  # e.g., "Good", "Moderate"
    primary: str = ""
    pm2p5: Optional[float] = None
    pm10: Optional[float] = None
    o3: Optional[float] = None
    so2: Optional[float] = None
    no2: Optional[float] = None
    co: Optional[float] = None
    description: str = ""
    source: WeatherProvider = "qweather"
    field_sources: Dict[str, WeatherProvider] = Field(default_factory=dict)

class WarningAlert(BaseModel):
    """Weather Warning Alert"""
    title: str
    type: str  # e.g., "Rainstorm"
    level: str  # e.g., "Red", "Orange"
    text: str
    pub_time: datetime
    source: str = "Unknown"
    status: Optional[str] = None
    alert_id: Optional[str] = None
    expire_time: Optional[datetime] = None

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value: Any) -> str:
        return normalize_warning_level(value)

class LifeIndex(BaseModel):
    """Life Suggestion Index"""
    type: str  # e.g., "1" (Sport)
    name: str  # e.g., "运动指数"
    category: str # e.g., "适宜"
    text: str = ""
    date: Optional[datetime] = None
    value: Optional[str] = None
    source: WeatherProvider = "qweather"


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
    now_feels_like: Optional[float] = None
    now_text: str
    now_icon: str
    now_wind_dir: str = ""
    now_wind_scale: str = ""
    now_wind_speed: Optional[float] = None
    now_wind_direction_degrees: Optional[float] = None
    now_humidity: Optional[int] = None
    now_precip: Optional[float] = None
    now_precip_kind: Optional[PrecipitationKind] = None
    now_precip_source: Optional[WeatherProvider] = None
    now_pressure: Optional[float] = None # hPa
    now_vis: Optional[float] = None # km (New)
    now_cloud: Optional[int] = None
    now_radiation: Optional[float] = None
    
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

    field_sources: Dict[str, WeatherProvider] = Field(default_factory=dict)
    provider_summaries: Dict[WeatherProvider, str] = Field(default_factory=dict)
    timezone: Optional[str] = None
    attributions: List[str] = Field(default_factory=list)

    
    # Context
    is_raining: bool = False

    @property
    def local_update_date(self) -> date:
        """Local calendar date carried by the provider update timestamp."""
        return self.update_time.date()

    def get_daily_forecasts(
        self,
        *,
        start_day: int = 0,
        limit: Optional[int] = None,
    ) -> List[DailyForecast]:
        """Return non-stale daily forecasts relative to the provider update date."""
        target_date = self.local_update_date + timedelta(days=max(0, start_day))
        forecasts = sorted(
            (day for day in self.daily if day.date.date() >= target_date),
            key=lambda day: day.date,
        )
        if limit is None:
            return forecasts
        return forecasts[:max(0, limit)]

    def get_current_daily_forecast(self) -> Optional[DailyForecast]:
        """Select today's forecast, or the first future forecast when today is absent."""
        forecasts = self.get_daily_forecasts(limit=1)
        return forecasts[0] if forecasts else None
    
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

    def get_hourly_feels_like_plot_data(self):
        """返回逐小时体感温度和旧估算标记 (times, feels_like, estimated_flags)"""
        if not self.hourly:
            return [], [], []
        times = [h.time for h in self.hourly]
        feels_like = [h.feels_like for h in self.hourly]
        estimated_flags = [h.feels_like_estimated for h in self.hourly]
        return times, feels_like, estimated_flags

    def get_daily_temp_plot_data(self):
        """返回逐日最高/最低温度数据 (dates, max_temps, min_temps)"""
        forecasts = self.get_daily_forecasts()
        if not forecasts:
            return [], [], []
        dates = [d.date for d in forecasts]
        max_temps = [d.temp_max for d in forecasts]
        min_temps = [d.temp_min for d in forecasts]
        return dates, max_temps, min_temps

    def get_hourly_rain_plot_data(self):
        """返回逐小时降水概率和降水量 (times, pops, precips)"""
        if not self.hourly:
            return [], [], []
        times = [h.time for h in self.hourly]
        pops = [h.pop if h.pop is not None else float("nan") for h in self.hourly]
        precips = [h.precip if h.precip is not None else float("nan") for h in self.hourly]
        return times, pops, precips
