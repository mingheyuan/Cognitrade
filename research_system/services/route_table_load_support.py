from __future__ import annotations

import json
from datetime import datetime, timezone

from research_system.adapters.storage.redis_route_store import RouteTableBackendError
from research_system.services.route_table_models import IntentRouteTable


class RouteTableLoadSupport:
    def __init__(
        self,
        *,
        backend,
        active_key: str,
        key_prefix: str,
        last_good_key: str,
        seed_defaults: bool,
        default_table: IntentRouteTable,
    ) -> None:
        self.backend = backend
        self.active_key = active_key
        self.key_prefix = key_prefix
        self.last_good_key = last_good_key
        self.seed_defaults = seed_defaults
        self.default_table = default_table

    def replace_table(self, table: IntentRouteTable) -> str:
        payload = table.model_dump_json(indent=2)
        version = table.version.strip() or "manual"
        self.backend.set_payload(f"{self.key_prefix}{version}", payload, nx=False)
        self.backend.set_payload(self.active_key, version, nx=False)
        self.backend.set_payload(self.last_good_key, payload, nx=False)
        return version

    def load_uncached(self) -> tuple[IntentRouteTable, str, str | None]:
        if self.backend is None:
            return self.default_table, "default", self._utc_timestamp()

        active_version = self._load_active_version()
        if active_version:
            active_table = self._load_version_payload(active_version)
            if active_table is not None:
                return active_table, "redis_active", self._utc_timestamp()

        last_good_table = self._load_last_good_payload()
        if last_good_table is not None:
            return last_good_table, "redis_last_good", self._utc_timestamp()

        return self.default_table, "default", self._utc_timestamp()

    def _load_active_version(self) -> str | None:
        try:
            active_version = self.backend.get_payload(self.active_key)
            if active_version is None and self.seed_defaults:
                active_version = self.default_table.version
                default_payload = self.default_table.model_dump_json(indent=2)
                self.backend.set_payload(f"{self.key_prefix}{active_version}", default_payload, nx=True)
                self.backend.set_payload(self.active_key, active_version, nx=True)
                self.backend.set_payload(self.last_good_key, default_payload, nx=True)
            return active_version
        except RouteTableBackendError:
            return None

    def _load_version_payload(self, version: str) -> IntentRouteTable | None:
        try:
            payload = self.backend.get_payload(f"{self.key_prefix}{version}")
            if not payload:
                return None
            table = IntentRouteTable.model_validate(json.loads(payload))
            self.backend.set_payload(self.last_good_key, payload, nx=False)
            return table
        except (RouteTableBackendError, ValueError):
            return None

    def _load_last_good_payload(self) -> IntentRouteTable | None:
        try:
            last_good_payload = self.backend.get_payload(self.last_good_key)
            if not last_good_payload:
                return None
            return IntentRouteTable.model_validate(json.loads(last_good_payload))
        except (RouteTableBackendError, ValueError):
            return None

    def _utc_timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat()
