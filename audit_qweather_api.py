import asyncio
import os
import sys
import httpx
from pprint import pprint

# Manual Import path fix
sys.path.append(os.getcwd())

from core.config import settings

async def audit_qweather():
    key = settings.qweather_api_key
    # Shanghai Loc ID (from previous logs or known)
    # Actually QWeather needs a Location ID usually, or lon,lat.
    # Let's use coordinates for parity: 121.47, 31.23 (Shanghai)
    location = "121.47,31.23"
    
    headers = {"X-QW-Api-Key": key}
    base_url = "https://api.qweather.com/v7"
    if settings.qweather_api_host:
         base_url = settings.qweather_api_host.rstrip('/')

    endpoints = {
        "Realtime": "/weather/now",
        "24h Forecast": "/weather/24h",
        "7d Forecast": "/weather/7d",
        "Indices (1d)": "/indices/1d",
        "Air Quality": "/air/now",
        "Minutely Rain": "/minutely/5m",
        "Warning": "/warning/now",
        "Sun/Moon": "/astronomy/sunmoon"
    }
    
    print(f"🌀 Auditing QWeather API (Key: {key[:4]}***)...")
    
    async with httpx.AsyncClient() as client:
        for name, path in endpoints.items():
            url = f"{base_url}{path}"
            params = {"location": location, "key": key}
            # Indices needs 'type' param, usually '0' for all? Or specific.
            if "indices" in path:
                params["type"] = "1,2,3,5,8,9" # Sport, Car, Dress, UV, Comfort, Flu
            
            try:
                resp = await client.get(url, params=params)
                data = resp.json()
                
                print(f"\n--- {name} ---")
                if data.get("code") == "200":
                    pprint(data, depth=2, compact=True)
                else:
                    print(f"❌ Error: {data.get('code')}")
            except Exception as e:
                print(f"❌ Exception for {name}: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
         sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(audit_qweather())
