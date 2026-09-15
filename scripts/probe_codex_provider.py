"""Opt-in Codex health and one structured request, with a create-only receipt."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel

from research_system.runtime.platform.settings import Settings
from research_system.runtime.providers.codex_provider import CodexProvider
from research_system.runtime.providers.errors import CodexTurnError


class Probe(BaseModel):
    ok: bool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-model-call", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Receipt already exists")
    provider = CodexProvider(Settings.from_env(ROOT))
    started = monotonic()
    receipt = {"provider": "codex", "model": provider.settings.default_model, "model_calls": 0}
    try:
        receipt["health"] = provider.health()
        if not receipt["health"]["authenticated"]:
            raise CodexTurnError("codex_chatgpt_login_required")
        if provider.settings.default_model not in receipt["health"]["models"]:
            raise CodexTurnError("configured_model_not_available")
        receipt["status"] = "health_verified"
        if args.allow_model_call:
            request = {"system_prompt": "Return the requested JSON only. No tools.", "user_prompt": "Return ok true."}
            receipt["request_hash"] = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
            receipt["model_calls"] = 1
            result = provider.parse(**request, schema=Probe, timeout_seconds=60)
            receipt["result"] = result.model_dump()
            receipt["execution"] = provider.last_call_metadata
            receipt["status"] = "structured_model_call_verified" if result.ok is True else "unexpected_output"
    except Exception as exc:  # noqa: BLE001 — safe diagnostic boundary
        receipt.update(status="failed", error_type=type(exc).__name__, error_code=getattr(exc, "code", None))
    receipt["elapsed_seconds"] = round(monotonic()-started, 6)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(receipt, handle, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["status"].endswith("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
