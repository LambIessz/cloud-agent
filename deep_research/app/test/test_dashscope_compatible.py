"""Regression tests for the DashScope OpenAI-compatible clients."""

import json
import sys
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from mult_agents.dashscope_compatible import build_chat_model, build_embeddings


class _OpenAICompatibleHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "payload": payload,
            }
        )

        if self.path == "/v1/chat/completions":
            response = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": payload["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "mock response"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        elif self.path == "/v1/embeddings":
            inputs = payload["input"]
            if not isinstance(inputs, list):
                inputs = [inputs]
            response = {
                "object": "list",
                "model": payload["model"],
                "data": [
                    {"object": "embedding", "embedding": [0.1, 0.2, 0.3], "index": index}
                    for index, _ in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        else:
            self.send_error(404)
            return

        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format, *_args):
        return


@contextmanager
def _openai_compatible_server():
    _OpenAICompatibleHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAICompatibleHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_clients_call_openai_compatible_chat_and_embedding_endpoints(monkeypatch):
    with _openai_compatible_server() as base_url:
        monkeypatch.setenv("DASHSCOPE_COMPATIBLE_BASE_URL", base_url)
        monkeypatch.setenv("DASHSCOPE_API_KEY", "environment-key")

        chat = build_chat_model("qwen-plus", 0.2, "explicit-key")
        embeddings = build_embeddings("text-embedding-v1", "explicit-key")

        assert chat.invoke("hello").content == "mock response"
        assert embeddings.embed_query("hello") == [0.1, 0.2, 0.3]

    assert [request["path"] for request in _OpenAICompatibleHandler.requests] == [
        "/v1/chat/completions",
        "/v1/embeddings",
    ]
    assert {request["authorization"] for request in _OpenAICompatibleHandler.requests} == {
        "Bearer explicit-key"
    }


def test_clients_fall_back_to_environment_api_key(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "environment-key")
    monkeypatch.delenv("DASHSCOPE_COMPATIBLE_BASE_URL", raising=False)

    chat = build_chat_model("qwen-plus", 0.2, None)
    embeddings = build_embeddings("text-embedding-v1", None)

    assert chat.openai_api_key.get_secret_value() == "environment-key"
    assert embeddings.openai_api_key.get_secret_value() == "environment-key"
    assert chat.openai_api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert embeddings.openai_api_base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
