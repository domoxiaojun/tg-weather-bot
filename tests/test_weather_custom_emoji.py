"""Custom weather emoji resolution (no network)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Settings require credentials at import time.
os.environ.setdefault("BOT_TOKEN", "0000000000:TEST_TOKEN_FOR_UNITTESTS")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from services.telegram_rich import custom_emoji  # noqa: E402
from utils import weather_icons as wi  # noqa: E402
from utils.rich_formatter import build_header  # noqa: E402
from utils.weather_icons import (  # noqa: E402
    emoji_for,
    rich_icon_pair,
    rich_icon_text,
    set_custom_emoji_map_for_tests,
    weather_icon,
    weather_icon_html,
    weather_icon_md,
    weather_icon_rich,
)
from domain.models import WeatherData  # noqa: E402
from datetime import datetime, timezone  # noqa: E402


class WeatherCustomEmojiTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_custom_emoji_map_for_tests(None)
        wi.reload_custom_emoji_map()

    def test_emoji_fallback_for_known_code(self) -> None:
        set_custom_emoji_map_for_tests({})
        self.assertEqual(weather_icon("100"), "☀️")
        self.assertEqual(emoji_for("301"), "🌧️")
        self.assertEqual(weather_icon(""), "❓")

    def test_legacy_emoji_passthrough(self) -> None:
        set_custom_emoji_map_for_tests({})
        self.assertEqual(weather_icon("☀️"), "☀️")

    def test_rich_custom_emoji_when_mapped(self) -> None:
        set_custom_emoji_map_for_tests({"100": "111", "301": "222"})
        self.assertEqual(
            weather_icon_rich("100"),
            {
                "type": "custom_emoji",
                "custom_emoji_id": "111",
                "alternative_text": "☀️",
            },
        )
        self.assertEqual(weather_icon_rich("999"), "❓")  # unmapped → emoji

    def test_html_and_md_fragments(self) -> None:
        set_custom_emoji_map_for_tests({"100": "111"})
        self.assertEqual(
            weather_icon_html("100"),
            '<tg-emoji emoji-id="111">☀️</tg-emoji>',
        )
        self.assertEqual(weather_icon_md("100"), "![☀️](tg://emoji?id=111)")
        self.assertEqual(weather_icon_html("301"), "🌧️")
        self.assertEqual(weather_icon_md("301"), "🌧️")

    def test_disabled_flag_forces_emoji(self) -> None:
        set_custom_emoji_map_for_tests({"100": "111"})
        with mock.patch.object(wi, "_settings_enabled", return_value=False):
            self.assertEqual(weather_icon_rich("100"), "☀️")
            self.assertEqual(weather_icon_html("100"), "☀️")

    def test_rich_helpers_compose(self) -> None:
        set_custom_emoji_map_for_tests({"100": "111", "150": "222"})
        self.assertEqual(
            rich_icon_text("100", "上海"),
            [weather_icon_rich("100"), " ", "上海"],
        )
        self.assertEqual(
            rich_icon_pair("100", "150"),
            [weather_icon_rich("100"), "→", weather_icon_rich("150")],
        )
        self.assertEqual(rich_icon_pair("100", "100"), weather_icon_rich("100"))

    def test_load_map_from_disk(self) -> None:
        set_custom_emoji_map_for_tests(None)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            path.write_text('{"icons": {"104": "9999"}}', encoding="utf-8")
            with mock.patch.object(wi, "resolve_custom_emoji_map_path", return_value=path):
                wi.reload_custom_emoji_map()
                self.assertEqual(weather_icon_rich("104")["custom_emoji_id"], "9999")

    def test_bundled_resources_map_exists(self) -> None:
        """Image ships resources/ so docker ./data mounts cannot erase the map."""
        bundled = Path(__file__).resolve().parents[1] / "resources" / "weather_custom_emoji.json"
        self.assertTrue(bundled.is_file(), msg=f"missing {bundled}")
        data = __import__("json").loads(bundled.read_text(encoding="utf-8"))
        self.assertIn("100", data.get("icons") or data)

    def test_header_uses_rich_icon(self) -> None:
        set_custom_emoji_map_for_tests({"101": "555"})
        data = WeatherData(
            location_name="测试",
            coords="0,0",
            update_time=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
            source="qweather",
            now_temp=25.0,
            now_text="多云",
            now_icon="101",
        )
        blocks = build_header(data)
        title = blocks[0]["text"]
        self.assertIsInstance(title, list)
        self.assertEqual(title[0]["type"], "custom_emoji")
        self.assertEqual(title[0]["custom_emoji_id"], "555")
        self.assertIn("测试", title[2] if len(title) > 2 else title[1])

    def test_custom_emoji_helper(self) -> None:
        self.assertEqual(
            custom_emoji("123", "☀️"),
            {
                "type": "custom_emoji",
                "custom_emoji_id": "123",
                "alternative_text": "☀️",
            },
        )

    def test_upload_order_is_stable_dict_order(self) -> None:
        from utils.weather_icons import UPLOAD_ICON_CODES, WEATHER_ICONS

        self.assertEqual(list(UPLOAD_ICON_CODES), list(WEATHER_ICONS.keys()))
        self.assertEqual(UPLOAD_ICON_CODES[0], "100")
        self.assertEqual(UPLOAD_ICON_CODES[5], "150")
        self.assertEqual(UPLOAD_ICON_CODES[-1], "999")

    def test_each_fallback_is_a_single_emoji(self) -> None:
        """Telegram custom-emoji base must be one emoji, not ❄️+🌨️ glued together."""
        import re

        from utils.weather_icons import WEATHER_ICONS

        emoji_seq = re.compile(
            r"(?:"
            r"[\U0001F1E6-\U0001F1FF]{2}"
            r"|[\U0001F300-\U0001FAFF\u2600-\u27BF]"
            r"(?:\uFE0F)?"
            r"(?:\u200D[\U0001F300-\U0001FAFF\u2600-\u27BF](?:\uFE0F)?)*"
            r")"
        )
        for code, em in WEATHER_ICONS.items():
            seqs = emoji_seq.findall(em)
            self.assertEqual(
                len(seqs),
                1,
                msg=f"code {code} fallback {em!r} must be exactly one emoji, got {seqs!r}",
            )

    def test_import_mdv2_by_order(self) -> None:
        import importlib.util
        from utils.weather_icons import UPLOAD_ICON_CODES

        path = Path(__file__).resolve().parents[1] / "scripts" / "import_weather_emoji_md.py"
        spec = importlib.util.spec_from_file_location("import_weather_emoji_md", path)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)

        sample = (
            "![☀️](tg://emoji?id=1111111111111111111)"
            "![🌤️](tg://emoji?id=2222222222222222222)"
        )
        ids = mod.extract_ids(sample)
        self.assertEqual(ids, ["1111111111111111111", "2222222222222222222"])
        padded = ids + [str(3000 + i) for i in range(len(UPLOAD_ICON_CODES) - 2)]
        mapping = mod.build_map(padded)
        self.assertEqual(mapping["100"], "1111111111111111111")
        self.assertEqual(mapping["101"], "2222222222222222222")
        self.assertEqual(len(mapping), len(UPLOAD_ICON_CODES))


if __name__ == "__main__":
    unittest.main()
