from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from research_system.market_data.report_inputs import ReportInputError

from research_system.runtime.presentation.display import (
    localize_public_error_fields,
    localize_runtime_error,
)

logger = logging.getLogger(__name__)

_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")

_VALIDATION_MESSAGES = {
    "missing": "缺少必填字段",
    "extra_forbidden": "不允许提供该字段",
    "json_invalid": "JSON 格式无效",
    "string_type": "字段值必须是字符串",
    "string_unicode": "字段值必须是字符串",
    "string_pattern_mismatch": "字段格式不符合要求",
    "int_type": "字段值必须是整数",
    "int_parsing": "字段值必须是整数",
    "float_type": "字段值必须是数字",
    "float_parsing": "字段值必须是数字",
    "bool_type": "字段值必须是布尔值",
    "list_type": "字段值必须是数组",
    "dict_type": "字段值必须是对象",
}
_CONTEXTUAL_VALIDATION_MESSAGES = {
    "string_too_short": ("min_length", "字符串长度不能少于 {} 个字符"),
    "string_too_long": ("max_length", "字符串长度不能超过 {} 个字符"),
    "literal_error": ("expected", "字段值必须是以下选项之一：{}"),
    "greater_than_equal": ("ge", "字段值必须大于或等于 {}"),
    "greater_than": ("gt", "字段值必须大于 {}"),
    "less_than_equal": ("le", "字段值必须小于或等于 {}"),
    "less_than": ("lt", "字段值必须小于 {}"),
}
_CONTEXT_DEFAULTS = {"expected": "允许值"}


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
    app.add_exception_handler(ReportInputError, _handle_report_input_error)
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


async def _handle_report_input_error(request: Request, exc: ReportInputError) -> JSONResponse:
    return _error_response(
        request,
        status_code=409,
        code=exc.code,
        message=str(exc),
    )


async def _handle_request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    raw_errors = list(exc.errors())
    details = [
        {
            "location": list(error.get("loc") or ()),
            "message": _validation_error_message(error),
            "type": str(error.get("type") or "validation_error"),
        }
        for error in raw_errors
    ]
    invalid_json = any(str(error.get("type") or "") == "json_invalid" for error in raw_errors)
    return _error_response(
        request,
        status_code=400 if invalid_json else 422,
        code="invalid_json" if invalid_json else "validation_error",
        message="请求体包含无效的 JSON" if invalid_json else "请求参数校验失败",
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
        message="服务器发生未预期的错误",
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
    public_message = public_error_message(
        message,
        fallback=_default_error_message(code=code, status_code=status_code),
    )
    error: dict[str, Any] = {
        "code": code,
        "message": public_message,
    }
    if details is not None:
        error["details"] = jsonable_encoder(
            localize_public_error_fields(details, failed_context=True)
        )
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
    return {
        400: "请求格式错误",
        401: "需要身份认证",
        403: "没有访问权限",
        404: "未找到请求的资源",
        405: "不支持该请求方法",
        409: "请求与当前资源状态冲突",
        413: "请求内容过大",
        415: "不支持该媒体类型",
        422: "请求参数校验失败",
        429: "请求过于频繁，请稍后重试",
        500: "服务器内部错误",
        502: "上游服务请求失败",
        503: "服务暂时不可用",
        504: "上游服务响应超时",
    }.get(status_code, "HTTP 请求失败")


def public_error_message(value: Any, *, fallback: str = "请求处理失败") -> str:
    """Return a user-facing Chinese message without exposing raw internal errors."""
    message = str(value or "").strip()
    if message and _CHINESE_TEXT.search(message):
        return localize_runtime_error(message, fallback=fallback)
    return str(fallback or "请求处理失败").strip() or "请求处理失败"


def _default_error_message(*, code: str, status_code: int) -> str:
    messages = {
        "authentication_required": "需要提供 Bearer token",
        "invalid_token": "Bearer token 无效",
        "identity_header_conflict": "请求身份与已认证主体不一致",
        "insufficient_role": "当前认证主体缺少所需角色",
        "invalid_identifier": "标识符格式无效",
        "invalid_json": "请求体包含无效的 JSON",
        "validation_error": "请求参数校验失败",
        "resource_not_found": "未找到请求的资源",
        "nested_resource_not_found": "未找到请求的子资源",
        "internal_server_error": "服务器发生未预期的错误",
    }
    return messages.get(code, _http_status_message(status_code))


def _validation_error_message(error: dict[str, Any]) -> str:
    error_type = str(error.get("type") or "validation_error")
    if message := _VALIDATION_MESSAGES.get(error_type):
        return message
    contextual = _CONTEXTUAL_VALIDATION_MESSAGES.get(error_type)
    if contextual is not None:
        context = error.get("ctx") if isinstance(error.get("ctx"), dict) else {}
        context_key, template = contextual
        return template.format(context.get(context_key, _CONTEXT_DEFAULTS.get(context_key)))
    return "字段值无效"


__all__ = ["ApiError", "install_exception_handlers", "public_error_message"]
