from __future__ import annotations

import unittest

from research_system.services.methodologies.policy import (
    DEFAULT_RETRIEVAL_POLICY,
    MethodologyManifestService,
    RetrievalPolicyService,
    build_review_focus_notes,
    normalize_retrieval_policy,
    resolve_review_focus,
)


class MethodologiesPolicyRuntimeTests(unittest.TestCase):
    def test_manifest_service_export_is_available(self) -> None:
        self.assertTrue(callable(MethodologyManifestService))

    def test_retrieval_policy_service_export_is_available(self) -> None:
        self.assertTrue(callable(RetrievalPolicyService))

    def test_retrieval_policy_view_exports_are_available(self) -> None:
        self.assertTrue(bool(DEFAULT_RETRIEVAL_POLICY))
        self.assertTrue(callable(normalize_retrieval_policy))

    def test_review_focus_exports_are_available(self) -> None:
        self.assertTrue(callable(resolve_review_focus))
        self.assertTrue(callable(build_review_focus_notes))


if __name__ == "__main__":
    unittest.main()
