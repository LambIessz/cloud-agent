import asyncio
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[2] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from infra.cache import COLLECTION_NAME, METADATA_OUTPUT_FIELDS, SemanticCache


class _Embeddings:
    async def aembed_query(self, _text):
        return [0.1, 0.2, 0.3]


class _Client:
    def __init__(self):
        self.deleted = []
        self.inserted = []
        self.query_rows = []
        self.search_results = []

    def delete(self, **kwargs):
        self.deleted.append(kwargs)

    def insert(self, **kwargs):
        self.inserted.append(kwargs)

    def query(self, **kwargs):
        self.last_query = kwargs
        return self.query_rows

    def search(self, **kwargs):
        self.last_search = kwargs
        return self.search_results


def _cache(client):
    cache = SemanticCache()
    cache._available = True
    cache._client = client
    cache._embeddings = _Embeddings()
    return cache


def test_set_cache_persists_optional_token_cost_metadata():
    client = _Client()
    cache = _cache(client)

    asyncio.run(
        cache.set_cache(
            " Question ",
            "answer",
            user_id="user_a",
            estimated_prompt_tokens=120,
            estimated_completion_tokens=80,
            estimated_cost_usd=0.00014,
            model="qwen-plus",
        )
    )

    assert client.deleted[0]["collection_name"] == COLLECTION_NAME
    assert client.inserted[0]["collection_name"] == COLLECTION_NAME
    row = client.inserted[0]["data"][0]
    assert row["question"] == "Question"
    assert row["question_norm"] == "question"
    assert row["scope"] == "user"
    assert row["user_id"] == "user_a"
    assert row["estimated_prompt_tokens"] == 120
    assert row["estimated_completion_tokens"] == 80
    assert row["estimated_cost_usd"] == 0.00014
    assert row["model"] == "qwen-plus"


def test_set_cache_uses_sentinel_values_when_metadata_missing_or_invalid():
    client = _Client()
    cache = _cache(client)

    asyncio.run(
        cache.set_cache(
            "Question",
            "answer",
            estimated_prompt_tokens=-1,
            estimated_completion_tokens=None,
            estimated_cost_usd=-0.1,
            model=None,
        )
    )

    row = client.inserted[0]["data"][0]
    assert row["scope"] == "public"
    assert row["user_id"] == ""
    assert row["estimated_prompt_tokens"] == -1
    assert row["estimated_completion_tokens"] == -1
    assert row["estimated_cost_usd"] == -1.0
    assert row["model"] == ""


def test_exact_cache_hit_returns_metadata_without_content_leak_fields():
    client = _Client()
    client.query_rows = [
        {
            "question": "secret matched question",
            "answer": "cached answer",
            "estimated_prompt_tokens": 120,
            "estimated_completion_tokens": 80,
            "estimated_cost_usd": 0.00014,
            "model": "qwen-plus",
        }
    ]
    cache = _cache(client)

    hit = asyncio.run(cache.get_cache("Question", "user_a"))

    assert client.last_query["output_fields"] == METADATA_OUTPUT_FIELDS
    assert hit == {
        "answer": "cached answer",
        "matched_question": "secret matched question",
        "level": "L1_EXACT",
        "distance": 0.0,
        "estimated_prompt_tokens": 120,
        "estimated_completion_tokens": 80,
        "estimated_cost_usd": 0.00014,
        "model": "qwen-plus",
    }


def test_semantic_cache_hit_returns_metadata():
    client = _Client()
    client.search_results = [
        [
            {
                "distance": 0.02,
                "entity": {
                    "question": "secret matched question",
                    "answer": "cached answer",
                    "estimated_prompt_tokens": 120,
                    "estimated_completion_tokens": 80,
                    "estimated_cost_usd": 0.00014,
                    "model": "qwen-plus",
                },
            }
        ]
    ]
    cache = _cache(client)

    hit = asyncio.run(cache.get_cache("Question", "user_a"))

    assert client.last_search["output_fields"] == METADATA_OUTPUT_FIELDS
    assert hit == {
        "answer": "cached answer",
        "matched_question": "secret matched question",
        "level": "L1_SEMANTIC",
        "distance": 0.02,
        "estimated_prompt_tokens": 120,
        "estimated_completion_tokens": 80,
        "estimated_cost_usd": 0.00014,
        "model": "qwen-plus",
    }
