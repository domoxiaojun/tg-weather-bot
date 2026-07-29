import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from telegram import Chat, InputFile, Message

from core.handlers.callbacks import CallbackHandlers
from core.handlers.messages import edit_weather_view, send_weather_view
from domain.models import (
    AirQuality,
    DailyForecast,
    HistoricalDaySummary,
    LifeIndex,
    WarningAlert,
    WeatherData,
)
from services.chart_cache import PreparedChart, remember_chart_file_id
from services.telegram_rich import rich
from utils.formatter import format_weather_response
from utils.rich_formatter import (
    build_indices_blocks,
    build_realtime_blocks,
    build_report_blocks,
    uv_level_text,
)
from utils.weather_icons import set_custom_emoji_map_for_tests


INDEX_NAMES = {
    "1": "运动指数",
    "2": "洗车指数",
    "3": "穿衣指数",
    "4": "钓鱼指数",
    "5": "紫外线指数",
    "6": "旅游指数",
    "7": "过敏指数",
    "8": "舒适度指数",
    "9": "感冒指数",
    "10": "空气污染扩散条件指数",
    "11": "空调开启指数",
    "12": "太阳镜指数",
    "13": "化妆指数",
    "14": "晾晒指数",
    "15": "交通指数",
    "16": "防晒指数",
}


def make_weather(index_count: int = 16) -> WeatherData:
    stamp = datetime(2026, 7, 27, 20, 19)
    indices = [
        LifeIndex(
            type=type_id,
            name=name,
            category="不宜" if type_id == "2" else "热" if type_id == "3" else f"等级{type_id}",
            date=stamp,
        )
        for type_id, name in list(INDEX_NAMES.items())[:index_count]
    ]
    return WeatherData(
        location_name="潮安, 广东省",
        coords="116.68,23.46",
        update_time=stamp,
        now_temp=25,
        now_text="阴",
        now_icon="104",
        now_wind_dir="东风",
        now_wind_scale="3",
        now_wind_speed=12,
        now_humidity=90,
        now_precip=0,
        now_precip_kind="amount",
        now_vis=30,
        now_pressure=1002,
        now_cloud=91,
        daily=[
            DailyForecast(
                date=stamp,
                temp_min=24,
                temp_max=29,
                text_day="中雨",
                icon_day="306",
                text_night="暴雨",
                icon_night="310",
                humidity=94,
                vis=14,
                precip=9.5,
                precip_kind="amount",
            )
        ],
        alerts=[
            WarningAlert(
                title="暴雨防御提醒",
                type="Rainstorm",
                level="橙色",
                text="请注意防范强降雨。",
                pub_time=stamp,
                source="QWeather",
            )
        ],
        indices=indices,
        air_quality=AirQuality(aqi=18, category="优"),
    )


def plain_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(plain_text(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") == "custom_emoji":
            return value.get("alternative_text") or ""
        return plain_text(value.get("text", ""))
    return ""


def table_text(block: dict) -> list[list[str]]:
    return [
        [plain_text(cell.get("text", "")) for cell in row]
        for row in block.get("cells", [])
    ]


def _flatten_block_text(block: dict) -> str:
    """Include custom_emoji alternative_text so layout asserts stay stable."""
    def walk(value) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return "".join(walk(item) for item in value)
        if isinstance(value, dict):
            if value.get("type") == "custom_emoji":
                return value.get("alternative_text") or ""
            parts = [walk(value.get("text", "")), walk(value.get("summary", ""))]
            if value.get("type") == "table":
                for row in value.get("cells") or []:
                    for cell in row:
                        parts.append(walk(cell.get("text", "")))
            for nested in value.get("blocks") or []:
                parts.append(walk(nested))
            return "".join(parts)
        return ""

    return walk(block)


class WeatherCardLayoutTests(unittest.TestCase):
    def test_default_card_orders_details_indices_then_air(self):
        blocks = build_realtime_blocks(make_weather())
        all_text = "\n".join(_flatten_block_text(block) for block in blocks)
        self.assertNotIn("今日详情", all_text)
        self.assertEqual(all_text.count("潮安, 广东省"), 1)

        extra_index = next(
            index
            for index, block in enumerate(blocks)
            if block.get("type") == "details" and "更多气象参数" in _flatten_block_text(block)
        )
        index_details = next(
            index
            for index, block in enumerate(blocks)
            if block.get("type") == "details" and "生活指数" in _flatten_block_text(block)
        )
        air_index = next(
            index
            for index, block in enumerate(blocks)
            if block.get("type") == "details" and "空气质量" in _flatten_block_text(block)
        )
        self.assertLess(extra_index, index_details)
        self.assertLess(index_details, air_index)
        # Collapsed by default (same pattern as air quality).
        self.assertFalse(blocks[index_details].get("is_open"))
        self.assertFalse(blocks[extra_index].get("is_open"))

        # Exactly one visible table before the folds — the core stats table.
        tables_before_folds = [
            block for block in blocks[:extra_index] if block.get("type") == "table"
        ]
        self.assertEqual(len(tables_before_folds), 1)
        core_labels = [row[0] for row in table_text(tables_before_folds[0])]
        self.assertLessEqual(len(core_labels), 8)
        # Ordering is the point: 冷热 → 概貌 → 体感 → 带伞. Compare the words
        # only; the leading icon is a custom emoji whose fallback may change.
        words = [label.split(" ", 1)[-1] for label in core_labels]
        self.assertEqual(words, ["气温", "日间/夜间", "湿度", "风况", "降水"])
        # Secondary readings must not stay on the core table.
        for label in ("云量", "能见度", "气压", "当前降水", "月相", "日均温"):
            self.assertFalse(
                any(label in text for text in core_labels), f"{label} 不应留在主表"
            )
        extra_labels = [
            row[0]
            for nested in blocks[extra_index].get("blocks") or []
            if nested.get("type") == "table"
            for row in table_text(nested)
        ]
        for label in ("当前降水", "云量", "能见度", "气压"):
            self.assertTrue(any(label in text for text in extra_labels), label)

        # Life indices: collapsed details wrapping a two-column table.
        index_table = next(
            nested
            for nested in blocks[index_details].get("blocks") or []
            if nested.get("type") == "table"
        )
        index_rows = table_text(index_table)
        self.assertEqual(len(index_rows), 8)
        self.assertEqual(index_rows[0][0], "🚗 洗车：不宜")
        self.assertEqual(index_rows[0][1], "👕 穿衣：热")
        self.assertNotIn("指数：", index_rows[0][0])

    def test_uv_value_carries_a_level_word(self):
        weather = make_weather()
        weather.daily[0].uv_index = 11
        weather.daily[0].sunrise = "05:52"
        weather.daily[0].sunset = "19:50"
        weather.yesterday = HistoricalDaySummary(
            date=datetime(2026, 7, 26), temp_max=28, temp_min=23
        )
        blocks = build_realtime_blocks(weather)
        core_table = next(block for block in blocks if block.get("type") == "table")
        labels = [row[0].split(" ", 1)[-1] for row in table_text(core_table)]
        rows = {row[0].split(" ", 1)[-1]: row[1] for row in table_text(core_table)}
        self.assertEqual(rows["紫外线"], "11 极强")
        self.assertEqual(rows["日出/日落"], "05:52 / 19:50")
        # Trend closes the table; UV/sunlight sit between rain and the trend.
        self.assertEqual(labels[-1], "比昨天")
        self.assertLess(labels.index("降水"), labels.index("紫外线"))
        self.assertLess(labels.index("紫外线"), labels.index("日出/日落"))
        self.assertEqual(uv_level_text(3), "3 中等")
        self.assertEqual(uv_level_text(0), "0 弱")

    def test_odd_index_count_last_line_has_one_entry(self):
        blocks = build_realtime_blocks(make_weather(index_count=15))
        index_details = next(
            block
            for block in blocks
            if block.get("type") == "details" and "生活指数" in _flatten_block_text(block)
        )
        index_table = next(
            nested
            for nested in index_details.get("blocks") or []
            if nested.get("type") == "table"
        )
        rows = table_text(index_table)
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[-1][1], "")

    def test_alert_summary_is_bold_without_marked_background(self):
        blocks = build_realtime_blocks(make_weather())
        alert = next(
            block
            for block in blocks
            if block.get("type") == "details" and "暴雨防御提醒" in _flatten_block_text(block)
        )
        summary = alert["summary"]
        self.assertTrue(any(isinstance(part, dict) and part.get("type") == "bold" for part in summary))
        self.assertFalse(any(isinstance(part, dict) and part.get("type") == "marked" for part in summary))

    def test_indices_view_uses_two_column_table(self):
        blocks = build_indices_blocks(make_weather())
        # Dedicated indices view stays expanded (not collapsed details).
        self.assertFalse(
            any(
                block.get("type") == "details" and "生活指数" in _flatten_block_text(block)
                for block in blocks
            )
        )
        index_table = next(block for block in blocks if block.get("type") == "table")
        rows = table_text(index_table)
        self.assertEqual(rows[0], ["🚗 洗车：不宜", "👕 穿衣：热"])
        self.assertEqual(len(rows), 8)

    def test_report_section_titles_use_weather_custom_emoji(self):
        set_custom_emoji_map_for_tests({"104": "id-104", "1003": "id-1003", "305": "id-305"})
        try:
            html = (
                "🟡 <b>预警</b>\n"
                "注意暴雨。\n"
                "☀️ <b>现在</b>\n"
                "阴天。\n"
                "⏱️ <b>接下来</b>\n"
                "稍后有雨。\n"
            )
            weather = make_weather()
            weather.now_icon = "104"
            blocks = build_report_blocks(
                html, title=f"🤖 {weather.location_name} 天气日报", weather=weather
            )
            title = blocks[0]["text"]
            self.assertIsInstance(title, list)
            self.assertEqual(title[0]["type"], "custom_emoji")
            self.assertEqual(title[0]["custom_emoji_id"], "id-104")

            def first_emoji(block):
                text = block.get("text")
                if isinstance(text, list) and text and isinstance(text[0], dict):
                    return text[0]
                return None

            alert_line = next(
                b for b in blocks if b.get("type") == "paragraph" and first_emoji(b)
                and "预警" in _flatten_block_text(b)
            )
            self.assertEqual(first_emoji(alert_line)["custom_emoji_id"], "id-1003")
            now_line = next(
                b for b in blocks if b.get("type") == "paragraph" and "现在" in _flatten_block_text(b)
            )
            self.assertEqual(first_emoji(now_line)["custom_emoji_id"], "id-104")
            next_line = next(
                b for b in blocks if b.get("type") == "paragraph" and "接下来" in _flatten_block_text(b)
            )
            self.assertEqual(first_emoji(next_line)["custom_emoji_id"], "id-305")
        finally:
            set_custom_emoji_map_for_tests(None)

    def test_core_table_labels_are_all_custom_emoji(self):
        """Row labels must come from the uploaded QWeather pack, not system emoji."""
        set_custom_emoji_map_for_tests(
            {code: f"id-{code}" for code in ("100", "101", "102", "104", "150", "151",
                                             "305", "306", "310", "501", "502", "503", "900")}
        )
        try:
            weather = make_weather()
            weather.daily[0].uv_index = "11"
            weather.daily[0].sunrise = "05:52"
            weather.daily[0].sunset = "19:50"
            blocks = build_realtime_blocks(weather)
            core_table = next(block for block in blocks if block.get("type") == "table")
            for row in core_table["cells"]:
                label = row[0].get("text")
                self.assertIsInstance(label, list, f"标签必须是 RichText 段: {label}")
                self.assertEqual(
                    label[0].get("type"),
                    "custom_emoji",
                    f"标签首段必须是 custom emoji: {label}",
                )
                for segment in label:
                    if isinstance(segment, dict):
                        continue
                    self.assertRegex(
                        segment, r"^[ 　\w一-鿿/·]+$", f"标签混入系统 emoji: {segment}"
                    )

            extra = next(
                block
                for block in blocks
                if block.get("type") == "details" and "更多气象参数" in _flatten_block_text(block)
            )
            self.assertEqual(extra["summary"][0].get("type"), "custom_emoji")
        finally:
            set_custom_emoji_map_for_tests(None)

    def test_report_tail_sections_collapse_and_footer_stays_outside(self):
        html = (
            "☀️ <b>现在</b>\n"
            "36°C 多云。\n"
            "⏱️ <b>接下来</b>\n"
            "80 分钟后有小雨。\n"
            "📅 <b>未来几天</b>\n"
            "周三转雨。\n"
            "👕 <b>建议</b>\n"
            "薄长袖 · 带伞\n"
            "🤖 Generated by test-model\n"
        )
        blocks = build_report_blocks(html, collapse_tail=True)
        fold = next(block for block in blocks if block.get("type") == "details")
        fold_text = _flatten_block_text(fold)
        self.assertIn("未来几天", fold_text)
        self.assertIn("建议", fold_text)
        self.assertIn("薄长袖", fold_text)
        self.assertFalse(fold.get("is_open"))

        above = "\n".join(
            _flatten_block_text(block)
            for block in blocks
            if block.get("type") not in ("details", "footer")
        )
        self.assertIn("现在", above)
        self.assertIn("接下来", above)
        self.assertNotIn("周三转雨", above)
        self.assertNotIn("薄长袖", above)

        # Attribution belongs at the very bottom, never inside the fold.
        self.assertEqual(blocks[-1].get("type"), "footer")
        self.assertNotIn("Generated by", fold_text)

    def test_report_without_collapse_tail_keeps_every_line_visible(self):
        html = "📅 <b>未来几天</b>\n周三转雨。\n"
        blocks = build_report_blocks(html)
        self.assertFalse(any(block.get("type") == "details" for block in blocks))

    def test_air_quality_table_has_three_columns_no_description(self):
        weather = make_weather()
        weather.air_quality.pm2p5 = 12
        weather.air_quality.pm10 = 20
        blocks = build_realtime_blocks(weather)
        air = next(
            block
            for block in blocks
            if block.get("type") == "details" and "空气质量" in _flatten_block_text(block)
        )
        aq_table = next(
            nested for nested in air.get("blocks") or [] if nested.get("type") == "table"
        )
        rows = table_text(aq_table)
        self.assertEqual(rows[0], ["污染物", "浓度", "水平"])
        self.assertEqual(len(rows[1]), 3)
        joined = "\n".join(" ".join(row) for row in rows)
        self.assertNotIn("细颗粒物", joined)
        self.assertNotIn("说明", joined)

    def test_plain_fallback_keeps_indices_before_air(self):
        text = format_weather_response(make_weather())
        self.assertNotIn("今日详情", text)
        self.assertEqual(text.count("潮安, 广东省"), 1)
        self.assertLess(text.index("💡 *生活指数*"), text.index("空气"))
        self.assertIn("🚗 洗车：不宜", text)
        self.assertIn("👕 穿衣：热", text)
        self.assertNotIn("洗车指数：", text)


class WeatherRichDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        rich.reset()

    @staticmethod
    def sent_message() -> Message:
        return Message(
            message_id=101,
            date=datetime(2026, 7, 27, 20, 19),
            chat=Chat(id=42, type="private"),
        )

    async def test_first_chart_upload_is_embedded_in_one_rich_message(self):
        bot = SimpleNamespace(
            do_api_request=AsyncMock(return_value=self.sent_message()),
            send_message=AsyncMock(),
        )
        context = SimpleNamespace(bot=bot)
        chart = PreparedChart(
            chart_type="rain",
            caption="逐小时降水",
            png_bytes=b"\x89PNG\r\n\x1a\nchart",
        )

        with patch(
            "core.handlers.messages.remember_chart_file_id", new=AsyncMock()
        ) as remember:
            await send_weather_view(context, 42, make_weather(), chart=chart)

        bot.do_api_request.assert_awaited_once()
        bot.send_message.assert_not_awaited()
        endpoint = bot.do_api_request.await_args.args[0]
        payload = bot.do_api_request.await_args.kwargs["api_kwargs"]
        self.assertEqual(endpoint, "sendRichMessage")
        self.assertIsInstance(payload["weather_chart"], InputFile)
        photo = next(
            block for block in payload["rich_message"]["blocks"] if block.get("type") == "photo"
        )
        self.assertEqual(photo["photo"]["media"], "attach://weather_chart")
        remember.assert_awaited_once()

    async def test_cached_chart_uses_file_id_without_attachment(self):
        bot = SimpleNamespace(
            do_api_request=AsyncMock(return_value=self.sent_message()),
            send_message=AsyncMock(),
        )
        context = SimpleNamespace(bot=bot)
        chart = PreparedChart(
            chart_type="rain",
            caption="逐小时降水",
            file_id="cached-chart-file-id",
        )

        with patch("core.handlers.messages.remember_chart_file_id", new=AsyncMock()):
            await send_weather_view(context, 42, make_weather(), chart=chart)

        payload = bot.do_api_request.await_args.kwargs["api_kwargs"]
        self.assertNotIn("weather_chart", payload)
        photo = next(
            block for block in payload["rich_message"]["blocks"] if block.get("type") == "photo"
        )
        self.assertEqual(photo["photo"]["media"], "cached-chart-file-id")

    async def test_uploaded_rich_photo_file_id_is_cached_from_raw_response(self):
        message = SimpleNamespace(
            photo=None,
            api_kwargs={
                "rich_message": {
                    "blocks": [
                        {
                            "type": "photo",
                            "photo": [
                                {"file_id": "small"},
                                {"file_id": "largest-rich-file-id"},
                            ],
                        }
                    ]
                }
            },
        )
        with patch("services.chart_cache.cache.set", new=AsyncMock()) as cache_set:
            await remember_chart_file_id(make_weather(), "rain", message)

        self.assertEqual(cache_set.await_args.args[1], "largest-rich-file-id")

    async def test_rich_failure_falls_back_to_one_text_message(self):
        bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=5)))
        context = SimpleNamespace(bot=bot)
        chart = PreparedChart(
            chart_type="rain",
            caption="逐小时降水",
            png_bytes=b"chart",
        )
        with patch.object(rich, "send_rich", new=AsyncMock(return_value=None)) as send_rich:
            await send_weather_view(context, 42, make_weather(), chart=chart)

        send_rich.assert_awaited_once()
        bot.send_message.assert_awaited_once()

    async def test_edit_replaces_or_removes_embedded_chart_in_place(self):
        context = SimpleNamespace(bot=SimpleNamespace())
        weather = make_weather()
        chart = PreparedChart(
            chart_type="rain",
            caption="逐小时降水",
            png_bytes=b"chart",
        )
        with patch.object(rich, "edit_rich", new=AsyncMock(return_value=True)) as edit:
            self.assertTrue(
                await edit_weather_view(
                    context,
                    weather,
                    chat_id=42,
                    message_id=101,
                    chart=chart,
                )
            )
            with_chart = edit.await_args.kwargs
            self.assertIn("weather_chart", with_chart["attachments"])
            self.assertTrue(
                any(block.get("type") == "photo" for block in with_chart["blocks"])
            )

            self.assertTrue(
                await edit_weather_view(
                    context,
                    weather,
                    chat_id=42,
                    message_id=101,
                    view_type="indices",
                    chart=None,
                )
            )
            without_chart = edit.await_args.kwargs
            self.assertIsNone(without_chart["attachments"])
            self.assertFalse(
                any(block.get("type") == "photo" for block in without_chart["blocks"])
            )


class WeatherCallbackChartTests(unittest.IsolatedAsyncioTestCase):
    async def test_view_switch_prepares_chart_and_edits_the_same_message(self):
        weather = make_weather()
        chart = PreparedChart(chart_type="daily", caption="逐日温度", file_id="daily-id")
        service = SimpleNamespace(get_fused_weather=AsyncMock(return_value=weather))
        weather_handlers = SimpleNamespace(prepare_auto_chart=AsyncMock(return_value=chart))
        handler = CallbackHandlers(
            SimpleNamespace(weather_service=service),
            weather_handlers=weather_handlers,
        )
        query = SimpleNamespace(
            message=SimpleNamespace(chat_id=42, message_id=101, caption=None),
            inline_message_id=None,
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=42))

        with patch(
            "core.handlers.callbacks.edit_weather_view", new=AsyncMock(return_value=True)
        ) as edit:
            await handler._handle_view_switch(
                update,
                SimpleNamespace(bot=SimpleNamespace()),
                ["view", "116.68,23.46", "daily", "0", "7"],
            )

        weather_handlers.prepare_auto_chart.assert_awaited_once_with(weather, "daily")
        self.assertEqual(edit.await_args.kwargs["message_id"], 101)
        self.assertEqual(edit.await_args.kwargs["chart"], chart)

    async def test_refresh_prepares_chart_and_edits_the_same_message(self):
        weather = make_weather()
        chart = PreparedChart(chart_type="rain", caption="逐小时降水", file_id="rain-id")
        service = SimpleNamespace(get_fused_weather=AsyncMock(return_value=weather))
        weather_handlers = SimpleNamespace(prepare_auto_chart=AsyncMock(return_value=chart))
        handler = CallbackHandlers(
            SimpleNamespace(weather_service=service),
            weather_handlers=weather_handlers,
        )
        query = SimpleNamespace(
            message=SimpleNamespace(chat_id=42, message_id=101, caption=None),
            inline_message_id=None,
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=42))

        with patch(
            "core.handlers.callbacks.edit_weather_view", new=AsyncMock(return_value=True)
        ) as edit:
            await handler._handle_refresh(
                update,
                SimpleNamespace(bot=SimpleNamespace()),
                ["refresh", "116.68,23.46", "rain", "0", "0"],
            )

        weather_handlers.prepare_auto_chart.assert_awaited_once_with(weather, "rain")
        self.assertEqual(edit.await_args.kwargs["message_id"], 101)
        self.assertEqual(edit.await_args.kwargs["chart"], chart)


if __name__ == "__main__":
    unittest.main()
