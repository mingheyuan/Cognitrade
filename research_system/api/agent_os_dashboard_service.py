from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from research_system.runtime.agent_os import AgentOS
from research_system.runtime.core.artifact_board import ArtifactBoard


class AgentOSDashboardService:
    """Read-only DomainPack dashboard projections from canonical Agent OS state."""

    def __init__(self, agent_os: AgentOS) -> None:
        self.agent_os = agent_os

    def view(self, *, domain_id: str, view_id: str) -> dict[str, Any]:
        manifest = self.agent_os.registry.get(domain_id)
        views = manifest.metadata.get("dashboard_views") or {}
        config = views.get(view_id) if isinstance(views, dict) else None
        if not isinstance(config, dict):
            raise KeyError(f"unknown dashboard view: {domain_id}:{view_id}")
        resource_configs = config.get("resources") or {}
        if not isinstance(resource_configs, dict):
            raise ValueError(f"dashboard view resources must be an object: {domain_id}:{view_id}")

        resources: dict[str, dict[str, Any]] = {}
        for resource_id, resource_config in resource_configs.items():
            if not isinstance(resource_config, dict):
                raise ValueError(
                    f"dashboard resource must be an object: {domain_id}:{view_id}:{resource_id}"
                )
            resources[str(resource_id)] = self._resource(
                domain_id=domain_id,
                resource_config=resource_config,
            )
        return {
            "domain_id": domain_id,
            "view_id": view_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "resources": resources,
        }

    def _resource(
        self,
        *,
        domain_id: str,
        resource_config: dict[str, Any],
    ) -> dict[str, Any]:
        source = str(resource_config.get("source") or "").strip()
        if source == "artifacts":
            return self._artifact_resource(domain_id=domain_id, config=resource_config)
        if source == "memory":
            return self._memory_resource(domain_id=domain_id, config=resource_config)
        if source == "manifest":
            return {
                "source": "manifest",
                "value": resource_config.get("value"),
            }
        raise ValueError(f"unsupported dashboard resource source: {source or '<missing>'}")

    def _artifact_resource(self, *, domain_id: str, config: dict[str, Any]) -> dict[str, Any]:
        artifact_type = str(config.get("artifact_type") or "").strip()
        if not artifact_type:
            raise ValueError("artifact dashboard resource requires artifact_type")
        selection = str(config.get("selection") or "all").strip()
        content_path = str(config.get("content_path") or "").strip()
        item_key = str(config.get("item_key") or "").strip()
        artifacts = self._artifacts(domain_id=domain_id, artifact_type=artifact_type)
        if selection == "latest_artifact":
            artifacts = artifacts[:1]
        elif selection not in {"all", "latest_per_item_key"}:
            raise ValueError(f"unsupported artifact dashboard selection: {selection}")

        items: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for artifact in artifacts:
            value = self._content_value(artifact.content, content_path)
            values = value if isinstance(value, list) else [value]
            for item in values:
                if item is None:
                    continue
                if selection == "latest_per_item_key":
                    key_value = self._content_value(item, item_key)
                    key = str(key_value or "").strip()
                    if key and key in seen_keys:
                        continue
                    if key:
                        seen_keys.add(key)
                items.append({"data": item, "provenance": self._artifact_provenance(artifact)})

        total_count = len(items)
        limit = self._limit(config)
        if limit is not None:
            items = items[:limit]
        return {
            "source": "artifacts",
            "artifact_type": artifact_type,
            "count": len(items),
            "total_count": total_count,
            "items": items,
        }

    def _memory_resource(self, *, domain_id: str, config: dict[str, Any]) -> dict[str, Any]:
        layer = str(config.get("layer") or "policy").strip()
        if layer not in {"episodic", "domain", "policy"}:
            raise ValueError(f"unsupported dashboard memory layer: {layer}")
        statuses = {str(value) for value in list(config.get("statuses") or [])}
        records = self.agent_os.memory.list(layer=layer, domain_id=domain_id)
        if statuses:
            records = [record for record in records if record.status in statuses]
        records.sort(key=lambda record: record.created_at, reverse=True)
        limit = self._limit(config)
        if limit is not None:
            records = records[:limit]
        return {
            "source": "memory",
            "layer": layer,
            "count": len(records),
            "items": [
                {
                    "data": record.content,
                    "provenance": {
                        "memory_id": record.memory_id,
                        "memory_type": record.memory_type,
                        "status": record.status,
                        "created_at": record.created_at.isoformat(),
                        "source_run_ids": list(record.source_run_ids),
                        "source_artifact_ids": list(record.source_artifact_ids),
                        "evaluations": [
                            evaluation.model_dump(mode="json")
                            for evaluation in record.evaluations
                        ],
                    },
                }
                for record in records
            ],
        }

    def _artifacts(self, *, domain_id: str, artifact_type: str) -> list[Any]:
        artifacts: list[Any] = []
        for row in self.agent_os.run_store.list_runs():
            run_id = str(row.get("run_id") or "").strip()
            if not run_id:
                continue
            try:
                board_path = self.agent_os.run_store.load_run_paths(run_id).artifact_board
            except FileNotFoundError:
                continue
            if not board_path.exists():
                continue
            for artifact in ArtifactBoard(board_path).list_artifacts(
                thread_id=None,
                artifact_type=artifact_type,
                status="active",
            ):
                if artifact.producer.domain_id == domain_id:
                    artifacts.append(artifact)
        return sorted(artifacts, key=lambda artifact: artifact.created_at, reverse=True)

    @staticmethod
    def _content_value(value: Any, path: str) -> Any:
        if not path:
            return value
        current = value
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, list) and part.isdigit():
                index = int(part)
                current = current[index] if 0 <= index < len(current) else None
            else:
                return None
            if current is None:
                return None
        return current

    @staticmethod
    def _limit(config: dict[str, Any]) -> int | None:
        if "limit" not in config:
            return None
        limit = int(config["limit"])
        if limit < 1 or limit > 100:
            raise ValueError("dashboard resource limit must be between 1 and 100")
        return limit

    @staticmethod
    def _artifact_provenance(artifact) -> dict[str, Any]:
        return {
            "artifact_id": artifact.artifact_id,
            "artifact_type": artifact.artifact_type,
            "schema_version": artifact.schema_version,
            "artifact_version": artifact.artifact_version,
            "run_id": artifact.run_id,
            "thread_id": artifact.thread_id,
            "request_id": artifact.request_id,
            "created_at": artifact.created_at.isoformat(),
            "confidence": artifact.confidence,
            "risk_flags": list(artifact.risk_flags),
            "source_refs": list(artifact.source_refs),
            "producer": artifact.producer.model_dump(mode="json"),
        }


__all__ = ["AgentOSDashboardService"]
