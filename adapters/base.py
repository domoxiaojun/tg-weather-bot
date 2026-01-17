from abc import ABC, abstractmethod
from typing import Optional
from domain.models import WeatherData

class WeatherAdapter(ABC):
    """Base interface for Weather Providers"""
    
    @abstractmethod
    async def get_weather(self, location: str) -> Optional[WeatherData]:
        """
        Fetch weather data for a location.
        :param location: Can be City Name or "lon,lat"
        """
        pass
