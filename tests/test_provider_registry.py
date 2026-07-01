from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from research_system.runtime.provider_registry import OpenAIProvider
from research_system.runtime.settings import Settings


class _FakeStreamContext:
    def __init__(self, *, events, final_response) -> None:
        self._events = events
        self._final_response = final_response

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        return self._final_response


class _FakeResponsesClient:
    def __init__(self, *, create_responses=None, stream_parts=None) -> None:
        self._create_responses = list(create_responses or [])
        self._stream_parts = list(stream_parts or [])
        self.create_calls: list[dict] = []
        self.stream_calls: list[dict] = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if not self._create_responses:
            raise AssertionError("unexpected create call")
        return self._create_responses.pop(0)

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        if not self._stream_parts:
            raise AssertionError("unexpected stream call")
        part = self._stream_parts.pop(0)
        return _FakeStreamContext(events=part["events"], final_response=part["final_response"])


class _FakeClient:
    def __init__(self, responses) -> None:
        self.responses = responses


class ProviderRegistryTests(unittest.TestCase):
    def _settings(self) -> Settings:
        settings = Settings.from_env(Path("."))
        return settings.model_copy(update={"openai_api_key": "test-key"})

    def test_from_env_does_not_leak_dotenv_values_into_later_workspaces(self) -> None:
        root_settings = Settings.from_env(Path("."))
        self.assertTrue(bool(root_settings.openai_api_key))

        with tempfile.TemporaryDirectory() as temp_dir:
            isolated_settings = Settings.from_env(Path(temp_dir))

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
        self.assertIsNone(responses.create_calls[0].get("previous_response_id"))
        self.assertEqual(responses.create_calls[1].get("previous_response_id"), "resp_1")
        self.assertEqual(
            responses.create_calls[1].get("input"),
            OpenAIProvider._CONTINUATION_PROMPT,
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
        self.assertIsNone(responses.stream_calls[0].get("previous_response_id"))
        self.assertEqual(responses.stream_calls[1].get("previous_response_id"), "resp_1")
        self.assertEqual(
            responses.stream_calls[1].get("input"),
            OpenAIProvider._CONTINUATION_PROMPT,
        )
