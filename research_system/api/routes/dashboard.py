from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from research_system.api.agent_os_dashboard_service import AgentOSDashboardService
from research_system.api.dependencies import ApiPrincipal, get_agent_os, require_principal
from research_system.api.errors import ApiError, public_error_message
from research_system.api.validation import safe_identifier
from research_system.runtime.presentation.display import localize_public_error_fields


router = APIRouter(tags=["dashboards"])


def _dashboard_service(agent_os=Depends(get_agent_os)) -> AgentOSDashboardService:
    return AgentOSDashboardService(agent_os)


def _scope_kwargs(
    service: AgentOSDashboardService,
    principal: ApiPrincipal | None,
) -> dict[str, str]:
    if principal is None or not principal.authenticated:
        return {}
    if not getattr(service, "identity_scoped", False):
        return {}
    return {
        "user_id": str(principal.user_id or ""),
        "tenant_id": str(principal.tenant_id or ""),
    }


@router.get("/dashboard/{domain_id}/{view_id}")
async def dashboard_view(
    domain_id: str,
    view_id: str,
    service: AgentOSDashboardService = Depends(_dashboard_service),
    principal: ApiPrincipal = Depends(require_principal),
) -> dict:
    resolved_domain = safe_identifier(domain_id, field_name="domain_id")
    resolved_view = safe_identifier(view_id, field_name="view_id")
    try:
        result = await run_in_threadpool(
            service.view,
            domain_id=resolved_domain,
            view_id=resolved_view,
            **_scope_kwargs(service, principal),
        )
        return localize_public_error_fields(result)
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message="未找到请求的仪表盘视图",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=500,
            code="dashboard_configuration_invalid",
            message=public_error_message(exc, fallback="仪表盘配置无效"),
        ) from exc


__all__ = ["router"]
