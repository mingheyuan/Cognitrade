from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from tests.api_test_support import API_TOKEN, FakeRunService, FakeRunServiceFactory, auth_headers
from domain_packs.finance_ashare.holding_reports import StaleHoldingsConfirmationRequired
from research_system.api.app import create_app
from research_system.api.config import ApiConfig
from research_system.runtime.platform.settings import Settings


REPO_ROOT = Path(__file__).resolve().parents[1]


def batch_payload(batch_id: str = "01J00000000000000000000031") -> dict:
    return {
        "batch_id": batch_id,
        "source_run_id": "01J00000000000000000000001",
        "source_artifact_id": "fund-artifact-1",
        "fund_code": "110011",
        "fund_name": "测试股票基金",
        "fund_type": "股票型",
        "profile": "lite",
        "status": "running",
        "concurrency": 5,
        "holding_source": "direct_equity_disclosure",
        "is_fund_of_funds": False,
        "disclosure_date": "2026-06-30",
        "disclosure_age_days": 37,
        "stale_level": "current",
        "cancel_requested": False,
        "holdings": [],
        "counts": {"total": 0, "supported": 0, "queued": 0, "running": 0, "deliverable": 0, "failed": 0, "cancelled": 0, "unsupported": 0},
        "data_gap": [],
        "created_at": "2026-08-06T00:00:00Z",
        "updated_at": "2026-08-06T00:00:00Z",
        "completed_at": None,
    }


class FakeHoldingReportService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.raise_stale = False

    def close(self) -> None:
        pass

    def create_batch(self, source_run_id: str, **kwargs) -> dict:
        self.calls.append((source_run_id, kwargs))
        if self.raise_stale:
            raise StaleHoldingsConfirmationRequired({
                "disclosure_date": "2025-01-01",
                "disclosure_age_days": 582,
                "stale_level": "confirmation_required",
            })
        return batch_payload()

    def list_batches(self, **kwargs) -> list[dict]:
        self.calls.append(("list", kwargs))
        return [batch_payload()]

    def detail(self, batch_id: str, **kwargs) -> dict:
        self.calls.append(("detail", {"batch_id": batch_id, **kwargs}))
        return batch_payload(batch_id)

    def cancel(self, batch_id: str, **kwargs) -> dict:
        result = batch_payload(batch_id)
        result.update({"status": "completed_partial", "cancel_requested": True})
        return result

    def retry(self, batch_id: str, **kwargs) -> dict:
        self.calls.append(("retry", {"batch_id": batch_id, **kwargs}))
        return batch_payload(batch_id)

    def build_pdf_zip(self, batch_id: str, **kwargs) -> tuple[bytes, str, int]:
        self.calls.append(("zip", {"batch_id": batch_id, **kwargs}))
        return b"PK-test", "fund-110011-holding-reports-lite.zip", 3


class ApiV1FundHoldingReportsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        settings = Settings.from_env(Path(self.temp_dir.name))
        self.service = FakeHoldingReportService()
        self.app = create_app(
            settings=settings,
            api_config=ApiConfig(api_token=API_TOKEN),
            run_service_factory=FakeRunServiceFactory(FakeRunService()),
            fund_holding_report_factory=lambda *_args: self.service,
            repo_root=REPO_ROOT,
        )
        self.context = TestClient(self.app, raise_server_exceptions=False)
        self.client = self.context.__enter__()

    def tearDown(self) -> None:
        self.context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_create_validates_and_forwards_batch_options(self) -> None:
        source_run_id = "01J00000000000000000000001"
        response = self.client.post(
            f"/api/v1/runs/{source_run_id}/holding-reports",
            headers=auth_headers(),
            json={"fund_code": "110011", "profile": "deep", "confirm_stale": True, "force": True},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["batch_id"], batch_payload()["batch_id"])
        self.assertEqual(self.service.calls[0][0], source_run_id)
        self.assertEqual(self.service.calls[0][1]["profile"], "deep")
        self.assertTrue(self.service.calls[0][1]["force"])

    def test_stale_disclosure_returns_structured_conflict(self) -> None:
        self.service.raise_stale = True
        response = self.client.post(
            "/api/v1/runs/01J00000000000000000000001/holding-reports",
            headers=auth_headers(),
            json={"fund_code": "110011", "profile": "lite"},
        )

        self.assertEqual(response.status_code, 409)
        body = response.json()["error"]
        self.assertEqual(body["code"], "stale_holdings_confirmation_required")
        self.assertEqual(body["details"]["disclosure_age_days"], 582)

    def test_list_detail_cancel_retry_continue_and_zip(self) -> None:
        headers = auth_headers()
        source = "01J00000000000000000000001"
        batch_id = batch_payload()["batch_id"]
        self.assertEqual(self.client.get(f"/api/v1/runs/{source}/holding-report-batches", headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/holding-report-batches/{batch_id}", headers=headers).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/holding-report-batches/{batch_id}/cancel", headers=headers).json()["status"], "completed_partial")
        self.assertEqual(self.client.post(f"/api/v1/holding-report-batches/{batch_id}/retry", headers=headers, json={"symbols": ["600001"]}).status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/holding-report-batches/{batch_id}/continue", headers=headers, json={"symbols": []}).status_code, 200)
        archive = self.client.get(f"/api/v1/holding-report-batches/{batch_id}/reports.zip", headers=headers)
        self.assertEqual(archive.status_code, 200)
        self.assertEqual(archive.headers["x-cognitrade-report-count"], "3")
        self.assertIn("application/zip", archive.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
