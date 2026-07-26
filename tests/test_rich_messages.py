"""Wire-format tests for Bot API 10.1/10.2 rich + ephemeral support.

The payloads are asserted against the Bot API 10.2 specification (2026-07-14):
InputRichMessage carries blocks/html/markdown (never text/parse_mode), table
cells require align and valign, block type discriminators are exact strings,
and sendRichMessageDraft needs a non-zero draft_id.
"""

import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from telegram.error import BadRequest, EndPointNotFound, Forbidden, NetworkError

from core.config import settings
from domain.models import DailyForecast, HourlyForecast, LifeIndex, WarningAlert, WeatherData
from services import telegram_rich as tr
from domain.models import AirQuality
from utils.rich_formatter import (
    build_air_quality_blocks,
    build_daily_blocks,
    build_hourly_blocks,
    build_indices_blocks,
    build_period_matrix,
    build_rain_alert_blocks,
    build_realtime_blocks,
    build_weather_blocks,
)

TZ = timezone(timedelta(hours=8))


def make_weather(*, hours: int = 26, days: int = 7, alerts: bool = True) -> WeatherData:
    now = datetime(2026, 7, 26, 16, 40, tzinfo=TZ)
    hourly = [
        HourlyForecast(
            time=now.replace(minute=0) + timedelta(hours=i),
            temp=28 + i % 4,
            feels_like=30 + i % 3,
            text="多云" if i % 3 else "晴",
            icon="101" if i % 3 else "100",
            pop=10 * (i % 5),
            precip=0.2 * (i % 3),
            precip_kind="amount",
            wind_dir="东南风",
            wind_scale="3",
            wind_speed=12,
            humidity=40 + i,
            uv_index=5 if i < 6 else None,
        )
        for i in range(hours)
    ]
    daily = [
        DailyForecast(
            date=datetime(2026, 7, 26) + timedelta(days=i),
            temp_min=22 + i,
            temp_max=31 + i,
            text_day="晴",
            icon_day="100",
            text_night="多云",
            icon_night="151",
            humidity=40,
            precip=0.0,
            precip_kind="amount",
            sunrise="05:05",
            sunset="19:46",
            uv_index="8",
            moon_phase="峨眉月",
            wind_dir_day="东南风",
            wind_scale_day="3",
            wind_speed_day=12,
        )
        for i in range(days)
    ]
    return WeatherData(
        source="fusion",
        location_name="北京, 北京市",
        coords="116.4,39.9",
        now_temp=30,
        now_feels_like=32.5,
        now_text="晴",
        now_icon="100",
        now_wind_dir="东南风",
        now_wind_scale="3",
        now_wind_speed=12,
        now_humidity=40,
        now_precip=0.0,
        now_precip_kind="amount",
        now_pressure=1005,
        now_vis=25,
        summary="当前 晴，温度 30°C。\n未来两小时不会下雨",
        update_time=now,
        hourly=hourly,
        daily=daily,
        indices=[
            LifeIndex(type="3", name="穿衣", category="短袖", text="建议穿短袖", source="qweather"),
            LifeIndex(type="2", name="洗车", category="适宜", text="", source="qweather"),
        ],
        alerts=(
            [WarningAlert(title="高温预警", type="heat", level="黄色", text="注意防暑" * 40,
                          pub_time=datetime(2026, 7, 26, 16, tzinfo=TZ), source="QWeather")]
            if alerts
            else []
        ),
    )


def walk(blocks):
    """Yield every block recursively (blocks nest inside details/list/quote)."""
    for block in blocks:
        yield block
        for key in ("blocks",):
            for nested in block.get(key, []) or []:
                yield from walk([nested])
        for item in block.get("items", []) or []:
            yield from walk(item.get("blocks", []) or [])


class RichMessagePayloadTests(unittest.TestCase):
    def test_input_rich_message_uses_blocks_not_text_parse_mode(self):
        payload = tr.rich_message(blocks=[tr.paragraph("hi")])
        self.assertIn("blocks", payload)
        self.assertNotIn("text", payload)
        self.assertNotIn("parse_mode", payload)
        self.assertTrue(payload["skip_entity_detection"])

    def test_html_variant_and_mutual_exclusion(self):
        payload = tr.rich_message(html="<b>hi</b>")
        self.assertEqual(payload["html"], "<b>hi</b>")
        with self.assertRaises(ValueError):
            tr.rich_message()
        with self.assertRaises(ValueError):
            tr.rich_message(blocks=[tr.paragraph("a")], html="<b>b</b>")

    def test_block_type_discriminators_match_spec(self):
        self.assertEqual(tr.paragraph("x")["type"], "paragraph")
        self.assertEqual(tr.heading("x")["type"], "heading")
        self.assertEqual(tr.divider()["type"], "divider")
        self.assertEqual(tr.footer("x")["type"], "footer")
        self.assertEqual(tr.thinking("x")["type"], "thinking")
        self.assertEqual(tr.preformatted("x")["type"], "pre")
        self.assertEqual(tr.blockquote([tr.paragraph("x")])["type"], "blockquote")
        self.assertEqual(tr.details("s", [tr.paragraph("x")])["type"], "details")
        self.assertEqual(tr.bullet_list(["a"])["type"], "list")
        self.assertEqual(tr.table([["a"]])["type"], "table")
        self.assertEqual(tr.photo_block("file-id")["type"], "photo")

    def test_heading_size_is_clamped_to_one_through_six(self):
        self.assertEqual(tr.heading("x", 0)["size"], 1)
        self.assertEqual(tr.heading("x", 9)["size"], 6)
        self.assertEqual(tr.heading("x", 4)["size"], 4)

    def test_every_table_cell_carries_required_align_and_valign(self):
        block = tr.table([["a", "b"], ["c", "d"]], headers=["h1", "h2"])
        for row in block["cells"]:
            for entry in row:
                self.assertIn("align", entry)
                self.assertIn("valign", entry)
                self.assertIn(entry["align"], {"left", "center", "right"})
                self.assertIn(entry["valign"], {"top", "middle", "bottom"})
        self.assertTrue(block["cells"][0][0]["is_header"])

    def test_rich_text_helpers_shape(self):
        self.assertEqual(tr.bold("x"), {"type": "bold", "text": "x"})
        self.assertEqual(tr.link("x", "https://e.com"), {"type": "url", "text": "x", "url": "https://e.com"})
        self.assertEqual(tr.marked("x")["type"], "marked")

    def test_list_items_wrap_bare_text_into_blocks(self):
        block = tr.bullet_list(["纯文本", tr.paragraph("已是块")])
        self.assertEqual(len(block["items"]), 2)
        for item in block["items"]:
            self.assertEqual(item["blocks"][0]["type"], "paragraph")

    def test_checklist_marks_checked_items(self):
        block = tr.checklist(["a", "b"], checked=[True, False])
        self.assertTrue(block["items"][0]["has_checkbox"])
        self.assertTrue(block["items"][0]["is_checked"])
        self.assertNotIn("is_checked", block["items"][1])

    def test_photo_block_wraps_input_media_photo(self):
        block = tr.photo_block("FILE123", "标题", credit="来源")
        self.assertEqual(block["photo"], {"type": "photo", "media": "FILE123"})
        self.assertEqual(block["caption"]["text"], "标题")
        self.assertEqual(block["caption"]["credit"], "来源")

    def test_draft_ids_are_non_zero_and_unique(self):
        first, second = tr.next_draft_id(), tr.next_draft_id()
        self.assertNotEqual(first, 0)
        self.assertNotEqual(first, second)


class FakeBot:
    """Minimal bot double recording do_api_request/send_message calls."""

    def __init__(self, *, error=None, result=None):
        self.error = error
        self.result = result if result is not None else True
        self.calls = []

    async def do_api_request(self, endpoint, api_kwargs=None, return_type=None):
        self.calls.append((endpoint, api_kwargs, return_type))
        if self.error:
            raise self.error
        return self.result

    async def send_message(self, **kwargs):
        self.calls.append(("sendMessage", kwargs, None))
        if self.error:
            raise self.error
        return "sent"


class CapabilityFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.messenger = tr.RichMessenger()

    async def test_endpoint_not_found_disables_feature_permanently(self):
        bot = FakeBot(error=EndPointNotFound("sendRichMessage not found"))
        self.assertIsNone(await self.messenger.send_rich(bot, 1, blocks=[tr.paragraph("x")]))
        self.assertFalse(self.messenger.supports(tr.FEATURE_SEND))

        # A second attempt must not hit the network again.
        before = len(bot.calls)
        self.assertIsNone(await self.messenger.send_rich(bot, 1, blocks=[tr.paragraph("x")]))
        self.assertEqual(len(bot.calls), before)

    async def test_bad_request_disables_only_after_repeated_strikes(self):
        bot = FakeBot(error=BadRequest("RICH_MESSAGE_INVALID"))
        for _ in range(tr._BAD_REQUEST_LIMIT - 1):
            self.assertIsNone(await self.messenger.send_rich(bot, 1, blocks=[tr.paragraph("x")]))
            self.assertTrue(self.messenger.supports(tr.FEATURE_SEND))
        self.assertIsNone(await self.messenger.send_rich(bot, 1, blocks=[tr.paragraph("x")]))
        self.assertFalse(self.messenger.supports(tr.FEATURE_SEND))

    async def test_transient_errors_never_disable_the_feature(self):
        bot = FakeBot(error=NetworkError("boom"))
        for _ in range(5):
            self.assertIsNone(await self.messenger.send_rich(bot, 1, blocks=[tr.paragraph("x")]))
        self.assertTrue(self.messenger.supports(tr.FEATURE_SEND))

    async def test_forbidden_is_chat_scoped_not_capability_scoped(self):
        bot = FakeBot(error=Forbidden("blocked"))
        self.assertIsNone(await self.messenger.send_rich(bot, 1, blocks=[tr.paragraph("x")]))
        self.assertTrue(self.messenger.supports(tr.FEATURE_SEND))

    async def test_config_flag_off_skips_every_rich_call(self):
        old = settings.enable_rich_messages
        settings.enable_rich_messages = False
        try:
            bot = FakeBot()
            self.assertIsNone(await self.messenger.send_rich(bot, 1, blocks=[tr.paragraph("x")]))
            self.assertFalse(await self.messenger.edit_rich(bot, chat_id=1, message_id=2, html="<b>x</b>"))
            self.assertEqual(bot.calls, [])
        finally:
            settings.enable_rich_messages = old

    async def test_draft_payload_targets_private_chat_with_draft_id(self):
        bot = FakeBot()
        self.assertTrue(await self.messenger.stream_draft(bot, 42, 7, blocks=[tr.thinking("…")]))
        endpoint, payload, _ = bot.calls[-1]
        self.assertEqual(endpoint, "sendRichMessageDraft")
        self.assertEqual(payload["chat_id"], 42)
        self.assertEqual(payload["draft_id"], 7)
        self.assertIn("blocks", payload["rich_message"])

    async def test_edit_uses_inline_message_id_when_given(self):
        bot = FakeBot()
        self.assertTrue(await self.messenger.edit_rich(bot, inline_message_id="abc", html="<b>x</b>"))
        endpoint, payload, _ = bot.calls[-1]
        self.assertEqual(endpoint, "editMessageText")
        self.assertEqual(payload["inline_message_id"], "abc")
        self.assertNotIn("chat_id", payload)
        self.assertNotIn("text", payload)

    async def test_ephemeral_send_passes_receiver_via_api_kwargs(self):
        bot = FakeBot()
        result = await self.messenger.send_ephemeral(bot, -100, "只有你能看到", 555, callback_query_id="cb1")
        self.assertEqual(result, "sent")
        endpoint, kwargs, _ = bot.calls[-1]
        self.assertEqual(endpoint, "sendMessage")
        self.assertEqual(kwargs["api_kwargs"]["receiver_user_id"], 555)
        self.assertEqual(kwargs["api_kwargs"]["callback_query_id"], "cb1")

    async def test_ephemeral_flag_off_falls_back(self):
        old = settings.enable_ephemeral_messages
        settings.enable_ephemeral_messages = False
        try:
            bot = FakeBot()
            self.assertIsNone(await self.messenger.send_ephemeral(bot, -100, "x", 555))
            self.assertEqual(bot.calls, [])
        finally:
            settings.enable_ephemeral_messages = old


class PersonalReplyRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Group bookkeeping replies go ephemeral; private chats stay normal."""

    def _update(self, chat_type):
        from types import SimpleNamespace

        return SimpleNamespace(
            effective_chat=SimpleNamespace(id=-1001, type=chat_type),
            effective_user=SimpleNamespace(id=777),
            callback_query=None,
        )

    async def test_group_reply_uses_ephemeral_parameters(self):
        from core.handlers.messages import send_personal_text

        tr.rich.reset()
        bot = FakeBot()
        context = type("Ctx", (), {"bot": bot})()
        await send_personal_text(self._update("supergroup"), context, "只有你能看到")
        endpoint, kwargs, _ = bot.calls[-1]
        self.assertEqual(endpoint, "sendMessage")
        self.assertEqual(kwargs["api_kwargs"]["receiver_user_id"], 777)

    async def test_private_reply_is_a_plain_send(self):
        from core.handlers.messages import send_personal_text

        tr.rich.reset()
        bot = FakeBot()
        context = type("Ctx", (), {"bot": bot})()
        await send_personal_text(self._update("private"), context, "普通消息")
        endpoint, kwargs, _ = bot.calls[-1]
        self.assertEqual(endpoint, "sendMessage")
        self.assertNotIn("api_kwargs", kwargs)

    async def test_group_reply_falls_back_when_server_lacks_ephemeral(self):
        from core.handlers.messages import send_personal_text

        class EphemeralUnawareBot(FakeBot):
            """Rejects only the 10.2 parameters, like an older Bot API server."""

            async def send_message(self, **kwargs):
                self.calls.append(("sendMessage", kwargs, None))
                if kwargs.get("api_kwargs"):
                    raise BadRequest("unknown parameter receiver_user_id")
                return "sent"

        tr.rich.reset()
        bot = EphemeralUnawareBot()
        context = type("Ctx", (), {"bot": bot})()
        result = await send_personal_text(self._update("supergroup"), context, "x")

        self.assertEqual(result, "sent")
        # First call attempted ephemeral, second is the plain fallback.
        self.assertEqual(len(bot.calls), 2)
        self.assertIn("api_kwargs", bot.calls[0][1])
        self.assertNotIn("api_kwargs", bot.calls[1][1])
        tr.rich.reset()


class WeatherBlockRenderingTests(unittest.TestCase):
    def test_hourly_view_renders_a_table_with_expected_columns(self):
        blocks = build_hourly_blocks(make_weather(), 12)
        tables = [b for b in walk(blocks) if b["type"] == "table"]
        self.assertEqual(len(tables), 1)
        header_texts = [c.get("text") for c in tables[0]["cells"][0]]
        self.assertEqual(header_texts[:3], ["时间", "天气", "温度"])
        self.assertIn("降概", header_texts)
        self.assertIn("降水", header_texts)

    def test_multi_day_hourly_table_inserts_full_width_date_row(self):
        blocks = build_hourly_blocks(make_weather(hours=26), 24)
        cells = [b for b in walk(blocks) if b["type"] == "table"][0]["cells"]
        spanning = [row for row in cells if len(row) == 1 and row[0].get("colspan")]
        self.assertTrue(spanning, "expected a colspan separator row across midnight")
        self.assertGreater(spanning[0][0]["colspan"], 1)

    def test_daily_view_has_table_and_collapsible_details(self):
        blocks = build_daily_blocks(make_weather(), 5)
        kinds = {b["type"] for b in walk(blocks)}
        self.assertIn("table", kinds)
        self.assertIn("details", kinds)

    def test_realtime_view_has_heading_stats_table_and_footer(self):
        blocks = build_realtime_blocks(make_weather())
        self.assertEqual(blocks[0]["type"], "heading")
        self.assertEqual(blocks[-1]["type"], "footer")
        kinds = [b["type"] for b in walk(blocks)]
        self.assertIn("table", kinds)
        self.assertIn("blockquote", kinds)  # the alert

    def test_realtime_view_without_alerts_has_no_blockquote(self):
        blocks = build_realtime_blocks(make_weather(alerts=False))
        self.assertNotIn("blockquote", [b["type"] for b in walk(blocks)])

    def test_indices_view_uses_lists(self):
        blocks = build_indices_blocks(make_weather())
        self.assertIn("list", [b["type"] for b in walk(blocks)])

    def test_rain_alert_blocks_start_with_heading(self):
        blocks = build_rain_alert_blocks(make_weather())
        self.assertEqual(blocks[0]["type"], "heading")

    def test_dispatcher_covers_all_views_and_every_block_is_valid(self):
        data = make_weather()
        for view in ("default", "hourly", "daily", "indices", "rain"):
            blocks = build_weather_blocks(data, view_type=view, days=6)
            self.assertTrue(blocks, view)
            for block in walk(blocks):
                self.assertIn(block["type"], tr._BLOCK_TYPES, f"{view}: {block['type']}")

    def test_blocks_are_json_serializable_for_ptb(self):
        import json

        for view in ("default", "hourly", "daily", "indices", "rain"):
            payload = tr.rich_message(blocks=build_weather_blocks(make_weather(), view_type=view))
            json.dumps(payload, ensure_ascii=False)

    def test_period_matrix_has_one_row_per_day_and_four_period_columns(self):
        blocks = build_period_matrix(make_weather(hours=48))
        self.assertEqual(len(blocks), 1)
        cells = blocks[0]["cells"]
        header = [c.get("text") for c in cells[0]]
        self.assertEqual(header, ["日期", "凌晨", "上午", "下午", "夜间"])
        for row in cells:
            self.assertEqual(len(row), 5)
        # 26 hours from 16:00 spans three calendar days.
        self.assertGreaterEqual(len(cells) - 1, 2)

    def test_period_matrix_leaves_uncovered_periods_invisible(self):
        cells = build_period_matrix(make_weather(hours=48))[0]["cells"]
        first_day = cells[1]
        # Hourly data starts at 16:00, so 凌晨/上午 of day one have no hours.
        self.assertNotIn("text", first_day[1])
        self.assertNotIn("text", first_day[2])
        self.assertIn("text", first_day[3])

    def test_period_matrix_highlights_wet_periods(self):
        cells = build_period_matrix(make_weather(hours=48))[0]["cells"]
        marks = [
            entry
            for row in cells[1:]
            for entry in row
            if isinstance(entry.get("text"), dict) and entry["text"].get("type") == "marked"
        ]
        self.assertTrue(marks, "expected precipitation periods to be highlighted")

    def test_period_matrix_needs_at_least_two_days(self):
        self.assertEqual(build_period_matrix(make_weather(hours=4)), [])
        self.assertEqual(build_period_matrix(make_weather(hours=0)), [])

    def test_daily_view_includes_the_period_matrix(self):
        blocks = build_daily_blocks(make_weather(hours=48), 7)
        captions = [
            b.get("caption") for b in walk(blocks) if b["type"] == "table" and b.get("caption")
        ]
        self.assertTrue(any("时段概览" in str(c) for c in captions))

    def test_air_quality_details_lists_all_reported_pollutants(self):
        data = make_weather()
        data.air_quality = AirQuality(
            aqi=82,
            category="良",
            primary="PM2.5",
            pm2p5=35.4,
            pm10=60.0,
            o3=90.0,
            no2=25.0,
            so2=5.0,
            co=0.6,
            description="敏感人群减少户外活动",
        )
        blocks = build_air_quality_blocks(data)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "details")
        self.assertIn("AQI 82", blocks[0]["summary"])
        pollutant_table = [b for b in walk(blocks) if b["type"] == "table"][0]
        names = [row[0].get("text") for row in pollutant_table["cells"][1:]]
        self.assertEqual(names, ["PM2.5", "PM10", "O₃", "NO₂", "SO₂", "CO"])
        rendered = str(blocks)
        self.assertIn("敏感人群减少户外活动", rendered)

    def test_air_quality_block_skipped_without_data(self):
        data = make_weather()
        data.air_quality = None
        self.assertEqual(build_air_quality_blocks(data), [])

    def test_realtime_view_drops_duplicate_air_row_when_details_present(self):
        data = make_weather()
        data.air_quality = AirQuality(aqi=50, category="优", pm2p5=12.0)
        blocks = build_realtime_blocks(data)
        stats_table = [b for b in walk(blocks) if b["type"] == "table"][0]
        labels = [row[0].get("text") for row in stats_table["cells"]]
        self.assertNotIn("🌫️ 空气", labels)
        self.assertTrue(any(b["type"] == "details" and "空气质量" in b["summary"] for b in walk(blocks)))

    def test_hourly_extras_expose_previously_hidden_metrics(self):
        data = make_weather()
        for index, hour in enumerate(data.hourly):
            hour.dew = 20.0 + index % 3
            hour.pressure = 1005.0
            hour.cloud = 30
            hour.visibility = 25.0
            hour.aqi = 60
        blocks = build_hourly_blocks(data, 6)
        extras = [
            b for b in walk(blocks) if b["type"] == "details" and "更多逐小时指标" in b["summary"]
        ]
        self.assertEqual(len(extras), 1)
        headers = [c.get("text") for c in [b for b in walk(extras) if b["type"] == "table"][0]["cells"][0]]
        self.assertEqual(headers, ["时间", "露点", "气压", "云量", "能见度", "AQI"])

    def test_hourly_extras_omitted_when_no_such_data(self):
        data = make_weather()
        for hour in data.hourly:
            hour.dew = hour.pressure = hour.cloud = hour.visibility = hour.aqi = None
        blocks = build_hourly_blocks(data, 6)
        self.assertFalse(
            [b for b in walk(blocks) if b["type"] == "details" and "更多逐小时" in b["summary"]]
        )

    def test_today_details_include_moonrise_avg_temp_and_split_precip(self):
        data = make_weather()
        today = data.daily[0]
        today.temp_avg = 26.5
        today.moon_rise = "19:20"
        today.moon_set = "05:40"
        today.precip_day = 1.2
        today.precip_night = 0.4
        rendered = str(build_realtime_blocks(data))
        self.assertIn("日均温", rendered)
        self.assertIn("19:20", rendered)
        self.assertIn("昼/夜降水", rendered)

    def test_rich_text_is_not_markdown_escaped(self):
        blocks = build_realtime_blocks(make_weather())
        rendered = str(blocks)
        self.assertNotIn("\\-", rendered)
        self.assertNotIn("\\(", rendered)
        self.assertIn("北京, 北京市", rendered)


if __name__ == "__main__":
    unittest.main()
