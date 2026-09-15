"""A reserved background run must not deduplicate its own first execution."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from research_system.api.agent_os_run_service import AgentOSRunService
from research_system.runtime.agent_os import AgentOS
from research_system.runtime.platform.settings import Settings
from research_system.runtime.storage.jsonl import read_json
from research_system.schemas.protocol import RequestEnvelope


@pytest.fixture
def agent_os(tmp_path):
    settings = Settings.from_env(Path(__file__).resolve().parents[1]).model_copy(
        update={
            "runs_root": tmp_path / "runs",
            "memory_root": tmp_path / "memory",
            "domain_packs_root": tmp_path / "empty_packs",
            "workspace_root": tmp_path / "workspace",
            "codex_enabled": False,
            "openai_base_url": None,
        }
    )
    return AgentOS(settings)


def request():
    return RequestEnvelope(
        user_query="Who are you?",
        constraints={"background": True},
        idempotency_key="background-same-key",
        tenant_id="tenant-one",
        user_id="user-one",
    )


def test_reserved_worker_enters_execution_with_its_own_idempotency_key(agent_os):
    original = request()
    run_id = agent_os.prepare_run(original, run_id="reserved-worker")
    result = agent_os.handle_request(original, run_id=run_id)
    assert result.status == "completed"
    assert result.run_id == run_id
    assert read_json(agent_os.run_store.load_run_paths(run_id).root / "response.json")


def test_external_repeat_does_not_consume_reserved_worker(agent_os):
    original = request()
    run_id = agent_os.prepare_run(original, run_id="reserved-worker")
    repeated = agent_os.handle_request(original.model_copy(update={"request_id": "retry"}))
    assert repeated.status == "running"
    assert repeated.run_id == run_id
    assert agent_os.handle_request(original, run_id=run_id).status == "completed"


def test_api_background_repeated_request_does_not_reserve_another_run(agent_os):
    service = AgentOSRunService(agent_os)
    try:
        with patch.object(service, "_submit_background_worker") as submit:
            first = service.run(request())
            repeat = service.run(request().model_copy(update={"request_id": "retry"}))
        assert first.run_id == repeat.run_id
        assert submit.call_count == 1
        assert len(list(agent_os.settings.runs_root.iterdir())) == 1
    finally:
        service.close()


def test_real_background_worker_finishes_instead_of_returning_its_own_pending_view(agent_os):
    service = AgentOSRunService(agent_os)
    finished = Event()
    responses = []
    original_persist = service._persist_background_response

    def persisted(response):
        responses.append(response)
        original_persist(response)
        finished.set()

    try:
        with patch.object(service, "_persist_background_response", side_effect=persisted):
            accepted = service.run(request())
            assert finished.wait(10), "Background worker did not return"
        assert responses[0].status == "completed"
        assert responses[0].run_id == accepted.run_id
    finally:
        service.close()


def test_service_restart_adopts_and_executes_original_keyed_reservation(agent_os):
    original = request()
    run_id = agent_os.prepare_run(original, run_id="restart-reservation")
    recovered = AgentOSRunService(AgentOS(agent_os.settings))
    recovered.close()  # Joins the actual recovery worker, not a sleep-based poll.
    result = read_json(agent_os.run_store.load_run_paths(run_id).root / "response.json")
    assert result and result["status"] == "completed"
    assert result["run_id"] == run_id
    assert len(list(agent_os.settings.runs_root.iterdir())) == 1


def test_two_concurrent_posts_reserve_once_and_completed_replay_stays_idempotent(agent_os):
    service = AgentOSRunService(agent_os)
    try:
        with (
            patch.object(service, "_submit_background_worker") as submit,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first, second = list(executor.map(lambda _: service.run(request()), range(2)))
        assert first.run_id == second.run_id
        assert submit.call_count == 1
        original = RequestEnvelope.model_validate(read_json(
            agent_os.run_store.load_run_paths(first.run_id).request
        ))
        complete = agent_os.handle_request(original, run_id=first.run_id)
        repeat = service.run(request())
        assert repeat.model_dump() == complete.model_dump()
        assert len(list(agent_os.settings.runs_root.iterdir())) == 1
    finally:
        service.close()


def test_same_key_from_another_user_does_not_replay_private_run(agent_os):
    service = AgentOSRunService(agent_os)
    try:
        with patch.object(service, "_submit_background_worker"):
            first = service.run(request())
            second = service.run(request().model_copy(update={"user_id": "user-two"}))
        assert first.run_id != second.run_id
    finally:
        service.close()


def test_explicit_run_id_with_different_request_cannot_start_reservation(agent_os):
    original = request()
    run_id = agent_os.prepare_run(original, run_id="reserved-worker")
    repeat = agent_os.handle_request(original.model_copy(update={"request_id": "different"}), run_id=run_id)
    assert repeat.status == "running"
    assert agent_os.handle_request(original, run_id=run_id).status == "completed"


def test_only_one_owner_can_enter_before_turn_state_is_persisted(agent_os):
    original = request()
    run_id = agent_os.prepare_run(original, run_id="reserved-worker")
    entered, release = Event(), Event()
    handle = agent_os._handle_routed_request

    def delay(**kwargs):
        entered.set()
        assert release.wait(10)
        return handle(**kwargs)

    with (
        patch.object(agent_os, "_handle_routed_request", side_effect=delay) as routed,
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        first = executor.submit(agent_os.handle_request, original, run_id=run_id)
        try:
            assert entered.wait(10)
            assert agent_os.handle_request(original, run_id=run_id).status == "running"
            assert routed.call_count == 1
        finally:
            release.set()
        assert first.result(timeout=10).status == "completed"
