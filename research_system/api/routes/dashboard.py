from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from research_system.api.agent_os_dashboard_service import AgentOSDashboardService
from research_system.api.dependencies import get_agent_os
from research_system.api.errors import ApiError
from research_system.api.validation import safe_identifier


router = APIRouter(tags=["dashboards"])


def _dashboard_service(agent_os=Depends(get_agent_os)) -> AgentOSDashboardService:
    return AgentOSDashboardService(agent_os)


@router.get("/dashboard/{domain_id}/{view_id}")
async def dashboard_view(
    domain_id: str,
    view_id: str,
    service: AgentOSDashboardService = Depends(_dashboard_service),
) -> dict:
    resolved_domain = safe_identifier(domain_id, field_name="domain_id")
    resolved_view = safe_identifier(view_id, field_name="view_id")
    try:
        return await run_in_threadpool(
            service.view,
            domain_id=resolved_domain,
            view_id=resolved_view,
        )
    except KeyError as exc:
        raise ApiError(
            status_code=404,
            code="resource_not_found",
            message="The requested dashboard view was not found",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            status_code=500,
            code="dashboard_configuration_invalid",
            message=str(exc),
        ) from exc


__all__ = ["router"]
