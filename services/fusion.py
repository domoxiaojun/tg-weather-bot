import asyncio
from typing import Optional
from loguru import logger

from adapters.qweather import QWeatherAdapter
from adapters.caiyun import CaiyunAdapter
from domain.models import WeatherData

class WeatherFusionService:
    """
    Intelligent Data Router.
    Merges QWeather's comprehensive data with Caiyun's minute-level precision.
    """
    
    def __init__(self):
        self.qweather = QWeatherAdapter()
        self.caiyun = CaiyunAdapter()
        
    async def get_fused_weather(self, location: str) -> Optional[WeatherData]:
        """
        Get the best possible weather data.
        Strategy:
        1. Fetch QWeather (Master) first to resolve location coordinates.
        2. If successful, use those coords to fetch Caiyun (Master for Rain) in parallel.
        3. Merge:
           - Base: QWeather
           - Enrich: Minutely, Alerts, Air Quality (if missing), Indices (Unique)
           - Summary: Intelligent combo
        """
        # 1. Fetch Master (QWeather) to get specific coords
        logger.info(f"Fusion: Fetching QWeather for '{location}'...")
        qw_data = await self.qweather.get_weather(location)
        
        # Scenario: QWeather Geocoding failed or API down
        if not qw_data:
            # If input is coordinates, we can try Caiyun directly as fallback
            if "," in location and any(c.isdigit() for c in location):
                 logger.info(f"Fusion: QWeather failed, trying Caiyun fallback for coords '{location}'...")
                 try:
                     return await self.caiyun.get_weather(location)
                 except Exception:
                     pass
            
            logger.warning(f"Fusion: QWeather returned NO DATA for '{location}'.")
            return None
            
        logger.info(f"Fusion: QWeather success. Coordinates: {qw_data.coords}")

        # 2. Fetch Slave (Caiyun) using resolved coords
        try:
            logger.info(f"Fusion: Fetching Caiyun for '{qw_data.coords}'...")
            cy_data = await self.caiyun.get_weather(qw_data.coords)
        except Exception as e:
            logger.error(f"Fusion: Caiyun fetch failed: {e}")
            cy_data = None
            
        if not cy_data:
            logger.info("Fusion: Using QWeather data only (Caiyun unavailable).")
            return qw_data
            
        logger.info("Fusion: Caiyun success. Merging data...")
        
        # 3. Fusion Logic
        
        # A. Minutely Data: Caiyun is King
        qw_data.minutely = cy_data.minutely
        
        # B. Alerts: Merge and Dedup
        existing_titles = {a.title for a in qw_data.alerts}
        for alert in cy_data.alerts:
            if alert.title not in existing_titles:
                qw_data.alerts.append(alert)
        
        # C. Air Quality: Fill if missing
        if not qw_data.air_quality and cy_data.air_quality:
            qw_data.air_quality = cy_data.air_quality
            
        # D. Forecasts Fallback
        # If QWeather hourly/daily is empty (api issue), use Caiyun
        if not qw_data.hourly and cy_data.hourly:
            qw_data.hourly = cy_data.hourly
        if not qw_data.daily and cy_data.daily:
            qw_data.daily = cy_data.daily
            
        # E. Summary & Decision Making
        base_summary = qw_data.summary
        
        # Combine summaries intelligently
        combined_summary = base_summary
        if cy_data.summary:
            # If raining, emphasize Caiyun
            if cy_data.is_raining:
                 combined_summary = f"🌧️ {cy_data.summary}\n(QWeather: {base_summary})"
            else:
                 # Standard concat
                 combined_summary = f"{base_summary}\n🤖 彩云: {cy_data.summary}"
                 
        qw_data.summary = combined_summary
        
        # F. Source tagging
        qw_data.source = "和风天气 & 彩云天气"
        
        logger.info("Fusion: Data merge complete.")
        return qw_data
