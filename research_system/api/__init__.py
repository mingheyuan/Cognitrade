"""FastAPI transport for the Cognitrade research runtime.

The package surface is lazy so importing a leaf service (for example the
Hikyuu runner) does not eagerly construct the complete FastAPI application.
"""

from typing import Any

__all__ = ["app", "create_app"]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    from research_system.api.app import app, create_app

    return app if name == "app" else create_app
