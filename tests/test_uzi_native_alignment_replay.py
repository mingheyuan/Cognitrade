from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_uzi_native_alignment.py"
SPEC = importlib.util.spec_from_file_location("uzi_alignment_replay_test_module", SCRIPT)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


class NativeAlignmentReplayTests(unittest.TestCase):
    def test_business_differences_are_not_ignored(self):
        reference = {
            "as_of_date": "2026-09-02", "data": None, "_timeout": True,
            "error": "provider unavailable", "source": [{"url": "https://example.test/a"}],
            "score": 50, "verdict": "谨慎", "battle_plan": {"entry": "¥9.20"},
        }
        host = {
            "as_of_date": "2026-09-03", "data": {}, "_timeout": False,
            "error": "", "source": [{"url": "https://example.test/b"}],
            "score": 50.0, "verdict": "可以蹲一蹲", "battle_plan": {"entry": "¥10.00"},
        }
        paths = {row["path"] for row in replay.field_diff(reference, host)}
        self.assertEqual(paths, {"/as_of_date", "/data", "/_timeout", "/error", "/source/0/url",
                                 "/score", "/verdict", "/battle_plan/entry"})

    def test_missing_is_not_the_same_as_null_and_array_order_matters(self):
        changes = replay.field_diff({"items": ["a", "b"], "field": None}, {"items": ["b", "a"]})
        removed = next(row for row in changes if row["path"] == "/field")
        self.assertEqual(removed["kind"], "removed")
        self.assertTrue(removed["reference_present"])
        self.assertFalse(removed["host_present"])
        self.assertEqual(len(changes), 3)

    def test_explicit_empty_panel_does_not_fall_back_to_new_skeleton(self):
        computed = {"investors": [{"score": 50}]}
        self.assertEqual(replay._stage2_panel({}, computed), {})
        copied = replay._stage2_panel(None, computed)
        self.assertEqual(copied, computed)
        copied["investors"][0]["score"] = 99
        self.assertEqual(computed["investors"][0]["score"], 50)

    def test_nan_and_infinity_are_losslessly_tagged_not_filled(self):
        data = {"nan": float("nan"), "inf": float("inf"), "zero": 0, "unknown": None}
        value = json.loads(replay.canonical(data))
        self.assertEqual(value["nan"], {"$native_float": "nan"})
        self.assertEqual(value["inf"], {"$native_float": "inf"})
        self.assertIsNone(value["unknown"])
        self.assertEqual(value["zero"], 0)
        self.assertEqual(replay.canonical(data), replay.canonical(data))
        self.assertTrue(replay.field_diff(value, {**value, "nan": None}))

    def test_unknown_native_types_fail_instead_of_default_string_coercion(self):
        with self.assertRaises(TypeError):
            replay.canonical({"value": object()})

    def test_recorded_auxiliary_bypasses_only_io_and_records_missing(self):
        def original_top(*args, **kwargs):
            raise AssertionError("real provider must never be called")
        module = SimpleNamespace(_fetch_top_holdings=original_top, _fetch_all_holding_funds=original_top, ak=None)
        recording = {"top_holdings": {
            "000001": {"status": "recorded", "data": [{"股票代码": "688041", "占净值比例": 1.5}]},
            "000002": {"status": "recorded_empty", "data": []},
        }, "all_holding_funds": {}}
        before = replay.canonical(recording)
        calls = []
        with replay.recorded_auxiliary(module, recording, calls):
            rows = module._fetch_top_holdings("000001")
            self.assertEqual(rows[0]["股票代码"], "688041")
            rows[0]["占净值比例"] = 99
            self.assertEqual(module._fetch_top_holdings("000002"), [])
            self.assertEqual(module._fetch_top_holdings("missing"), [])
            self.assertEqual(module._fetch_all_holding_funds("688041"), [])
        self.assertEqual(replay.canonical(recording), before)
        self.assertIs(module._fetch_top_holdings, original_top)
        self.assertIs(module._fetch_all_holding_funds, original_top)
        self.assertEqual([row["status"] for row in calls], ["recorded", "recorded_empty", "unrecorded", "unrecorded"])

    def test_recorded_auxiliary_restores_io_even_after_failure(self):
        original = lambda: None
        module = SimpleNamespace(_fetch_top_holdings=original, _fetch_all_holding_funds=original)
        with self.assertRaisesRegex(RuntimeError, "test interruption"):
            with replay.recorded_auxiliary(module, {}, []):
                raise RuntimeError("test interruption")
        self.assertIs(module._fetch_top_holdings, original)

    def test_known_upstream_issue_is_not_a_diff_allowlist(self):
        data = {"features": {"ma_align": "非多头", "ma_bull_aligned": True, "pe_quantile_5y": 50},
                "raw_after_derivation": {"dimensions": {"10_valuation": {"data": {"pe_quantile": "—"}}}}}
        known = replay.known_upstream_issues({"business": data})
        self.assertEqual(len(known), 2)
        changed = {**data, "features": {**data["features"], "ma_bull_aligned": False}}
        self.assertEqual(replay.field_diff(data, changed)[0]["path"], "/features/ma_bull_aligned")

    def test_stale_native_counts_are_disclosed_without_rewriting_baseline(self):
        panel = {"long_active": 2, "investors": [{"signal": "neutral", "mandate": "long"},
                                                  {"signal": "skip", "mandate": "long"},
                                                  {"signal": "bearish", "mandate": "short"}]}
        before = replay.canonical(panel)
        issues = replay.known_upstream_issues({"business": {"panel": panel}})
        self.assertEqual(issues[0]["id"], "native_post_override_long_count_stale")
        self.assertEqual(issues[0]["actual_active"], 1)
        self.assertEqual(replay.canonical(panel), before)

    def test_review_excludes_only_runtime_timestamp_not_issue_or_dates(self):
        original = {"reviewed_at": "runtime", "as_of_date": "2026-09-02", "passed": False,
                    "issues": [{"severity": "warning", "issue": "native original", "suggested_fix": "native action"}]}
        result = replay._review_business(original)
        self.assertNotIn("reviewed_at", result)
        self.assertEqual(result["as_of_date"], original["as_of_date"])
        self.assertEqual(result["issues"], original["issues"])
        self.assertFalse(result["passed"])

    def test_baseline_output_cannot_overwrite_existing_result(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json"
            replay.write_new(path, {"baseline": True})
            with self.assertRaises(FileExistsError):
                replay.write_new(path, {"baseline": False})
            self.assertEqual(json.loads(path.read_text()), {"baseline": True})

    def test_extra_review_provenance_is_validated_not_blindly_ignored(self):
        value = {"source_commit": replay.PIN, "passed": False, "issues": [{"severity": "critical"}]}
        self.assertEqual(replay._review_business(value), {"passed": False, "issues": [{"severity": "critical"}]})
        with self.assertRaisesRegex(ValueError, "provenance"):
            replay._review_business({**value, "source_commit": "different-source"})

    def test_host_diagnostics_are_separate_but_their_business_effects_still_diff(self):
        original = {"panel": {"score": 5}, "host_checks": {"architecture_deltas": ["nullable guard"]}}
        business, diagnostics = replay._split_host_diagnostics(original)
        self.assertEqual(diagnostics, original["host_checks"])
        self.assertIn("host_checks", original)
        self.assertEqual(replay.field_diff({"panel": {"score": 4}}, business)[0]["path"], "/panel/score")

    def test_upstream_swallowed_network_exception_cannot_pass_alignment(self):
        self.assertIsNotNone(replay.worker_error({"error": None, "network_attempts": ["socket.connect"]}))
        self.assertIsNotNone(replay.worker_error({"error": None, "forbidden_writes": ["/production/cache"]}))
        self.assertIsNone(replay.worker_error({"error": None, "network_attempts": [], "forbidden_writes": []}))

    def test_reference_rejects_wrong_pin_before_import(self):
        with patch.object(replay, "_git_revision", return_value="650788c"):
            with self.assertRaisesRegex(ValueError, "Reference must be pinned"):
                replay.verify_pristine(Path("/unused"))


if __name__ == "__main__":
    unittest.main()
