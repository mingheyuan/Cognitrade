from __future__ import annotations

from dataclasses import asdict
from importlib.util import find_spec
from typing import Any

from research_system.adapters.vendors.provider_registry import (
    ProviderSpec,
    VendorProviderRegistry,
)
from research_system.market_data.dimension_contracts import DEFAULT_DIMENSION_REGISTRY
from research_system.market_data.historical import capability_for_dimension
from research_system.runtime.platform.settings import Settings


class MarketDataProviderCatalog:
    """Expose provider capability without ever returning credentials.

    A provider being configured only means that its dependency and required
    credential are present.  It is deliberately distinct from a successful
    query: only a persisted collection task result can prove that.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        registry: VendorProviderRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry or VendorProviderRegistry(settings)

    def list(self) -> list[dict[str, Any]]:
        return [self._public(provider) for provider in self.registry.list_all()]

    def get(self, provider_id: str) -> dict[str, Any] | None:
        provider = self.registry.get(str(provider_id or "").strip())
        return self._public(provider) if provider is not None else None

    def test_configuration(self, provider_id: str) -> dict[str, Any]:
        provider = self.registry.get(str(provider_id or "").strip())
        if provider is None:
            return {
                "provider_id": str(provider_id or ""),
                "ok": False,
                "status": "unknown_provider",
                "message": "未登记该数据源",
            }
        public = self._public(provider)
        if not provider.enabled:
            status = "disabled"
            message = "数据源适配器未启用"
        elif not public["dependency_installed"]:
            status = "dependency_missing"
            message = f"缺少依赖：{provider.dependency}"
        elif not public["credential_configured"]:
            status = "credential_missing"
            message = "所需凭据尚未配置"
        elif not public["policy_enabled"]:
            status = "policy_disabled"
            message = "敏感或浏览器数据源未获显式授权"
        else:
            status = "configuration_ready"
            message = "配置检查通过；实际可用性以显式采集任务为准"
        return {
            **public,
            "ok": status == "configuration_ready",
            "status": status,
            "message": message,
            "network_test_performed": False,
        }

    def _public(self, provider: ProviderSpec) -> dict[str, Any]:
        raw = asdict(provider)
        dependency_installed = not provider.dependency or find_spec(provider.dependency) is not None
        credential_configured = self._credential_configured(provider)
        policy_enabled = self._policy_enabled(provider)
        configured = bool(
            provider.enabled
            and dependency_installed
            and credential_configured
            and policy_enabled
        )
        dimension_by_type = {
            contract.snapshot_type: contract.dimension
            for contract in DEFAULT_DIMENSION_REGISTRY
        }
        historical_capabilities = {
            dim: capability_for_dimension(dimension_by_type.get(dim, dim)).strategy
            for dim in provider.dims
        }
        return {
            **raw,
            "dependency_installed": dependency_installed,
            "credential_configured": credential_configured,
            "policy_enabled": policy_enabled,
            "configured": configured,
            "supports_historical_reconstruction": any(
                strategy != "current_only"
                for strategy in historical_capabilities.values()
            ),
            "historical_capabilities": historical_capabilities,
        }

    def _credential_configured(self, provider: ProviderSpec) -> bool:
        if not provider.requires_key:
            if provider.source_id == "xueqiu":
                return bool(self.settings.xueqiu_cookie)
            return True
        if provider.provider_id.startswith("tushare_"):
            return bool(self.settings.tushare_token)
        if provider.provider_id.startswith("mx_api_"):
            return bool(self.settings.mx_api_key)
        return False

    def _policy_enabled(self, provider: ProviderSpec) -> bool:
        if provider.access != "browser":
            return True
        source_id = provider.source_id.lower()
        permitted = (
            self.settings.sensitive_sources_enabled
            and source_id in self.settings.sensitive_source_allowlist
        )
        if source_id == "xueqiu":
            permitted = permitted and bool(self.settings.xueqiu_cookie)
        if provider.provider_id != "xueqiu_contests":
            permitted = permitted and self.settings.browser_sources_enabled
        return bool(permitted)


__all__ = ["MarketDataProviderCatalog"]
