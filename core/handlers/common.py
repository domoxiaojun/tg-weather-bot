import datetime
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.fusion import WeatherFusionService
    from services.llm import LLMService


@dataclass(slots=True)
class BotDependencies:
    weather_service: "WeatherFusionService"
    llm_service: "LLMService"


DAILY_WORDS = {"daily", "day", "days", "forecast", "预报", "未来"}
HOURLY_WORDS = {"hourly", "hour", "hours", "逐小时", "小时"}
CHART_TYPES = {"daily", "hourly", "rain", "temp"}
RELATIVE_DAY_WORDS = {
    "今天": 0,
    "今日": 0,
    "today": 0,
    "明天": 1,
    "明日": 1,
    "tomorrow": 1,
    "后天": 2,
    "大后天": 3,
}


def join_location_args(args: list[str]) -> str:
    return " ".join(args).strip()


def parse_chart_request(args: list[str]) -> tuple[str, str]:
    """Parse a multi-word chart location and optional trailing chart type."""
    if len(args) > 1 and args[-1].strip().lower() in CHART_TYPES:
        return join_location_args(args[:-1]), args[-1].strip().lower()
    return join_location_args(args), "temp"


def parse_query_param(param: str) -> tuple[str, int, Optional[int]]:
    """
    Parse a weather query suffix.

    Returns: (view_type, start_day, limit)
    """
    param = param.strip().lower()
    today = datetime.date.today()

    if not param:
        return "default", 0, None

    if param in ["降水", "rain", "雨"]:
        return "rain", 0, None
    if param in ["指数", "index", "indices", "life"]:
        return "indices", 0, None
    if param in RELATIVE_DAY_WORDS:
        return "daily", RELATIVE_DAY_WORDS[param], 1
    if param in DAILY_WORDS:
        return "daily", 0, 7
    if param in HOURLY_WORDS:
        return "hourly", 0, 24

    if param.isdigit():
        return "daily", 0, min(int(param), 15)

    if param.endswith("h") and param[:-1].isdigit():
        hours = int(param[:-1])
        return "hourly", 0, min(hours, 72)

    if "-" in param:
        parts = param.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            p1, p2 = int(parts[0]), int(parts[1])

            try:
                target_date = datetime.date(today.year, p1, p2)
                if target_date < today:
                    target_date = target_date.replace(year=today.year + 1)

                diff = (target_date - today).days
                if 0 <= diff <= 15:
                    return "daily", diff, 1
            except ValueError:
                pass

            start_idx = p1 - 1
            end_idx = p2 - 1
            if 0 <= start_idx < end_idx <= 15:
                return "daily", start_idx, end_idx - start_idx + 1

    return "default", 0, None


def parse_location_and_view(args: list[str]) -> tuple[Optional[str], str, int, Optional[int]]:
    """Parse command or inline text parts into location and view arguments."""
    if not args:
        return None, "default", 0, None

    if len(args) >= 3:
        prev = args[-2].strip().lower()
        tail = args[-1].strip().lower()
        if prev in DAILY_WORDS and tail.isdigit():
            return " ".join(args[:-2]), "daily", 0, min(int(tail), 15)
        if prev in HOURLY_WORDS and tail.isdigit():
            return " ".join(args[:-2]), "hourly", 0, min(int(tail), 72)

    view_type, start_day, limit = parse_query_param(args[-1])
    if view_type != "default" and len(args) >= 2:
        return " ".join(args[:-1]), view_type, start_day, limit

    return " ".join(args), "default", 0, None
