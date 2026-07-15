from __future__ import annotations


def error_type(exc: BaseException) -> str:
    return exc.__class__.__name__


def sanitized_tool_error_text(operation: str, exc: BaseException) -> str:
    return f"{operation}失败，请稍后重试。error_type={error_type(exc)}"


def sanitized_error_payload(operation: str, exc: BaseException) -> dict[str, str]:
    return {
        "status": "error",
        "message": f"{operation}失败，请稍后重试。",
        "error_type": error_type(exc),
    }
