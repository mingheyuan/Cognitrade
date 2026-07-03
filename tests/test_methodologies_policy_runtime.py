from __future__ import annotations

import unittest

from research_system.services.methodologies.methodology_manifest_service import (
    MethodologyManifestService as PackageMethodologyManifestService,
)
from research_system.services.methodologies.retrieval_policy_service import (
    RetrievalPolicyService as PackageRetrievalPolicyService,
)
from research_system.services.methodologies.retrieval_policy_view import (
    DEFAULT_RETRIEVAL_POLICY as package_default_retrieval_policy,
    normalize_retrieval_policy as package_normalize_retrieval_policy,
)
from research_system.services.methodologies.review_focus_support import (
    build_review_focus_notes as package_build_review_focus_notes,
    resolve_review_focus as package_resolve_review_focus,
)
from research_system.services.methodologies.policy import (
    DEFAULT_RETRIEVAL_POLICY as package_policy_default_retrieval_policy,
    MethodologyManifestService as PackagePolicyMethodologyManifestService,
    RetrievalPolicyService as PackagePolicyRetrievalPolicyService,
    build_review_focus_notes as package_policy_build_review_focus_notes,
    normalize_retrieval_policy as package_policy_normalize_retrieval_policy,
    resolve_review_focus as package_policy_resolve_review_focus,
)
from research_system.services.methodology_manifest_service import MethodologyManifestService
from research_system.services.retrieval_policy_service import RetrievalPolicyService
from research_system.services.retrieval_policy_view import DEFAULT_RETRIEVAL_POLICY, normalize_retrieval_policy
from research_system.services.review_focus_support import build_review_focus_notes, resolve_review_focus


class MethodologiesPolicyRuntimeTests(unittest.TestCase):
    def test_manifest_service_top_level_export_matches_package_export(self) -> None:
        self.assertIs(MethodologyManifestService, PackageMethodologyManifestService)
        self.assertIs(PackageMethodologyManifestService, PackagePolicyMethodologyManifestService)

    def test_retrieval_policy_service_top_level_export_matches_package_export(self) -> None:
        self.assertIs(RetrievalPolicyService, PackageRetrievalPolicyService)
        self.assertIs(PackageRetrievalPolicyService, PackagePolicyRetrievalPolicyService)

    def test_retrieval_policy_view_top_level_exports_match_package_exports(self) -> None:
        self.assertIs(DEFAULT_RETRIEVAL_POLICY, package_default_retrieval_policy)
        self.assertIs(normalize_retrieval_policy, package_normalize_retrieval_policy)
        self.assertIs(package_default_retrieval_policy, package_policy_default_retrieval_policy)
        self.assertIs(package_normalize_retrieval_policy, package_policy_normalize_retrieval_policy)

    def test_review_focus_top_level_exports_match_package_exports(self) -> None:
        self.assertIs(resolve_review_focus, package_resolve_review_focus)
        self.assertIs(build_review_focus_notes, package_build_review_focus_notes)
        self.assertIs(package_resolve_review_focus, package_policy_resolve_review_focus)
        self.assertIs(package_build_review_focus_notes, package_policy_build_review_focus_notes)


if __name__ == "__main__":
    unittest.main()
