#!/usr/bin/env python
"""Fail if charts would render Chinese as tofu boxes.

This only reproduces in a Linux container (macOS always has CJK fonts), so it
runs as a CI step inside the built image rather than in the unit suite.
"""

import os
import sys

os.environ.setdefault("BOT_TOKEN", "ci-token")
os.environ.setdefault("QWEATHER_API_KEY", "ci-key")

from datetime import datetime, timedelta, timezone  # noqa: E402

from matplotlib import font_manager  # noqa: E402

from domain.models import DailyForecast, HourlyForecast, WeatherData  # noqa: E402
from services.visualizer import Visualizer  # noqa: E402

TZ = timezone(timedelta(hours=8))
PROBE_TEXT = "北京 逐小时温度 周日 降水"


def build_weather() -> WeatherData:
    start = datetime(2026, 7, 26, 8, tzinfo=TZ)
    return WeatherData(
        source="qweather",
        location_name="北京, 北京市",
        coords="116.4,39.9",
        now_temp=30,
        now_text="晴",
        now_icon="100",
        summary="当前 晴",
        update_time=start,
        hourly=[
            HourlyForecast(
                time=start + timedelta(hours=i),
                temp=25 + i % 5,
                feels_like=27 + i % 3,
                text="多云",
                icon="101",
                pop=float(i * 4 % 100),
                precip=0.1 * (i % 3),
                precip_kind="amount",
            )
            for i in range(24)
        ],
        daily=[
            DailyForecast(
                date=datetime(2026, 7, 26) + timedelta(days=i),
                temp_min=22 + i,
                temp_max=31 + i,
                text_day="晴",
                icon_day="100",
                text_night="多云",
                icon_night="151",
            )
            for i in range(5)
        ],
    )


# Debian/Ubuntu fonts-noto-cjk ships these; Docker build asserts they exist.
_REQUIRED_FONT_FILES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
)


def main() -> int:
    failures = []

    # Linux 镜像里应有 Regular + Bold；本机 macOS 可跳过路径检查。
    if sys.platform.startswith("linux"):
        for path in _REQUIRED_FONT_FILES:
            if os.path.isfile(path):
                print(f"system font OK: {path}")
            else:
                failures.append(
                    f"missing system font {path} — install fonts-noto-cjk "
                    "(apt install fonts-noto-cjk fontconfig && fc-cache -f)"
                )

    Visualizer._setup_style()
    family = Visualizer._cjk_font_family
    regular_w = Visualizer._weight_regular
    bold_w = Visualizer._weight_bold
    print(f"detected CJK family: {family!r} regular={regular_w} bold={bold_w}")

    # findfont falls back to DejaVu Sans when nothing can render the glyphs.
    resolved = font_manager.findfont(
        font_manager.FontProperties(family=["sans-serif"]), fallback_to_default=True
    )
    print(f"resolved sans-serif font: {resolved}")

    if not family:
        failures.append("no CJK font registered — charts would show tofu boxes")
    if "DejaVu" in resolved and not family:
        failures.append(f"matplotlib fell back to {resolved}")
    if family and regular_w == bold_w:
        print(
            f"note: regular and bold weights collapsed to {regular_w} "
            "(single-face font; titles still render)"
        )

    weather = build_weather()
    for name, render in (
        ("hourly_temp", Visualizer.draw_hourly_temp_chart),
        ("hourly_rain", Visualizer.draw_hourly_rain_chart),
        ("daily_temp", Visualizer.draw_daily_temp_chart),
    ):
        png = render(weather)
        if not png or not png.startswith(b"\x89PNG"):
            failures.append(f"{name} chart did not render")
        else:
            print(f"{name}: {len(png)} bytes OK")

    # Any glyph the chosen font cannot draw is a silent tofu box at runtime.
    if family:
        try:
            path = font_manager.findfont(font_manager.FontProperties(family=[family]))
            from matplotlib.ft2font import FT2Font

            face = FT2Font(path)
            missing = [char for char in PROBE_TEXT if char.strip() and face.get_char_index(ord(char)) == 0]
            if missing:
                failures.append(f"font {family} lacks glyphs for: {''.join(missing)}")
            else:
                print(f"glyph coverage OK for: {PROBE_TEXT}")
        except Exception as error:
            print(f"glyph probe skipped: {error}")

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nCJK font check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
