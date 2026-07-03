from __future__ import annotations


def build_provider_checks(provider_health: dict) -> list[dict]:
    checks: list[dict] = []
    for row in provider_health.get("provider_rows", []):
        checks.append(
            {
                "name": f"provider_chain_{row.get('dim')}",
                "status": "passed" if row.get("provider_count", 0) >= 1 else "failed",
                "detail": (
                    f"providers={','.join(row.get('provider_ids') or [])}; "
                    f"fallback={'yes' if row.get('has_fallback') else 'no'}"
                ),
            }
        )
    network_profile = provider_health.get("network_profile") or {}
    checks.extend(
        [
            {
                "name": "network_domestic",
                "status": "passed" if network_profile.get("domestic_reachable") else "failed",
                "detail": str(network_profile.get("domestic") or []),
            },
            {
                "name": "network_overseas",
                "status": "passed" if network_profile.get("overseas_reachable") else "skipped",
                "detail": str(network_profile.get("overseas") or []),
            },
        ]
    )
    return checks


__all__ = ["build_provider_checks"]
