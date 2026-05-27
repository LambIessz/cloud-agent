from fastapi import APIRouter
from fastapi.responses import Response

from core.workflow.metrics import render_prometheus_metrics


router = APIRouter()


@router.get("/metrics")
async def metrics_endpoint():
    return Response(
        content=render_prometheus_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
