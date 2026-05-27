import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import app_main
import service.chat_service as chat_service
from router.health import router as health_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(health_router)
    return TestClient(app)


def test_healthz_returns_liveness_without_sensitive_fields():
    response = _client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "cloud_agent",
    }

    text = response.text
    for forbidden in (
        "request_id",
        "user_id",
        "user_id_hash",
        "tenant_id",
        "session_id",
        "query",
        "prompt",
        "completion",
        "matched_question",
    ):
        assert forbidden not in text


def test_readyz_returns_503_before_agent_graph_initialization(monkeypatch):
    monkeypatch.setattr(chat_service, "graph", None)

    response = _client().get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "service": "cloud_agent",
        "checks": {
            "agent_graph": "not_ready",
        },
    }


def test_readyz_returns_ready_after_agent_graph_initialization(monkeypatch):
    monkeypatch.setattr(chat_service, "graph", object())

    response = _client().get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "cloud_agent",
        "checks": {
            "agent_graph": "ready",
        },
    }


def test_app_main_registers_root_health_routes():
    route_paths = {route.path for route in app_main.app.routes}

    assert "/healthz" in route_paths
    assert "/readyz" in route_paths
