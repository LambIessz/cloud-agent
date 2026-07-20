import os
from pathlib import Path
from typing import Iterable


DEFAULT_FILE_SECRET_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "NEO4J_PASSWORD",
    "MILVUS_API_KEY",
    "OPENWEATHER_API_KEY",
    "CLOUD_AGENT_AUTH_JWT_SECRET",
    "CLOUD_AGENT_METRICS_TOKEN",
)


def _has_value(value: str | None) -> bool:
    return bool(value and value.strip())


def _read_secret_file(path: str) -> str | None:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def load_file_secrets(secret_names: Iterable[str] | None = None) -> list[str]:
    """
    Load SECRET_NAME_FILE values into SECRET_NAME environment variables.

    Direct environment variables win. This helper intentionally emits no logs:
    secret file paths, values, and OS error messages must not leak to logs or
    metrics.
    """
    loaded: list[str] = []
    for name in secret_names or DEFAULT_FILE_SECRET_ENV_VARS:
        if _has_value(os.getenv(name)):
            continue
        secret_file = os.getenv(f"{name}_FILE")
        if not _has_value(secret_file):
            continue
        value = _read_secret_file(str(secret_file))
        if value is None:
            continue
        os.environ[name] = value
        loaded.append(name)
    return loaded
