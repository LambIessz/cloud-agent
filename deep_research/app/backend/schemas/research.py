from pydantic import BaseModel, ConfigDict, Field, field_validator


SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9_.:@-]+$"


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=4000)
    user_id: str | None = Field(default=None, max_length=128, pattern=SAFE_IDENTIFIER_PATTERN)
    thread_id: str = Field(default="default_thread", max_length=128, pattern=SAFE_IDENTIFIER_PATTERN)
    tenant_id: str | None = Field(default=None, max_length=128, pattern=SAFE_IDENTIFIER_PATTERN)
    max_iterations: int | None = Field(default=None, ge=1, le=6)
    enable_memory: bool | None = None

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value


class ResearchResponse(BaseModel):
    query: str
    user_id: str
    thread_id: str
    tenant_id: str
    final: str
