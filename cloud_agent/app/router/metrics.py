from fastapi import APIRouter, Depends
from fastapi.responses import Response

from core.workflow.metrics import render_prometheus_metrics
from security.metrics import require_metrics_access


router = APIRouter()


@router.get("/metrics")
async def metrics_endpoint(_: None = Depends(require_metrics_access)):
    return Response(
        content=render_prometheus_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
