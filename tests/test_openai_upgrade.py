import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import openai
from pydantic import ValidationError


os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from core.config import DEFAULT_OPENAI_MODEL, Settings
from services.llm import OpenAIProvider


class OpenAIConfigurationTests(unittest.TestCase):
    def test_runtime_matches_pinned_sdk_release(self):
        self.assertEqual(openai.__version__, "2.48.0")

    def test_gpt_5_6_accepts_max_but_rejects_minimal_effort(self):
        configured = Settings(
            bot_token="test-token",
            qweather_api_key="test-qweather-key",
            openai_model=DEFAULT_OPENAI_MODEL,
            openai_reasoning_effort="max",
            _env_file=None,
        )
        self.assertEqual(configured.openai_reasoning_effort, "max")

        with self.assertRaises(ValidationError):
            Settings(
                bot_token="test-token",
                qweather_api_key="test-qweather-key",
                openai_model=DEFAULT_OPENAI_MODEL,
                openai_reasoning_effort="minimal",
                _env_file=None,
            )

    def test_legacy_model_can_keep_minimal_effort(self):
        configured = Settings(
            bot_token="test-token",
            qweather_api_key="test-qweather-key",
            openai_model="gpt-5.5",
            openai_reasoning_effort="minimal",
            _env_file=None,
        )
        self.assertEqual(configured.openai_reasoning_effort, "minimal")


class OpenAIProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_responses_request_uses_gpt_5_6_baseline(self):
        provider = OpenAIProvider(api_key="test-openai-key")
        create = AsyncMock(return_value=SimpleNamespace(output_text="天气日报"))

        try:
            with patch.object(provider.client.responses, "create", create):
                result = await provider.generate_report("系统提示", "天气 JSON")
        finally:
            await provider.aclose()

        self.assertEqual(result, "天气日报")
        request = create.await_args.kwargs
        self.assertEqual(request["model"], "gpt-5.6-sol")
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        self.assertEqual(request["text"], {"verbosity": "medium"})
        # 用户决策：默认不限制输出长度，交给提示词与 3800 字符安全截断兜底。
        self.assertNotIn("max_output_tokens", request)
        self.assertNotIn("temperature", request)


if __name__ == "__main__":
    unittest.main()
