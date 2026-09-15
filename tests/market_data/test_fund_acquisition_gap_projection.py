from copy import deepcopy

import pytest
from sqlalchemy import select

from research_system.market_data.collection import CollectionJobError
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.job_diagnostics import (
    fund_gap_dimensions,
    fund_retryable_scope,
    project_fund_parent_status,
    summarize_fund_portfolio,
)
from research_system.market_data.models import (
    MarketDataCollectionJob,
    MarketDataCollectionTask,
)
from tests.market_data.test_fund_holding_collection import (
    HOLDING_SYMBOLS,
    _create_parent,
)
from tests.market_data.test_fund_holding_collection import (
    harness as harness,  # noqa: PLC0414 -- pytest fixture re-export
)


def _actual_portfolio_shape():
    dimensions = list(DEFAULT_DIMENSION_REGISTRY.dimension_ids)
    always_missing = {
        "3_macro",
        "13_policy",
        "4_peers",
        "14_moat",
        "18_trap",
        "19_contests",
    }
    proposals, tasks = [], []
    for index, symbol in enumerate(HOLDING_SYMBOLS):
        gaps = always_missing | ({"8_materials", "9_futures"} if index < 3 else set())
        proposals.append(
            {
                "asset_role": "holding",
                "symbol": symbol,
                # Native noncritical disclosure is not acquisition completeness.
                "missing_dimensions": ["19_contests"],
                "items": [
                    {"dimension": d, "status": "data_gap" if d in gaps else "available"}
                    for d in dimensions
                ],
            }
        )
        for dimension in dimensions:
            tasks.append(
                {
                    "id": f"{symbol}:{dimension}",
                    "job_id": "latest",
                    "symbol": symbol,
                    "dimension": dimension,
                    "attempt_count": 1,
                    "max_attempts": 3,
                    "status": "failed"
                    if dimension == "3_macro"
                    else "partial"
                    if dimension in gaps or dimension == "11_governance"
                    else "succeeded",
                    "retryable": dimension in {"3_macro", "11_governance"},
                    "last_error_code": "historical_provider_failure"
                    if dimension == "3_macro"
                    else "historical_partial"
                    if dimension == "11_governance"
                    else "historical_unavailable"
                    if dimension in gaps
                    else None,
                }
            )
    checkpoint = {
        "holdings": [{"symbol": symbol} for symbol in HOLDING_SYMBOLS],
        "child_job_ids": ["latest"],
        "publication": {"status": "pending_confirmation"},
        "publication_candidate": {
            "bundles": proposals,
            "missing_dimensions": [f"{s}:19_contests" for s in HOLDING_SYMBOLS],
        },
    }
    return checkpoint, tasks, dimensions


def test_actual_item_gaps_not_native_critical_missing_define_acquisition_counts():
    checkpoint, tasks, dimensions = _actual_portfolio_shape()
    untouched = deepcopy(checkpoint)
    gaps = fund_gap_dimensions(checkpoint)
    expected_gaps = {
        proposal["symbol"]: {
            item["dimension"]
            for item in proposal["items"]
            if item["status"] == "data_gap"
        }
        for proposal in checkpoint["publication_candidate"]["bundles"]
    }
    assert {s: set(ds) for s, ds in gaps.items()} == expected_gaps
    summary = summarize_fund_portfolio(
        checkpoint=checkpoint,
        child_tasks=tasks,
        dimensions_per_holding=len(dimensions),
        dimension_ids=dimensions,
        top_n=len(HOLDING_SYMBOLS),
    )
    expected_missing = sum(map(len, expected_gaps.values()))
    assert expected_missing == 66
    assert summary["total_slots"] == len(dimensions) * len(HOLDING_SYMBOLS) == 220
    assert summary["missing_slots"] == expected_missing
    assert summary["ready_slots"] == summary["total_slots"] - expected_missing == 154
    assert summary["retryable_slots"] == len(HOLDING_SYMBOLS) == 10
    assert summary["blocked_slots"] == expected_missing - len(HOLDING_SYMBOLS) == 56
    assert fund_retryable_scope(checkpoint=checkpoint, child_tasks=tasks) == {
        s: ["3_macro"] for s in HOLDING_SYMBOLS
    }
    assert fund_retryable_scope(
        checkpoint=checkpoint,
        child_tasks=tasks,
        symbols=[HOLDING_SYMBOLS[0]],
        dimensions=["3_macro", "11_governance"],
    ) == {HOLDING_SYMBOLS[0]: ["3_macro"]}
    assert checkpoint == untouched


@pytest.mark.parametrize("usable_status", ["available", "verified_empty"])
def test_failed_refresh_keeps_old_usable_evidence_but_missing_item_can_retry(
    usable_status,
):
    symbol = HOLDING_SYMBOLS[0]
    checkpoint = {
        "holdings": [{"symbol": symbol}],
        "child_job_ids": ["old", "latest"],
        "publication_candidate": {
            "bundles": [
                {
                    "asset_role": "holding",
                    "symbol": symbol,
                    "missing_dimensions": [],
                    "items": [
                        {"dimension": d, "status": usable_status}
                        for d in ["3_macro", "11_governance"]
                    ],
                }
            ]
        },
    }
    tasks = [
        {
            "symbol": symbol,
            "dimension": "3_macro",
            "job_id": "latest",
            "status": "failed",
            "retryable": True,
            "attempt_count": 1,
            "last_error_code": "historical_provider_failure",
        },
        {
            "symbol": symbol,
            "dimension": "11_governance",
            "job_id": "latest",
            "status": "partial",
            "retryable": True,
            "attempt_count": 1,
            "last_error_code": "historical_partial",
        },
    ]
    assert fund_retryable_scope(checkpoint=checkpoint, child_tasks=tasks) == {}
    summary = summarize_fund_portfolio(
        checkpoint=checkpoint, child_tasks=tasks, dimensions_per_holding=2, top_n=1
    )
    assert (
        summary["ready_slots"],
        summary["missing_slots"],
        summary["retryable_slots"],
    ) == (2, 0, 0)
    assert (
        project_fund_parent_status(
            status="succeeded", checkpoint=checkpoint, child_tasks=tasks
        )
        == "succeeded"
    )
    # Native report warnings/critical_missing are a separate input gate, not a
    # reason to recast an explicitly usable selected snapshot as uncollected.
    checkpoint["publication_candidate"]["bundles"][0]["missing_dimensions"] = [
        "3_macro"
    ]
    assert fund_retryable_scope(checkpoint=checkpoint, child_tasks=tasks) == {}
    assert not fund_gap_dimensions(checkpoint, child_tasks=tasks)
    checkpoint["publication_candidate"]["bundles"][0]["missing_dimensions"] = []
    # Without a selected usable item, the same failed provider request must
    # remain visible/retryable even when native missing_dimensions is empty.
    checkpoint["publication_candidate"]["bundles"][0]["items"] = [
        {"dimension": "11_governance", "status": "available"}
    ]
    assert fund_retryable_scope(checkpoint=checkpoint, child_tasks=tasks) == {
        symbol: ["3_macro"]
    }
    summary = summarize_fund_portfolio(
        checkpoint=checkpoint, child_tasks=tasks, dimensions_per_holding=2, top_n=1
    )
    assert (
        summary["ready_slots"],
        summary["missing_slots"],
        summary["retryable_slots"],
    ) == (1, 1, 1)
    assert (
        project_fund_parent_status(
            status="succeeded", checkpoint=checkpoint, child_tasks=tasks
        )
        == "partial"
    )
    # An old failed generation must not supersede a later successful one.
    tasks[0]["job_id"] = "old"
    tasks.append(
        {**tasks[0], "job_id": "latest", "status": "succeeded", "retryable": False}
    )
    assert fund_retryable_scope(checkpoint=checkpoint, child_tasks=tasks) == {}


def test_partial_candidate_does_not_infer_missing_items_are_ready():
    checkpoint = {
        "holdings": [{"symbol": HOLDING_SYMBOLS[0]}],
        "publication_candidate": {
            "bundles": [
                {
                    "symbol": HOLDING_SYMBOLS[0],
                    "items": [{"dimension": "0_basic", "status": "verified_empty"}],
                    "missing_dimensions": [],
                }
            ]
        },
    }
    summary = summarize_fund_portfolio(
        checkpoint=checkpoint, child_tasks=[], dimensions_per_holding=3, top_n=1
    )
    assert summary["ready_slots"] == 1
    assert summary["unassessed_slots"] == 2


@pytest.mark.parametrize("frozen", [False, True])
def test_report_ready_parent_keeps_source_retry_capability_but_frozen_rejects_mutation(
    harness, frozen
):
    database, skill, _adapter, _equity, coordinator = harness
    parent = _create_parent(coordinator, as_of_date=coordinator._local_today())
    coordinator.run_job_now(parent["id"])
    checkpoint, _tasks, _dimensions = _actual_portfolio_shape()
    with database.session() as session:
        row = session.get(MarketDataCollectionJob, parent["id"])
        persisted = deepcopy(row.checkpoint_json)
        persisted["publication_candidate"] = checkpoint["publication_candidate"]
        persisted["publication"] = {
            "status": "published" if frozen else "pending_confirmation"
        }
        row.checkpoint_json = persisted
        for task in session.scalars(
            select(MarketDataCollectionTask).where(
                MarketDataCollectionTask.job_id.in_(persisted["child_job_ids"])
            )
        ):
            if task.dimension == "3_macro":
                task.status = "failed"
                task.retryable = True
                task.attempt_count = 1
                task.last_error_code = "historical_provider_failure"
            elif task.dimension == "11_governance":
                task.status = "partial"
                task.retryable = True
                task.attempt_count = 1
                task.last_error_code = "historical_partial"
    detail = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert detail["portfolio_summary"]["ready_slots"] == 154
    assert detail["portfolio_summary"]["retryable_slots"] == 10
    assert detail["status"] == "partial"
    if frozen:
        assert detail["freeze_status"] == "frozen"
        assert detail["can_confirm_freeze"] is False
        with pytest.raises(CollectionJobError) as raised:
            coordinator.retry_failed(
                parent["id"],
                tenant_id="tenant",
                user_id="user",
                actor_id="actor",
                dimensions=["3_macro"],
                submit=False,
            )
        assert raised.value.code == "collection_retry_bundle_frozen"
        assert raised.value.status_code == 409
        with database.session() as session:
            assert (
                session.get(MarketDataCollectionJob, parent["id"]).checkpoint_json
                == persisted
            )
        return
    queued = coordinator.retry_failed(
        parent["id"],
        tenant_id="tenant",
        user_id="user",
        actor_id="actor",
        dimensions=["3_macro"],
        submit=False,
    )
    assert queued["checkpoint"]["retry_scope_by_symbol"] == {
        s: ["3_macro"] for s in HOLDING_SYMBOLS
    }
    assert "publication_candidate" not in queued["checkpoint"]
    coordinator.run_job_now(parent["id"])
    completed = coordinator.get_job(parent["id"], tenant_id="tenant", user_id="user")
    assert len(skill.calls) == 1
    with database.session() as session:
        child = session.get(
            MarketDataCollectionJob, completed["child_collection_job_ids"][-1]
        )
        assert child.dimensions_json == ["3_macro"]
        assert set(child.symbols_json) == set(HOLDING_SYMBOLS)
        assert child.mode == "force_all"
