from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from pydantic import BaseModel

from research_system.runtime.providers.openai_client_support import OpenAIClientSupport
from research_system.runtime.providers.openai_response_support import OpenAIResponseSupport
from research_system.runtime.providers.provider_registry import OpenAIProvider, ProviderRegistry
from research_system.runtime.platform.settings import Settings


class _FakeStreamContext:
    def __init__(self, *, events, final_response) -> None:
        self._events = events
        self._final_response = final_response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        return self._final_response


class _FakeResponsesClient:
    def __init__(self, *, create_responses=None, parse_responses=None, stream_parts=None) -> None:
        self._create_responses = list(create_responses or [])
        self._parse_responses = list(parse_responses or [])
        self._stream_parts = list(stream_parts or [])
        self.create_calls: list[dict] = []
        self.parse_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if not self._create_responses:
            raise AssertionError("unexpected create call")
        return self._create_responses.pop(0)

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        if not self._parse_responses:
            raise AssertionError("unexpected parse call")
        return self._parse_responses.pop(0)

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        if not self._stream_parts:
            raise AssertionError("unexpected stream call")
        part = self._stream_parts.pop(0)
        return _FakeStreamContext(events=part["events"], final_response=part["final_response"])


class _FakeClient:
    def __init__(self, responses) -> None:
        self.responses = responses
        self.with_options_calls: list[dict] = []

    def with_options(self, **kwargs):
        self.with_options_calls.append(kwargs)
        return self


class _StructuredPayload(BaseModel):
    value: str


class ProviderRegistryTests(unittest.TestCase):
    def _settings(self) -> Settings:
        settings = Settings.from_env(Path("."))
        return settings.model_copy(update={"openai_api_key": "test-key"})

    def test_from_env_does_not_leak_dotenv_values_into_later_workspaces(self) -> None:
        with (
            tempfile.TemporaryDirectory() as configured_dir,
            tempfile.TemporaryDirectory() as isolated_dir,
            patch.dict("os.environ", {}, clear=True),
        ):
            Path(configured_dir, ".env").write_text(
                "OPENAI_API_KEY=workspace-key\nOPENAI_BASE_URL=https://example.test/v1\n",
                encoding="utf-8",
            )
            configured_settings = Settings.from_env(Path(configured_dir))
            isolated_settings = Settings.from_env(Path(isolated_dir))

        self.assertEqual(configured_settings.openai_api_key, "workspace-key")
        self.assertEqual(configured_settings.openai_base_url, "https://example.test/v1")
        self.assertIsNone(isolated_settings.openai_api_key)
        self.assertIsNone(isolated_settings.openai_base_url)

    def test_complete_continues_after_max_output_tokens(self) -> None:
        first = SimpleNamespace(
            id="resp_1",
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_text="first half ",
        )
        second = SimpleNamespace(
            id="resp_2",
            status="completed",
            incomplete_details=None,
            output_text="second half",
        )
        responses = _FakeResponsesClient(create_responses=[first, second])
        provider = OpenAIProvider(self._settings())
        provider._client = _FakeClient(responses)  # type: ignore[assignment]

        text = provider.complete(
            system_prompt="sys",
            user_prompt="user",
            temperature=0.2,
            reasoning_effort="low",
        )

        self.assertEqual(text, "first half second half")
        self.assertEqual(len(responses.create_calls), 2)
        self.assertFalse(responses.create_calls[0]["store"])
        self.assertIsNone(responses.create_calls[0].get("previous_response_id"))
        self.assertEqual(responses.create_calls[1].get("previous_response_id"), "resp_1")
        self.assertEqual(
            responses.create_calls[1].get("input"),
            OpenAIProvider._CONTINUATION_PROMPT,
        )

    def test_complete_can_disable_sdk_retries_for_uzi_owned_recovery(self) -> None:
        final = SimpleNamespace(
            id="resp_1",
            status="completed",
            incomplete_details=None,
            output_text="done",
        )
        responses = _FakeResponsesClient(create_responses=[final])
        client = _FakeClient(responses)
        provider = OpenAIProvider(self._settings())
        provider._client = client  # type: ignore[assignment]

        text = provider.complete(
            system_prompt="sys",
            user_prompt="user",
            max_retries=0,
        )

        self.assertEqual(text, "done")
        self.assertEqual(client.with_options_calls, [{"max_retries": 0}])

    def test_parse_uses_responses_json_schema_protocol(self) -> None:
        payload = _StructuredPayload(value="grounded")
        response = SimpleNamespace(
            id="resp_structured",
            status="completed",
            incomplete_details=None,
            output_parsed=payload,
        )
        responses = _FakeResponsesClient(parse_responses=[response])
        client = _FakeClient(responses)
        provider = OpenAIProvider(self._settings())
        provider._client = client  # type: ignore[assignment]

        result = provider.parse(
            system_prompt="sys",
            user_prompt="user",
            schema=_StructuredPayload,
            reasoning_effort="low",
            timeout_seconds=321,
            max_retries=0,
        )

        self.assertEqual(result, payload)
        self.assertEqual(client.with_options_calls, [{"max_retries": 0}])
        self.assertEqual(responses.parse_calls[0]["text_format"], _StructuredPayload)
        self.assertEqual(responses.parse_calls[0]["reasoning"], {"effort": "low"})
        self.assertEqual(responses.parse_calls[0]["timeout"], 321.0)
        self.assertFalse(responses.parse_calls[0]["store"])

    def test_provider_registry_wires_default_provider(self) -> None:
        registry = ProviderRegistry(self._settings())

        provider = registry.default()

        self.assertIsInstance(provider, OpenAIProvider)
        self.assertIsInstance(provider.client_support, OpenAIClientSupport)
        self.assertIsInstance(provider.response_support, OpenAIResponseSupport)

    def test_openai_base_url_normalization_does_not_duplicate_v1(self) -> None:
        support = OpenAIClientSupport(SimpleNamespace())

        self.assertEqual(
            support.normalize_base_url("https://example.com/gateway"),
            "https://example.com/gateway/v1",
        )
        self.assertEqual(
            support.normalize_base_url("https://example.com/v1"),
            "https://example.com/v1",
        )
        self.assertEqual(
            support.normalize_base_url("https://example.com/v1/"),
            "https://example.com/v1",
        )

    def test_stream_complete_continues_after_max_output_tokens(self) -> None:
        first_final = SimpleNamespace(
            id="resp_1",
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_text="first half ",
        )
        second_final = SimpleNamespace(
            id="resp_2",
            status="completed",
            incomplete_details=None,
            output_text="second half",
        )
        responses = _FakeResponsesClient(
            stream_parts=[
                {
                    "events": [
                        SimpleNamespace(type="response.reasoning_summary_text.done", text="summary one"),
                        SimpleNamespace(type="response.output_text.delta", delta="first half "),
                    ],
                    "final_response": first_final,
                },
                {
                    "events": [
                        SimpleNamespace(type="response.reasoning_summary_text.done", text="summary two"),
                        SimpleNamespace(type="response.output_text.delta", delta="second half"),
                    ],
                    "final_response": second_final,
                },
            ]
        )
        provider = OpenAIProvider(self._settings())
        provider._client = _FakeClient(responses)  # type: ignore[assignment]
        deltas: list[str] = []
        summaries: list[str] = []

        text = provider.stream_complete(
            system_prompt="sys",
            user_prompt="user",
            on_text_delta=deltas.append,
            on_reasoning_summary=summaries.append,
            temperature=0.2,
            reasoning_effort="low",
        )

        self.assertEqual(text, "first half second half")
        self.assertEqual(deltas, ["first half ", "second half"])
        self.assertEqual(summaries, ["summary one", "summary two"])
        self.assertEqual(len(responses.stream_calls), 2)
        self.assertFalse(responses.stream_calls[0]["store"])
        self.assertIsNone(responses.stream_calls[0].get("previous_response_id"))
        self.assertEqual(responses.stream_calls[1].get("previous_response_id"), "resp_1")
        self.assertEqual(
            responses.stream_calls[1].get("input"),
            OpenAIProvider._CONTINUATION_PROMPT,
        )

    def test_default_reasoning_effort_is_forwarded_when_call_does_not_override_it(self) -> None:
        final = SimpleNamespace(
            id="resp_1",
            status="completed",
            incomplete_details=None,
            output_text="done",
        )
        responses = _FakeResponsesClient(create_responses=[final])
        settings = self._settings().model_copy(update={"openai_reasoning_effort": "xhigh"})
        provider = OpenAIProvider(settings)
        provider._client = _FakeClient(responses)  # type: ignore[assignment]

        self.assertEqual(provider.complete(system_prompt="sys", user_prompt="user"), "done")
        self.assertEqual(responses.create_calls[0]["reasoning"], {"effort": "xhigh"})
        self.assertEqual(responses.create_calls[0]["service_tier"], "default")

    def test_public_progress_block_is_extracted_without_leaking_into_answer_text(self) -> None:
        text = (
            '<progress>{"current":"Reading the requested file",'
            '"next":"Summarize the returned content"}</progress>\n'
            "The file contains the requested content."
        )

        self.assertEqual(
            OpenAIProvider._extract_public_progress(text),
            {
                "current": "Reading the requested file",
                "next": "Summarize the returned content",
            },
        )
        self.assertEqual(
            OpenAIProvider._strip_public_progress(text),
            "The file contains the requested content.",
        )
