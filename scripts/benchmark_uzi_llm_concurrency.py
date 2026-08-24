#!/usr/bin/env python3
"""Probe bounded UZI structured-output concurrency against the configured LLM."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from statistics import median
import sys
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain_packs.finance_ashare.subagents.uzi_llm_support import (
    parse_uzi_structured,
)
from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.openai_provider import OpenAIProvider


class ProbePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    probe_id: int
    ok: Literal[True]


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * fraction), len(ordered) - 1)
    return ordered[index]


def _request(provider: OpenAIProvider, probe_id: int) -> dict[str, object]:
    started = monotonic()
    try:
        payload = parse_uzi_structured(
            provider,
            system_prompt=(
                "You are a concurrency health probe. Return only the requested "
                "structured payload without commentary."
            ),
            user_prompt=f"Return probe_id={probe_id} and ok=true.",
            schema=ProbePayload,
            temperature=0,
            reasoning_effort="low",
        )
        return {
            "probe_id": probe_id,
            "ok": payload.probe_id == probe_id and payload.ok is True,
            "duration_seconds": round(monotonic() - started, 3),
            "error": None,
        }
    except Exception as exc:  # pragma: no cover - exercised only by real providers
        return {
            "probe_id": probe_id,
            "ok": False,
            "duration_seconds": round(monotonic() - started, 3),
            "error": f"{exc.__class__.__name__}:{str(exc)[:300]}",
        }


def probe_level(settings: Settings, concurrency: int, waves: int) -> dict[str, object]:
    request_count = concurrency * waves
    provider = OpenAIProvider(
        settings.model_copy(
            update={
                "uzi_llm_max_concurrency": concurrency,
                "uzi_llm_request_timeout_seconds": min(
                    settings.uzi_llm_request_timeout_seconds,
                    120,
                ),
            }
        )
    )
    started = monotonic()
    with ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix=f"uzi-llm-probe-{concurrency}",
    ) as pool:
        futures = [
            pool.submit(_request, provider, probe_id)
            for probe_id in range(1, request_count + 1)
        ]
        results = [future.result() for future in as_completed(futures)]
    elapsed = monotonic() - started
    durations = [float(row["duration_seconds"]) for row in results]
    success_count = sum(row["ok"] is True for row in results)
    return {
        "concurrency": concurrency,
        "requests": request_count,
        "successes": success_count,
        "failures": request_count - success_count,
        "wall_seconds": round(elapsed, 3),
        "throughput_requests_per_second": round(request_count / elapsed, 4),
        "latency_seconds": {
            "min": round(min(durations), 3) if durations else 0.0,
            "median": round(median(durations), 3) if durations else 0.0,
            "p95": round(_percentile(durations, 0.95), 3),
            "max": round(max(durations), 3) if durations else 0.0,
        },
        "errors": [row["error"] for row in results if row["error"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", nargs="+", type=int, default=[1, 2, 4, 6])
    parser.add_argument("--waves", type=int, default=1)
    args = parser.parse_args()
    levels = list(dict.fromkeys(args.levels))
    if args.waves <= 0 or any(level <= 0 for level in levels):
        parser.error("levels and waves must be positive")
    settings = Settings.from_env(Path.cwd())
    if not settings.openai_api_key:
        parser.error("OPENAI_API_KEY is required")
    summary = {
        "model": settings.default_model,
        "service_tier": settings.openai_service_tier,
        "waves": args.waves,
        "levels": [probe_level(settings, level, args.waves) for level in levels],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all(row["failures"] == 0 for row in summary["levels"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
