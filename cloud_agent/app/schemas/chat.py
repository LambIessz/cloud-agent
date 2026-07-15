from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9_.:@-]+$"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=4000)
    user_id: Optional[str] = Field(
        default="user_1001",
        min_length=1,
        max_length=128,
        pattern=SAFE_IDENTIFIER_PATTERN,
    )
    tenant_id: Optional[str] = Field(
        default="default_tenant",
        min_length=1,
        max_length=128,
        pattern=SAFE_IDENTIFIER_PATTERN,
    )
    session_id: Optional[str] = Field(
        default="default_session",
        min_length=1,
        max_length=128,
        pattern=SAFE_IDENTIFIER_PATTERN,
    )

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must not be blank")
        return value

class ChatResponse(BaseModel):
    status: str
    reply: str
    user_id: str
    session_id: str
