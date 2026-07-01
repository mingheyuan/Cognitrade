from __future__ import annotations

import unittest

from research_system.services.status_payloads import (
    group_doctor_checks,
    render_doctor_check_line,
    status_header_fields,
)


class StatusPayloadsTests(unittest.TestCase):
    def test_status_header_fields_use_shared_order(self) -> None:
        self.assertEqual(
            status_header_fields(
                {
                    "project_root": "D:/repo",
                    "provider_name": "openai",
                    "default_model": "gpt-5.5",
                    "qdrant_mode": "local",
                    "qdrant_collection_name": "research_evidence",
                    "total_runs": 2,
                }
            ),
            [
                ("project_root", "D:/repo"),
                ("provider", "openai / model: gpt-5.5"),
                ("qdrant_mode", "local / collection: research_evidence"),
                ("total_runs", "2"),
            ],
        )

    def test_group_doctor_checks_groups_by_status(self) -> None:
        grouped = group_doctor_checks(
            [
                {"name": "env_file", "status": "passed"},
                {"name": "openai_api_key", "status": "failed"},
                {"name": "qdrant_local_dir", "status": "skipped"},
            ]
        )
        self.assertEqual([row["name"] for row in grouped["passed"]], ["env_file"])
        self.assertEqual([row["name"] for row in grouped["failed"]], ["openai_api_key"])
        self.assertEqual([row["name"] for row in grouped["skipped"]], ["qdrant_local_dir"])

    def test_render_doctor_check_line_prefers_detail_then_path(self) -> None:
        self.assertEqual(
            render_doctor_check_line({"name": "openai_api_key", "detail": "missing"}),
            "  openai_api_key  missing",
        )
        self.assertEqual(
            render_doctor_check_line({"name": "env_file", "path": "D:/repo/.env"}),
            "  env_file  D:/repo/.env",
        )


if __name__ == "__main__":
    unittest.main()
