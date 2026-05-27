from fastapi import APIRouter, Response

import service.chat_service as chat_service


router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "cloud_agent",
    }


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, object]:
    graph_ready = chat_service.graph is not None
    if not graph_ready:
        response.status_code = 503
        return {
            "status": "not_ready",
            "service": "cloud_agent",
            "checks": {
                "agent_graph": "not_ready",
            },
        }

    return {
        "status": "ready",
        "service": "cloud_agent",
        "checks": {
            "agent_graph": "ready",
        },
    }
