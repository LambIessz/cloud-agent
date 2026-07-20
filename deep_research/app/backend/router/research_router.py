import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from backend.schemas import ResearchRequest, ResearchResponse
from backend.security import REQUEST_BUDGET, resolve_authenticated_identity_from_request
from backend.service import WorkflowService, get_workflow_service


router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.post("/run", response_model=ResearchResponse)
async def run_research(
    request: Request,
    payload: ResearchRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> ResearchResponse:
    authenticated_identity = resolve_authenticated_identity_from_request(request)
    client_host = request.client.host if request.client and request.client.host else "unknown"
    request_key = ":".join(
        part
        for part in (
            authenticated_identity.user_id,
            authenticated_identity.tenant_id,
            payload.thread_id,
            client_host,
        )
        if part
    )
    async with REQUEST_BUDGET.slot(request_key):
        final = await workflow_service.run(
            query=payload.query,
            user_id=authenticated_identity.user_id or "default_user",
            thread_id=payload.thread_id,
            tenant_id=authenticated_identity.tenant_id or "default_tenant",
            max_iterations=payload.max_iterations,
            enable_memory=payload.enable_memory,
        )
    return ResearchResponse(
        query=payload.query,
        user_id=authenticated_identity.user_id or "default_user",
        thread_id=payload.thread_id,
        tenant_id=authenticated_identity.tenant_id or "default_tenant",
        final=final,
    )


@router.post("/stream")
async def stream_research(
    request: Request,
    payload: ResearchRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> StreamingResponse:
    authenticated_identity = resolve_authenticated_identity_from_request(request)
    client_host = request.client.host if request.client and request.client.host else "unknown"
    request_key = ":".join(
        part
        for part in (
            authenticated_identity.user_id,
            authenticated_identity.tenant_id,
            payload.thread_id,
            client_host,
        )
        if part
    )

    async def event_stream():
        async with REQUEST_BUDGET.slot(request_key):
            start_event = {
                "type": "status",
                "message": "task accepted; initializing multi-agent workflow",
            }
            yield f"data: {json.dumps(start_event, ensure_ascii=False)}\n\n"
            async for event in workflow_service.stream_events(
                query=payload.query,
                user_id=authenticated_identity.user_id or "default_user",
                thread_id=payload.thread_id,
                tenant_id=authenticated_identity.tenant_id or "default_tenant",
                max_iterations=payload.max_iterations,
                enable_memory=payload.enable_memory,
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
