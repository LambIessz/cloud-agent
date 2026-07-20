from __future__ import annotations

import json
from typing import Any

from .error_sanitizer import error_type


def build_tool_payload(
    status: str,
    *,
    data: Any = None,
    user_message: str = "",
    error_code: str = "",
    message: str | None = None,
    error_type_value: str = "",
) -> dict[str, Any]:
    payload = {
        "status": status,
        "data": data,
        "user_message": user_message,
        "error_code": error_code,
        "message": user_message if message is None else message,
        "error_type": error_type_value,
    }
    return payload


def dump_tool_payload(
    status: str,
    *,
    data: Any = None,
    user_message: str = "",
    error_code: str = "",
    message: str | None = None,
    error_type_value: str = "",
) -> str:
    return json.dumps(
        build_tool_payload(
            status,
            data=data,
            user_message=user_message,
            error_code=error_code,
            message=message,
            error_type_value=error_type_value,
        ),
        ensure_ascii=False,
    )


def success_tool_payload(
    data: Any = None,
    *,
    user_message: str = "",
    error_code: str = "",
) -> dict[str, Any]:
    return build_tool_payload(
        "success",
        data=data,
        user_message=user_message,
        error_code=error_code,
    )


def not_found_tool_payload(
    data: Any = None,
    *,
    user_message: str = "",
    error_code: str = "NO_MATCH",
) -> dict[str, Any]:
    return build_tool_payload(
        "not_found",
        data=data,
        user_message=user_message,
        error_code=error_code,
    )


def error_tool_payload(
    operation: str,
    exc: BaseException,
    *,
    user_message: str | None = None,
    error_code: str | None = None,
    data: Any = None,
) -> dict[str, Any]:
    code = error_code or error_type(exc)
    message = user_message or f"{operation}失败，请稍后重试。"
    return build_tool_payload(
        "error",
        data=data,
        user_message=message,
        error_code=code,
        message=message,
        error_type_value=code,
    )
