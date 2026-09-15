"""Read-only audit of fixed inputs; does not create a report or invoke a model."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from domain_packs.finance_ashare.subagents.runtime import (
    _ensure_requested_snapshot_records,
    _normalize_snapshot_record,
    _snapshot_artifact_content,
)
from domain_packs.finance_ashare.subagents.uzi_392_native_methods import build_uzi_raw
from domain_packs.finance_ashare.subagents.uzi_qualitative_deep_dive import _dimension_context
from domain_packs.finance_ashare.subagents.uzi_recovery import content_hash
from research_system.market_data.report_inputs import ReportInputError, slots_for_manifest, validate_bundle_only_input
from research_system.strategies.runtime_pins import deployed_prompt_hash


def audit(manifest: dict, *, expected_hash: str) -> dict:
    assert content_hash(manifest) == expected_hash, "source manifest content changed"
    results = []
    for item in manifest["items"]:
        symbol = item["symbol"]
        slots = slots_for_manifest(manifest, symbols=[symbol])
        safety = {"status": "passed"}
        try:
            validate_bundle_only_input(
                snapshots=slots["snapshots"], manifest=slots["report_data_manifest"],
                bundle=slots["market_data_bundle"], symbols=[symbol],
            )
        except ReportInputError as exc:
            if exc.code not in {"bundle_snapshot_future_price_fact", "bundle_snapshot_unproven_empty_result"}:
                raise
            # Audit may inspect an unsafe immutable historical artifact. It
            # must neither erase the finding nor execute it as a new report.
            safety = {"status": "blocked", "code": exc.code, "message": str(exc)}
        rows = [_normalize_snapshot_record(row) for row in slots["snapshots"]]
        requested_symbol = symbol.split(".")[0] if symbol.endswith((".SH", ".SZ", ".BJ")) else symbol
        complete = _ensure_requested_snapshot_records(
            rows, requested_types=[row["snapshot_type"] for row in rows],
            symbols=[requested_symbol], sector=None, intent="stock_check",
        )
        assert len(complete) == len(rows), "false missing placeholders created"
        actual = [_snapshot_artifact_content(row) for row in complete]
        for original, transported in zip(rows, actual, strict=True):
            for ref in original.get("source_refs") or []:
                assert ref in transported["source_refs"], "fixed reference was lost or altered"
        native_hash = content_hash(build_uzi_raw(rows, symbol))
        assert content_hash(build_uzi_raw(actual, symbol)) == native_hash, "native raw changed"
        dimensions = {}
        for dimension in ("3_macro", "13_policy", "7_industry", "15_events", "8_materials", "9_futures"):
            before = _dimension_context(rows, dimension)
            after = _dimension_context(actual, dimension)
            assert before["evidence_records"] == after["evidence_records"], f"{symbol}/{dimension} evidence changed"
            assert before["allowed_urls"] == after["allowed_urls"], f"{symbol}/{dimension} URLs changed"
            dimensions[dimension] = {
                "before_urls": len(before["allowed_urls"]), "after_urls": len(after["allowed_urls"]),
                "evidence_count": len(after["evidence_records"]),
                "evidence_records_hash": content_hash(after["evidence_records"]),
            }
        results.append({
            "symbol": symbol, "input_rows": len(rows), "output_rows": len(actual),
            "native_raw_hash": native_hash, "reference_transport_verified": True,
            "current_input_safety": safety,
            "qualitative_dimensions": dimensions,
        })
    assert content_hash(manifest) == expected_hash, "audit mutated the source manifest"
    return {
        "scope": "offline_frozen_input_transport_only", "source_manifest_id": manifest["manifest_id"],
        "source_manifest_content_hash": expected_hash, "source_unchanged": True,
        "old_execution_hash": (manifest.get("workflow_pins") or {}).get("prompt_hash"),
        "new_execution_hash": deployed_prompt_hash(), "stock_count": len(results),
        "business_fetch_calls": 0, "model_calls": 0, "holdings": results,
        "limitation": "Not a new report, native delivery approval, or trading replay.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--expected-hash", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(json.loads(args.manifest.read_text()), expected_hash=args.expected_hash)
    with args.output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
