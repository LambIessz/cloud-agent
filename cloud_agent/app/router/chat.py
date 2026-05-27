from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse
from schemas.chat import ChatRequest
from security.auth import resolve_authenticated_identity_from_request
from service.chat_service import stream_chat
from uuid import uuid4

router = APIRouter()

@router.post("/chat")
async def chat_endpoint(
    request: Request,
    chat_request: ChatRequest,
    x_user_id: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
):
    """
    处理多智能体聊天请求，并使用 SSE (Server-Sent Events) 返回流式响应。
    如果命中 L1 语义缓存，将直接返回缓存结果。
    否则进入 Agent 图编排流程。
    """
    authenticated_identity = resolve_authenticated_identity_from_request(
        request,
        debug_user_id=x_user_id,
        debug_tenant_id=x_tenant_id,
    )
    request_id = f"req_{uuid4().hex[:16]}"
    return StreamingResponse(
        stream_chat(
            chat_request.query,
            chat_request.user_id,
            chat_request.session_id,
            request_id=request_id,
            request_tenant_id=chat_request.tenant_id,
            authenticated_user_id=authenticated_identity.user_id,
            authenticated_tenant_id=authenticated_identity.tenant_id,
        ),
        media_type="text/event-stream"
    )
