from __future__ import annotations

import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.skills.uzi_source_snapshot.skill import MaterialsSnapshotSkill
from domain_packs.finance_ashare.tools import snapshot_collector


class UziMaterialFuturesLinkTests(unittest.TestCase):
    def test_latest_uzi_industry_mapping_includes_optical_material_contracts(self) -> None:
        self.assertEqual(
            MaterialsSnapshotSkill._material_contracts("光学光电子"),
            (("玻璃", "FG0"), ("铜", "CU0")),
        )

    def test_collector_runs_material_wave_before_futures_and_passes_lineage(self) -> None:
        events: list[tuple[str, object]] = []

        class MaterialSkill:
            def run(self, *, query: str, symbol: str):
                events.append(("materials", symbol))
                return {
                    "snapshot_type": "materials",
                    "symbol": symbol,
                    "snapshot_date": "2026-08-07",
                    "facts": {
                        "availability": "available",
                        "materials_detail": [{"name": "玻璃", "code": "FG0", "latest_price": 1234}],
                    },
                }

        class FuturesSkill:
            def run(self, *, query: str, symbol: str, dependencies=None):
                material = dict((dependencies or {}).get("materials") or {})
                events.append(("futures", material.get("snapshot_type")))
                row = dict((material.get("facts") or {}).get("materials_detail", [{}])[0])
                return {
                    "snapshot_type": "futures",
                    "symbol": symbol,
                    "facts": {
                        "availability": "available",
                        "contract_code": row.get("code"),
                        "dependency_lineage": {"used": bool(row)},
                    },
                }

        with patch.dict(
            snapshot_collector._SYMBOL_SKILLS,
            {"materials": MaterialSkill, "futures": FuturesSkill},
            clear=True,
        ):
            rows = snapshot_collector.collect_snapshots(
                query="分析光学行业材料与期货",
                symbols=["002273"],
                snapshot_types=["futures", "materials"],
                max_workers=2,
            )

        futures = next(row for row in rows if row["snapshot_type"] == "futures")
        self.assertEqual(events[0], ("materials", "002273"))
        self.assertIn(("futures", "materials"), events)
        self.assertEqual(futures["facts"]["contract_code"], "FG0")
        self.assertTrue(futures["facts"]["dependency_lineage"]["used"])


if __name__ == "__main__":
    unittest.main()
