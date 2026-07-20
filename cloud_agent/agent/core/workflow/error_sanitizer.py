from __future__ import annotations


def error_type(exc: BaseException) -> str:
    return exc.__class__.__name__


def sanitized_tool_error_text(operation: str, exc: BaseException) -> str:
    return f"{operation}澶辫触锛岃绋嶅悗閲嶈瘯銆俥rror_type={error_type(exc)}"


def sanitized_error_payload(operation: str, exc: BaseException) -> dict[str, str | None]:
    user_message = f"{operation}澶辫触锛岃绋嶅悗閲嶈瘯銆?"
    code = error_type(exc)
    return {
        "status": "error",
        "data": None,
        "user_message": user_message,
        "error_code": code,
        "message": user_message,
        "error_type": code,
    }
