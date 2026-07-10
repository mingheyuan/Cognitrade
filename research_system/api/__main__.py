from __future__ import annotations

from pathlib import Path

import uvicorn

from research_system.api.config import ApiConfig


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = ApiConfig.from_env(repo_root)
    config.validate()
    uvicorn.run(
        "research_system.api.app:app",
        host=config.host,
        port=config.port,
        log_level=config.log_level,
        reload=config.reload,
        workers=1,
    )


if __name__ == "__main__":
    main()
