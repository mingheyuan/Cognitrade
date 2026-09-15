"""Unit regressions must never consume the developer's logged-in Codex quota."""
import pytest
from openai_codex import AsyncCodex


@pytest.fixture(autouse=True)
def forbid_live_codex_in_unit_tests(monkeypatch):
    async def denied(*_args, **_kwargs):
        raise AssertionError("Unit tests must inject a fake Codex SDK; live probes are separate")
    monkeypatch.setattr(AsyncCodex, "__aenter__", denied)
