import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.handlers.common import BotDependencies
from core.handlers.weather import WeatherHandlers
from domain.models import HourlyForecast, WeatherData
from utils.formatter import format_hourly_weather, get_weather_keyboard


def make_weather() -> WeatherData:
    tz = timezone(timedelta(hours=8))
    return WeatherData(
        source="qweather",
        location_name="北京, 北京市",
        coords="116.4,39.9",
        now_temp=30,
        now_text="晴",
        now_icon="100",
        summary="s",
        update_time=datetime(2026, 7, 26, 16, tzinfo=tz),
    )


class KeyboardViewTests(unittest.TestCase):
    def test_refresh_carries_current_view(self):
        keyboard = get_weather_keyboard("北京", coords="116.4,39.9", view_type="hourly")
        refresh = keyboard.inline_keyboard[0][0]
        self.assertEqual(refresh.callback_data, "refresh|北京|hourly|0|24")

    def test_view_row_excludes_current_view(self):
        keyboard = get_weather_keyboard("北京", coords="116.4,39.9", view_type="daily")
        view_callbacks = [button.callback_data for button in keyboard.inline_keyboard[1]]
        self.assertEqual(len(view_callbacks), 3)
        self.assertNotIn("view|北京|daily|0|7", view_callbacks)
        self.assertIn("view|北京|hourly|0|24", view_callbacks)

    def test_long_names_stay_under_callback_limit_with_view_suffix(self):
        long_name = "新疆维吾尔自治区某某某某很长很长的地名测试"
        keyboard = get_weather_keyboard(long_name, coords="86.15,41.77", view_type="indices")
        checked = 0
        for row in keyboard.inline_keyboard:
            for button in row:
                if button.callback_data is None:
                    continue  # share button carries switch_inline_query instead
                checked += 1
                self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64, button.callback_data)
        self.assertGreater(checked, 0)

    def test_semantic_actions_are_colour_coded(self):
        keyboard = get_weather_keyboard("北京", coords="116.4,39.9")
        buttons = {b.text: b for row in keyboard.inline_keyboard for b in row}
        self.assertEqual(buttons["🔔 降雨提醒"].to_dict().get("style"), "success")
        self.assertEqual(buttons["🤖 AI日报"].to_dict().get("style"), "primary")
        # Neutral navigation stays uncoloured — colouring everything is noise.
        self.assertIsNone(buttons["🔄 刷新"].to_dict().get("style"))

    def test_share_button_uses_switch_inline_query(self):
        keyboard = get_weather_keyboard("北京", coords="116.4,39.9")
        share = [b for row in keyboard.inline_keyboard for b in row if b.text == "📤 分享"]
        self.assertEqual(len(share), 1)
        self.assertEqual(share[0].switch_inline_query, "北京")

    def test_unsubscribe_buttons_are_danger_styled(self):
        from core.handlers.subscriptions import render_subscription_list

        _text, keyboard = render_subscription_list({"subs": ["北京"]}, "rain")
        buttons = [b for row in keyboard.inline_keyboard for b in row]
        unsub = [b for b in buttons if (b.callback_data or "").startswith("unsub|")]
        self.assertEqual(len(unsub), 1)
        self.assertEqual(unsub[0].to_dict().get("style"), "danger")


class HourlyCompactFormatTests(unittest.TestCase):
    def test_two_lines_per_hour_and_date_divider(self):
        tz = timezone(timedelta(hours=8))
        hours = [
            HourlyForecast(
                time=datetime(2026, 7, 26, 23, tzinfo=tz) + timedelta(hours=i),
                temp=28,
                text="晴",
                icon="100",
                pop=10,
                precip=0.0,
                precip_kind="amount",
                humidity=40,
            )
            for i in range(3)
        ]
        rendered = format_hourly_weather(hours)
        lines = rendered.split("\n")
        divider_lines = [line for line in lines if line.startswith("——")]
        self.assertEqual(len(divider_lines), 1)
        self.assertIn("周一", divider_lines[0])
        # 3 hours * 2 lines + 1 divider
        self.assertEqual(len(lines), 7)
        self.assertIn("降概 10% / 降水 0mm", rendered)


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return None

    async def send_chat_action(self, **kwargs):
        return None


class FakeQWeather:
    async def get_geo_location(self, raw):
        return {"name": "北京", "adm1": "北京市", "lon": 116.4, "lat": 39.9}

    async def get_geo_candidates(self, raw, limit=4):
        return [{"name": "北京", "adm1": "北京市", "lon": 116.4, "lat": 39.9}]


class FakeWeatherService:
    def __init__(self):
        self.calls = []
        self.qweather = FakeQWeather()

    async def get_fused_weather(self, location, *, profile, refresh_qweather=False):
        self.calls.append((location, profile))
        return make_weather()


class FakeMessage:
    def __init__(self, text=None):
        self.text = text
        self.location = None

    async def set_reaction(self, _reaction):
        return None


class LastLocationMemoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = FakeWeatherService()
        deps = BotDependencies(weather_service=self.service, llm_service=None)
        self.handlers = WeatherHandlers(deps)
        self.chat_data = {}

    def _update(self, text=None):
        return SimpleNamespace(
            effective_message=FakeMessage(text),
            effective_chat=SimpleNamespace(id=1, type="private"),
        )

    def _context(self, args):
        return SimpleNamespace(args=args, chat_data=self.chat_data, bot=FakeBot())

    async def test_no_arg_tq_falls_back_to_last_queried_city(self):
        await self.handlers.handle_weather_request(self._update(), self._context(["北京"]))
        self.assertEqual(self.chat_data["last_location"]["coords"], "116.4,39.9")

        await self.handlers.handle_weather_request(self._update(), self._context([]))
        self.assertEqual(self.service.calls[-1][0], "116.4,39.9")

    async def test_private_plain_text_triggers_query_but_long_text_is_ignored(self):
        context = self._context([])
        await self.handlers.handle_private_text(self._update("北京"), context)
        self.assertEqual(len(self.service.calls), 1)

        long_text = "这是一段明显不是城市名的很长很长的闲聊内容啊朋友们"
        await self.handlers.handle_private_text(self._update(long_text), self._context([]))
        self.assertEqual(len(self.service.calls), 1)


if __name__ == "__main__":
    unittest.main()
