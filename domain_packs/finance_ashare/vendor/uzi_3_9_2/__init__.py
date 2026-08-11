"""Pinned UZI-Skill capability and report-presentation migration.

The analysis capability and visible report presentation are fixed to UZI
3.9.4 commit ``0acf25122cabf493e7bc442faff62ceceb7f2f02``. Runtime access is provided by
``runtime.Uzi392FetcherRuntime``; callers must not execute the upstream CLI.
"""

from .runtime import (
    UZI_COMMAND_IDS,
    UZI_COMMAND_TREE_SHA256,
    UZI_ANALYST_ASSET_TREE_SHA256,
    UZI_FETCHER_IDS,
    UZI_ENVIRONMENT_LOCK,
    UZI_PURE_PYTHON_TREE_SHA256,
    UZI_REPORT_RENDERER_SHA256,
    UZI_REPORT_SOURCE_COMMIT,
    UZI_REPORT_TEMPLATE_SHA256,
    UZI_REPORT_VERSION,
    UZI_SOURCE_COMMIT,
    UZI_STANDALONE_SKILL_TREE_SHA256,
    Uzi392FetcherRuntime,
    verify_vendored_uzi_integrity,
)

__all__ = [
    "UZI_COMMAND_IDS",
    "UZI_COMMAND_TREE_SHA256",
    "UZI_ANALYST_ASSET_TREE_SHA256",
    "UZI_FETCHER_IDS",
    "UZI_ENVIRONMENT_LOCK",
    "UZI_PURE_PYTHON_TREE_SHA256",
    "UZI_REPORT_RENDERER_SHA256",
    "UZI_REPORT_SOURCE_COMMIT",
    "UZI_REPORT_TEMPLATE_SHA256",
    "UZI_REPORT_VERSION",
    "UZI_SOURCE_COMMIT",
    "UZI_STANDALONE_SKILL_TREE_SHA256",
    "Uzi392FetcherRuntime",
    "verify_vendored_uzi_integrity",
]
