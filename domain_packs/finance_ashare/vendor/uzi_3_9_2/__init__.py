"""Pinned UZI-Skill 3.9.2 pure-capability migration.

The vendored source is fixed to commit
``22e65f2e0cee2cb41cb40fdbe391c9600b7fe359``.  Runtime access is provided by
``runtime.Uzi392FetcherRuntime``; callers must not execute the upstream CLI.
"""

from .runtime import (
    UZI_COMMAND_IDS,
    UZI_COMMAND_TREE_SHA256,
    UZI_FETCHER_IDS,
    UZI_PURE_PYTHON_TREE_SHA256,
    UZI_REPORT_TEMPLATE_SHA256,
    UZI_SOURCE_COMMIT,
    Uzi392FetcherRuntime,
    verify_vendored_uzi_integrity,
)

__all__ = [
    "UZI_COMMAND_IDS",
    "UZI_COMMAND_TREE_SHA256",
    "UZI_FETCHER_IDS",
    "UZI_PURE_PYTHON_TREE_SHA256",
    "UZI_REPORT_TEMPLATE_SHA256",
    "UZI_SOURCE_COMMIT",
    "Uzi392FetcherRuntime",
    "verify_vendored_uzi_integrity",
]
