import os
import httpx
import asyncio
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM
import io

ICON_DIR = "assets/icons"
BASE_URL = "https://raw.githubusercontent.com/qwd/Icons/main/icons"

# 常用图标 ID 列表 (QWeather)
ICONS = [
    # 晴/云
    "100", "101", "102", "103", "104", 
    "150", "151", "152", "153", "154",
    # 雨
    "300", "301", "302", "303", "304", "305", "306", "307", "308", "309", "310", "311", "312", "313",
    "314", "315", "316", "317", "318", "350", "351", "399",
    # 雪
    "400", "401", "402", "403", "404", "405", "406", "407", "408", "409", "410", "456", "457", "499",
    # 雾/霾
    "500", "501", "502", "503", "504", "507", "508", "509", "510", "511", "512", "513", "514", "515",
    # 风/其他
    "800", "801", "802", "803", "804", "805", "806", "807", "900", "901", "999"
]

async def download_and_convert_icon(client, code):
    png_path = os.path.join(ICON_DIR, f"{code}.png")
    
    # 强制重新下载（为了覆盖旧版PNG）
    # if os.path.exists(png_path):
    #     print(f"✅ {code} exists")
    #     return

    try:
        url = f"{BASE_URL}/{code}.svg"
        resp = await client.get(url, timeout=10)
        
        if resp.status_code == 200:
            # SVG to PNG
            svg_file = io.BytesIO(resp.content)
            drawing = svg2rlg(svg_file)
            
            # 这里默认转出来尺寸可能较小，ReportLab SVG默认缩放。
            # 但作为图标足够清晰。Matplotlib会再缩放。
            renderPM.drawToFile(drawing, png_path, fmt="PNG")
            print(f"🎨 {code} converted")
        else:
            print(f"❌ {code} not found on GitHub")
    except Exception as e:
        print(f"⚠️ {code} error: {e}")

async def main():
    if not os.path.exists(ICON_DIR):
        os.makedirs(ICON_DIR)
        
    print(f"Starting standardizing {len(ICONS)} icons from QWeather GitHub...")
    async with httpx.AsyncClient(verify=False) as client:
        # 限制并发以避免被 GitHub 限流
        sem = asyncio.Semaphore(10)
        
        async def safe_download(code):
            async with sem:
                await download_and_convert_icon(client, code)
                
        tasks = [safe_download(code) for code in ICONS]
        await asyncio.gather(*tasks)
    print("✨ All icons refined.")

if __name__ == "__main__":
    asyncio.run(main())
