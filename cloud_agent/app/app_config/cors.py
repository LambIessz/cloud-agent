import os


DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def get_cors_origins(raw_origins: str | None = None) -> list[str]:
    raw_value = os.getenv("CLOUD_AGENT_CORS_ORIGINS", "") if raw_origins is None else raw_origins
    origins = [origin.strip() for origin in raw_value.split(",") if origin.strip()]
    if not origins:
        return list(DEFAULT_CORS_ORIGINS)
    if "*" in origins:
        raise ValueError("CLOUD_AGENT_CORS_ORIGINS cannot include wildcard '*' with credentials enabled")
    return origins
