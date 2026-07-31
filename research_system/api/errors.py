from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.details = details
        self.headers = dict(headers) if headers else None


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_request_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(Exception, _handle_unknown_error)


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(
        request,
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        details=exc.details,
        headers=exc.headers,
    )


async def _handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    raw_errors = list(exc.errors())
    details = [
        {
            "location": list(error.get("loc") or ()),
            "message": str(error.get("msg") or "Invalid value"),
            "type": str(error.get("type") or "validation_error"),
        }
        for error in raw_errors
    ]
    invalid_json = any(str(error.get("type") or "") == "json_invalid" for error in raw_errors)
    return _error_response(
        request,
        status_code=400 if invalid_json else 422,
        code="invalid_json" if invalid_json else "validation_error",
        message="Request body contains invalid JSON" if invalid_json else "Request validation failed",
        details={"errors": details},
    )


async def _handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    status_code = int(exc.status_code)
    detail = exc.detail
    if status_code >= 500:
        message = _http_status_message(status_code)
        details = None
    else:
        message = detail if isinstance(detail, str) and detail else _http_status_message(status_code)
        details = detail if isinstance(detail, (dict, list)) else None
    return _error_response(
        request,
        status_code=status_code,
        code=_http_error_code(status_code),
        message=str(message),
        details=details,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _handle_unknown_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled API exception for %s %s (type=%s, request_id=%s)",
        request.method,
        request.url.path,
        exc.__class__.__name__,
        _request_id(request),
    )
    return _error_response(
        request,
        status_code=500,
        code="internal_server_error",
        message="An unexpected server error occurred",
    )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    if details is not None:
        error["details"] = jsonable_encoder(details)
    request_id = _request_id(request)
    if request_id is not None:
        error["request_id"] = request_id
    response_headers = dict(headers or {})
    if request_id is not None:
        response_headers.setdefault("X-Request-ID", request_id)
    if request.url.path.startswith(("/api/v1", "/api/v2")):
        response_headers.setdefault("Cache-Control", "no-store")
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=response_headers,
    )


def _request_id(request: Request) -> str | None:
    state_request_id = str(getattr(request.state, "request_id", "") or "").strip()
    if state_request_id:
        return state_request_id[:128]
    header_request_id = str(request.headers.get("X-Request-ID") or "").strip()
    return header_request_id[:128] or None


def _http_error_code(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "authentication_required",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "request_too_large",
        415: "unsupported_media_type",
        429: "rate_limited",
        500: "internal_server_error",
        502: "upstream_failure",
        503: "service_unavailable",
    }.get(status_code, "http_error")


def _http_status_message(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP request failed"


__all__ = ["ApiError", "install_exception_handlers"]
