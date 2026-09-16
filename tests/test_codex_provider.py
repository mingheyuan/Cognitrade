import json
from pathlib import Path
from threading import Event, Timer
from time import monotonic
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers import codex_transport as transport
from research_system.runtime.providers.codex_provider import CodexProvider
from research_system.runtime.providers.errors import (
    CodexTurnError,
    LLMCancelledError,
    LLMDeadlineError,
    LLMProviderError,
    LLMUnavailableError,
    StructuredOutputError,
)
from research_system.runtime.providers.frozen_documents import FrozenDocuments
from research_system.runtime.providers.provider_registry import ProviderRegistry
from research_system.runtime.providers.structured_output import strict_schema
from tests.codex_fakes import sdk_factory


def test_reader_turn_keeps_tools_scoped_and_audits_actual_reads(sdk, monkeypatch):
    from research_system.runtime.providers import codex_frozen_tools
    async def start(client, bridge, **kwargs):
        bridge.thread_id = "thread-offline"
        sdk["handler"] = bridge
        sdk["reader_tools"] = bridge.session.tools
        return await client.thread_start(**kwargs)
    monkeypatch.setattr(codex_frozen_tools, "start_thread", start)
    sdk["tool_requests"] = [{"threadId": "thread-offline", "callId": "read-1", "tool": "frozen_list", "arguments": {"offset": 0}}]
    sdk["events"] = [("item/completed", {"item": {"type": "dynamicToolCall", "id": "read-1", "tool": "frozen_list"}}),
                     ("item/completed", {"item": {"type": "agentMessage", "text": '{"value":1}'}}),
                     ("turn/completed", {"turn": {"status": "completed"}})]
    p = provider()
    p.parse(system_prompt="fixed", user_prompt="read", schema=Value, frozen_corpus=FrozenDocuments([("x", "body")], input_hash="fixed"))
    assert sdk["tool_responses"][0]["success"]
    assert {row["name"] for row in sdk["reader_tools"]} == {"frozen_list", "frozen_search", "frozen_read"}
    assert p.last_call_metadata["frozen_read_audit"]["tool_calls"] == 1
    assert sdk["closed"] and sdk["stream_exited"]


class Value(BaseModel):
    value: int


@pytest.fixture
def sdk(monkeypatch):
    state = {}
    monkeypatch.setattr(transport, "AsyncCodex", sdk_factory(state))
    monkeypatch.setattr(transport, "runtime_config", lambda cwd: {"cwd": cwd})
    return state


def provider():
    return CodexProvider(SimpleNamespace(default_model="gpt-5.6-luna", codex_reasoning_effort="low",
                                         llm_request_timeout_seconds=2))


@pytest.mark.parametrize("method", ["parse", "parse_cancellable"])
def test_structured_stage_uses_selected_model_and_closes_runtime(sdk, method):
    p = provider()
    result = getattr(p, method)(system_prompt="frozen rules", user_prompt="frozen facts", schema=Value)
    assert result.value == 1
    assert sdk["thread"]["model"] == "gpt-5.6-luna"
    assert sdk["thread"]["base_instructions"] == "frozen rules"
    assert sdk["thread"]["ephemeral"] is True
    assert sdk["turn"][0] == "frozen facts"
    assert sdk["turn"][1]["output_schema"]["additionalProperties"] is False
    assert sdk["closed"] and sdk["stream_exited"]
    assert p.last_call_metadata["provider"] == "codex"


@pytest.mark.parametrize("auth", [None, "apiKey"])
def test_no_login_or_api_key_auth_is_rejected_without_turn(sdk, auth):
    sdk["auth"] = auth
    with pytest.raises(LLMUnavailableError, match="login_required"):
        provider().parse(system_prompt="s", user_prompt="u", schema=Value)
    assert "thread" not in sdk
    assert sdk["closed"]


@pytest.mark.parametrize("method", ["parse", "parse_cancellable"])
def test_validation_is_not_a_transport_retry(sdk, method):
    sdk["events"] = [("item/completed", {"item": {"type": "agentMessage", "text": '{"value":"invalid"}'}}),
                     ("turn/completed", {"turn": {"status": "completed"}})]
    with pytest.raises(StructuredOutputError) as exc:
        getattr(provider(), method)(system_prompt="s", user_prompt="u", schema=Value)
    assert isinstance(exc.value.__cause__, ValidationError)


@pytest.mark.parametrize("kind", ["commandExecution", "webSearch", "mcpToolCall", "fileChange", "collabAgentToolCall"])
def test_unexpected_tools_are_not_accepted_as_report_results(sdk, kind):
    sdk["events"] = [("item/completed", {"item": {"type": kind}})]
    with pytest.raises(CodexTurnError, match="unexpected_tool"):
        provider().parse(system_prompt="s", user_prompt="u", schema=Value)
    assert sdk["closed"]


@pytest.mark.parametrize("status", ["failed", "interrupted", "inProgress"])
def test_non_success_terminal_never_returns_partial_output(sdk, status):
    sdk["events"] = [("item/completed", {"item": {"type": "agentMessage", "text": '{"value":1}'}}),
                     ("turn/completed", {"turn": {"status": status}})]
    with pytest.raises(LLMProviderError):
        provider().parse(system_prompt="s", user_prompt="u", schema=Value)


def test_rerouting_is_not_silently_accepted(sdk):
    sdk["events"] = [("model/rerouted", {"toModel": "other"})]
    with pytest.raises(CodexTurnError, match="model_rerouted"):
        provider().parse(system_prompt="s", user_prompt="u", schema=Value)


@pytest.mark.parametrize("control", ["request", "deadline", "cancel"])
def test_actual_stream_exits_before_cancellation_returns(sdk, control):
    sdk["wait"] = True
    cancellation = Event()
    timer = Timer(0.05, cancellation.set)
    kwargs = {"timeout_seconds": 0.05} if control == "request" else (
        {"deadline_at": monotonic() + 0.05} if control == "deadline" else {"cancellation": cancellation})
    error = {"request": LLMProviderError, "deadline": LLMDeadlineError, "cancel": LLMCancelledError}[control]
    if control == "cancel":
        timer.start()
    try:
        with pytest.raises(error):
            provider().parse_cancellable(system_prompt="s", user_prompt="u", schema=Value, **kwargs)
    finally:
        if control == "cancel":
            timer.join()
    assert sdk["interrupted"] and sdk["closed"] and sdk["stream_exited"]


def test_preexpired_deadline_does_not_start_runtime(sdk):
    with pytest.raises(LLMDeadlineError):
        provider().parse_cancellable(system_prompt="s", user_prompt="u", schema=Value, deadline_at=monotonic()-1)
    assert not sdk


@pytest.mark.parametrize("with_events", [False, True])
def test_timeout_preserves_safe_trace_and_closed_runtime(sdk, with_events):
    secret = "private_reasoning_and_credentials_must_not_be_logged"
    sdk["events"] = ([("item/agentMessage/delta", {"delta": secret}),
        ("item/reasoning/textDelta", {"delta": secret}),
        ("thread/tokenUsage/updated", {"tokenUsage": {"total": {"inputTokens": 20, "outputTokens": 3,
                                                              "secret": secret}}}),
        (secret, {"secret": secret})] if with_events else [])
    sdk["wait_after_events"] = True
    p = provider()
    with pytest.raises(LLMProviderError):
        p.parse(system_prompt="s", user_prompt="u", schema=Value, timeout_seconds=0.05)
    metadata = p.last_call_metadata
    assert metadata["thread_id"] == "thread-offline" and metadata["turn_id"] == "turn-offline"
    assert metadata["status"] == "timed_out"
    trace = metadata["request_trace"]
    assert trace["phase"] == "streaming" and trace["runtime_closed"]
    assert trace["event_count"] == (4 if with_events else 0)
    assert trace["output_characters"] == (len(secret) if with_events else 0)
    if with_events:
        assert trace["last_event"] == "other"
        assert trace["token_usage"]["total"] == {"inputTokens": 20, "outputTokens": 3}
    assert secret not in json.dumps(metadata)
    assert sdk["closed"] and sdk["stream_exited"]


def test_terminal_event_finishes_without_waiting_for_stream_eof(sdk):
    sdk["wait_after_events"] = True
    p = provider()
    assert p.parse(system_prompt="s", user_prompt="u", schema=Value, timeout_seconds=0.2).value == 1
    assert p.last_call_metadata["request_trace"]["terminal_status"] == "completed"
    assert not sdk.get("interrupted") and sdk["closed"] and sdk["stream_exited"]


@pytest.mark.parametrize("late_status", ["interrupted", "completed"])
def test_late_completion_during_interrupt_cannot_erase_timeout_evidence(sdk, late_status):
    sdk["events"] = [("item/agentMessage/delta", {"delta": "unfinished private text"})]
    sdk["wait_after_events"] = True
    sdk["interrupt_events"] = [
        ("item/completed", {"item": {"type": "agentMessage", "text": '{"value": 1}'}}),
        ("turn/completed", {"turn": {"status": late_status}}),
    ]
    p = provider()
    with pytest.raises(LLMProviderError):
        p.parse(system_prompt="s", user_prompt="u", schema=Value, timeout_seconds=0.05)
    metadata = p.last_call_metadata
    trace, stopped = metadata["request_trace"], metadata["request_trace"]["stop_snapshot"]
    assert metadata["status"] == "timed_out"
    assert stopped["generation_state"] == "output_incomplete"
    assert stopped["last_event"] == "item/agentMessage/delta" and "terminal_status" not in stopped
    assert trace["terminal_status"] == late_status
    assert trace["runtime_closed"] and trace["interrupt_acknowledged"]
    assert "unfinished private text" not in json.dumps(metadata)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_deadlines_are_rejected_before_runtime(sdk, timeout):
    with pytest.raises(ValueError):
        provider().parse_cancellable(system_prompt="s", user_prompt="u", schema=Value, timeout_seconds=timeout)
    assert not sdk


def test_runtime_exception_does_not_expose_private_messages(sdk):
    sdk["raise"] = RuntimeError("token=private private-host")
    with pytest.raises(CodexTurnError) as exc:
        provider().parse(system_prompt="s", user_prompt="u", schema=Value)
    assert "private" not in str(exc.value)
    assert exc.value.__suppress_context__


def test_schema_normalization_preserves_business_fields_and_original():
    class Nested(BaseModel):
        data: Value
        optional: str | None = None
    schema = Nested.model_json_schema()
    result = strict_schema(schema)
    assert "additionalProperties" not in schema
    assert result["additionalProperties"] is False
    assert result["$defs"]["Value"]["additionalProperties"] is False
    assert result["required"] == ["data", "optional"]
    assert result["properties"]["optional"]["anyOf"] == schema["properties"]["optional"]["anyOf"]


def test_legacy_keys_cannot_select_old_provider(tmp_path):
    (tmp_path/".env").write_text("PROVIDER_NAME=openai\nOPENAI_API_KEY=private\nOPENAI_MODEL=old\n")
    settings = Settings.from_env(tmp_path)
    settings.validate()
    assert settings.provider_name == "codex" and settings.default_model == "gpt-5.6-luna"
    assert not hasattr(settings, "openai_api_key")
    assert isinstance(ProviderRegistry(settings).default(), CodexProvider)


def test_legacy_transport_modules_are_deleted():
    root = Path(__file__).resolve().parents[1]/"research_system/runtime/providers"
    assert not any((root/name).exists() for name in ("openai_provider.py", "openai_client_support.py", "openai_response_support.py"))


def test_tool_decisions_still_return_to_core(sdk):
    value = {"kind": "tool_calls", "message": "", "tool_calls": [{"tool_name": "core.read", "arguments_json": '{"id":1}'}]}
    sdk["events"] = [("item/completed", {"item": {"type": "agentMessage", "text": json.dumps(value)}}),
                     ("turn/completed", {"turn": {"status": "completed"}})]
    result = provider().complete_with_tools(system_prompt="s", user_prompt="u", tools=[{"name": "core.read"}])
    assert result["tool_calls"][0]["arguments"] == {"id": 1}
    with pytest.raises(StructuredOutputError):
        provider().complete_with_tools(system_prompt="s", user_prompt="u", tools=[{"name": "different"}])


def test_no_embedding_fallback():
    assert provider().supports_embeddings is False
    with pytest.raises(LLMUnavailableError):
        provider().embed_texts(texts=["report"])


def test_open_report_maps_keep_their_keys_and_original_validation(sdk):
    class Report(BaseModel):
        dimensions: dict[str, Value]

    original = Report.model_json_schema()
    assert strict_schema(original) is None
    assert isinstance(original["properties"]["dimensions"]["additionalProperties"], dict)
    sdk["events"] = [("item/completed", {"item": {"type": "agentMessage", "text": '{"dimensions":{"22_deep_methods":{"value":7}}}'}}),
                     ("turn/completed", {"turn": {"status": "completed"}})]
    result = provider().parse(system_prompt="native rules", user_prompt="frozen", schema=Report)
    assert result.dimensions["22_deep_methods"].value == 7
    assert sdk["turn"][1]["output_schema"] is None
    assert "additionalProperties" in sdk["turn"][0]


@pytest.mark.parametrize(("info", "code", "retryable"), [
    ("unauthorized", "unauthorized", False),
    ("usageLimitExceeded", "insufficient_quota", False),
    ("sessionBudgetExceeded", "insufficient_quota", False),
    ("rateLimitExceeded", "rate_limit_exceeded", True),
    ("httpConnectionFailed", "httpConnectionFailed", True),
    ("responseStreamDisconnected", "responseStreamDisconnected", True),
    ("serverOverloaded", "serverOverloaded", True),
    ("contextWindowExceeded", "contextWindowExceeded", False),
])
def test_sdk_error_code_reaches_stage_retry_policy(info, code, retryable):
    from domain_packs.finance_ashare.subagents.uzi_llm_support import (
        model_failure_record,
    )
    error = transport.turn_error({"codexErrorInfo": info, "message": "never include secret response text"})
    record = model_failure_record(error, stage="qualitative_A")
    assert record["provider_error_code"] == code
    assert record["provider_retryable"] is retryable
    assert "secret response text" not in str(record)
