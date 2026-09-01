from __future__ import annotations

import unittest

from research_system.runtime.core.tool_registry import _redact_arguments


class ToolEventRedactionTests(unittest.TestCase):
    def test_small_arguments_remain_inspectable(self) -> None:
        self.assertEqual(
            _redact_arguments({"symbol": "600519", "count": 2}),
            {"symbol": "600519", "count": 2},
        )

    def test_large_nested_argument_is_replaced_by_a_hash_receipt(self) -> None:
        redacted = _redact_arguments(
            {
                "request": {
                    "snapshots": [{"payload": "x" * 1_000} for _ in range(100)],
                    "api_key": "must-not-leak",
                }
            }
        )

        receipt = redacted["request"]
        self.assertTrue(receipt["_event_payload_omitted"])
        self.assertEqual(receipt["kind"], "dict")
        self.assertRegex(receipt["sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("must-not-leak", str(redacted))


if __name__ == "__main__":
    unittest.main()
