from __future__ import annotations

import unittest

from research_system.adapters.vendors.akshare_utils import coerce_numeric


class AkshareUtilsTests(unittest.TestCase):
    def test_coerce_numeric_handles_cn_units_and_percentages(self) -> None:
        self.assertEqual(coerce_numeric("5.93万亿"), 5.93 * 1_000_000_000_000.0)
        self.assertEqual(coerce_numeric("426.33亿"), 426.33 * 100_000_000.0)
        self.assertEqual(coerce_numeric("4302.00万"), 4302.00 * 10_000.0)
        self.assertAlmostEqual(coerce_numeric("29.86%") or 0.0, 0.2986)
        self.assertAlmostEqual(coerce_numeric("-4.9378%") or 0.0, -0.049378)

    def test_coerce_numeric_returns_none_for_unusable_values(self) -> None:
        self.assertIsNone(coerce_numeric(False))
        self.assertIsNone(coerce_numeric(""))
        self.assertIsNone(coerce_numeric("False"))
        self.assertIsNone(coerce_numeric("not_a_number"))


if __name__ == "__main__":
    unittest.main()
