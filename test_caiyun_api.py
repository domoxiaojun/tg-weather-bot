import asyncio
import os
import sys
import httpx
from datetime import datetime
from pprint import pprint

# Manual Import path fix
sys.path.append(os.getcwd())

from core.config import settings

async def test_caiyun():
    token = settings.caiyun_api_token
    # Beijing Coordinates
    location = "116.4074,39.9042" 
    
    url = f"https://api.caiyunapp.com/v2.6/{token}/{location}/weather.json"
    params = {
        "alert": "true",
        "dailysteps": "15",
        "hourlysteps": "48",
        "unit": "metric:v2" 
    }
    
    print(f"Testing URL: https://api.caiyunapp.com/v2.6/MASKED_TOKEN/{location}/weather.json")
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            
            if data["status"] != "ok":
                print(f"❌ API Status Error: {data['status']}")
                pprint(data)
                return

            result = data["result"]
            print("✅ API Connection Success!")
            
            print("\n--- 1. Realtime ---")
            rt = result["realtime"]
            print(f"Location: {location}")
            print(f"Temp: {rt.get('temperature')}")
            print(f"Skycon: {rt.get('skycon')}")
            aq = rt.get('air_quality', {})
            print(f"AQI: {aq.get('aqi')}")
            print(f"Description: {aq.get('description')}")
            print(f"Life Index Example: {rt.get('life_index', {}).get('comfort')}")
            
            print("\n--- 2. Minutely ---")
            minutely = result.get("minutely", {})
            print(f"Summary: {result.get('forecast_keypoint')}")
            print(f"Desc: {minutely.get('description')}")
            
            print("\n--- 3. Hourly (First 2) ---")
            hourly = result.get("hourly", {})
            temps = hourly.get("temperature", [])
            for i in range(min(2, len(temps))):
                print(f"Hour {i}: {temps[i]}")

            print("\n--- 4. Daily (First 2) ---")
            daily = result.get("daily", {})
            temps_d = daily.get("temperature", [])
            for i in range(min(2, len(temps_d))):
                 print(f"Day {i}: {temps_d[i]}")
                
            print("\n--- 5. Alerts ---")
            alert = result.get("alert", {})
            content = alert.get("content", [])
            print(f"Alert count: {len(content)}")
            if content:
                pprint(content[0])
            else:
                print("No active alerts.")

        except Exception as e:
            print(f"❌ Exception: {e}")
            if hasattr(e, "response"):
                print(f"Response: {e.response.text}")

if __name__ == "__main__":
    if sys.platform == 'win32':
         # Fix windows encoding for print
         sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(test_caiyun())
