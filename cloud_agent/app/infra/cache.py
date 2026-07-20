from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from app_config.settings import settings


logger = logging.getLogger(__name__)

COLLECTION_NAME = os.getenv(
    "CLOUD_AGENT_SEMANTIC_CACHE_COLLECTION",
    "qa_semantic_cache_v2",
)
EMBEDDING_DIM = 512
L1_SEMANTIC_DISTANCE_THRESHOLD = 0.08
METADATA_OUTPUT_FIELDS = [
    "question",
    "answer",
    "scope",
    "user_id",
    "estimated_prompt_tokens",
    "estimated_completion_tokens",
    "estimated_cost_usd",
    "model",
]


class _FallbackEmbeddings:
    def __init__(self, dimension: int = EMBEDDING_DIM) -> None:
        self._dimension = dimension

    def _embed(self, text: str) -> list[float]:
        seed = hashlib.sha256(str(text).encode("utf-8", errors="ignore")).digest()
        base = [
            int.from_bytes(seed[index : index + 2].ljust(2, b"\0"), "big") / 65535.0
            for index in range(0, len(seed), 2)
        ]
        if not base:
            base = [0.0]
        return (base * ((self._dimension // len(base)) + 1))[: self._dimension]

    async def aembed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class SemanticCache:
    def __init__(self) -> None:
        self._client: Any = None
        self._embeddings: Any = None
        self._available: bool = False

    async def initialize(self) -> None:
        if os.getenv("CLOUD_AGENT_SEMANTIC_CACHE_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
            logger.info("Semantic cache disabled by CLOUD_AGENT_SEMANTIC_CACHE_ENABLED")
            self._available = False
            return

        try:
            from pymilvus import MilvusClient

            cache_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent", "milvus_lite_cache.db")
            connect_kwargs: dict[str, Any] = {
                "uri": cache_db_path
            }

            self._client = MilvusClient(**connect_kwargs)
            try:
                from langchain_huggingface import HuggingFaceEmbeddings

                self._embeddings = HuggingFaceEmbeddings(
                    model_name="BAAI/bge-small-zh-v1.5",
                    model_kwargs={"device": "cpu"},
                    encode_kwargs={"normalize_embeddings": True},
                )
            except Exception as embedding_exc:
                logger.warning(
                    "Semantic cache embedding model unavailable (%s); using deterministic fallback embeddings.",
                    embedding_exc.__class__.__name__,
                )
                self._embeddings = _FallbackEmbeddings()
            self._ensure_collection()
            self._client.load_collection(COLLECTION_NAME)
            self._available = True
        except Exception as exc:
            logger.warning("Semantic cache init failed: %s", exc.__class__.__name__)
            self._available = False

    async def set_cache(
        self,
        query: str,
        response: str,
        user_id: str | None = None,
        scope: str = "public",
        *,
        estimated_prompt_tokens: int | None = None,
        estimated_completion_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        model: str | None = None,
        raise_on_error: bool = False,
    ) -> None:
        if not self._available:
            return
        normalized = self._normalize(query)
        owner = user_id or ""
        cache_scope = "user" if owner else scope
        try:
            embedding = await self._embeddings.aembed_query(normalized)
            safe_norm = normalized.replace('"', '\\"')
            safe_scope = cache_scope.replace('"', '\\"')
            safe_owner = owner.replace('"', '\\"')
            delete_filter = (
                f'question_norm == "{safe_norm}" and scope == "{safe_scope}" and user_id == "{safe_owner}"'
            )
            self._client.delete(collection_name=COLLECTION_NAME, filter=delete_filter)
            self._client.insert(
                collection_name=COLLECTION_NAME,
                data=[
                    {
                        "question": query.strip(),
                        "question_norm": normalized,
                        "answer": response,
                        "scope": cache_scope,
                        "user_id": owner,
                        "enabled": 1,
                        "estimated_prompt_tokens": self._metadata_int(
                            estimated_prompt_tokens
                        ),
                        "estimated_completion_tokens": self._metadata_int(
                            estimated_completion_tokens
                        ),
                        "estimated_cost_usd": self._metadata_float(estimated_cost_usd),
                        "model": self._metadata_model(model),
                        "embedding": embedding,
                    }
                ],
            )
        except Exception as exc:
            if raise_on_error:
                raise
            logger.warning("Semantic cache set failed: %s", exc.__class__.__name__)

    async def get_cache(self, query: str, user_id: str) -> dict[str, Any] | None:
        if not self._available:
            return None
        normalized = self._normalize(query)
        safe_norm = normalized.replace('"', '\\"')
        safe_user = user_id.replace('"', '\\"')

        user_filter = (
            f'enabled == 1 and question_norm == "{safe_norm}" and scope == "user" and user_id == "{safe_user}"'
        )
        public_filter = (
            f'enabled == 1 and question_norm == "{safe_norm}" and scope == "public"'
        )
        user_exact = self._query_one(user_filter)
        if user_exact:
            return self._cache_hit_payload(
                answer=user_exact["answer"],
                matched_question=user_exact["question"],
                level="L1_EXACT",
                distance=0.0,
                source=user_exact,
            )

        public_exact = self._query_one(public_filter)
        if public_exact:
            return self._cache_hit_payload(
                answer=public_exact["answer"],
                matched_question=public_exact["question"],
                level="L1_EXACT",
                distance=0.0,
                source=public_exact,
            )

        try:
            query_embedding = await self._embeddings.aembed_query(normalized)
            scoped_filter = (
                f'enabled == 1 and (scope == "public" or (scope == "user" and user_id == "{safe_user}"))'
            )
            results = self._client.search(
                collection_name=COLLECTION_NAME,
                data=[query_embedding],
                filter=scoped_filter,
                limit=1,
                output_fields=METADATA_OUTPUT_FIELDS,
            )
            if not results:
                return None
            hit = results[0][0] if results[0] else None
            if not hit:
                return None
            distance = float(hit.get("distance", 1.0))
            if distance > L1_SEMANTIC_DISTANCE_THRESHOLD:
                return None
            entity = hit.get("entity", {})
            return self._cache_hit_payload(
                answer=entity.get("answer", ""),
                matched_question=entity.get("question", ""),
                level="L1_SEMANTIC",
                distance=distance,
                source=entity,
            )
        except Exception as exc:
            logger.warning("Semantic cache get failed: %s", exc.__class__.__name__)
            return None

    @property
    def available(self) -> bool:
        return self._available

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(text.strip().lower().split())

    @staticmethod
    def _metadata_int(value: int | None) -> int:
        if isinstance(value, int) and value >= 0:
            return value
        return -1

    @staticmethod
    def _metadata_float(value: float | None) -> float:
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return -1.0

    @staticmethod
    def _metadata_model(value: str | None) -> str:
        if value is None:
            return ""
        return str(value).strip()[:128]

    @staticmethod
    def _non_negative_int_or_none(value: Any) -> int | None:
        if isinstance(value, int) and value >= 0:
            return value
        return None

    @staticmethod
    def _non_negative_float_or_none(value: Any) -> float | None:
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
        return None

    def _cache_hit_payload(
        self,
        *,
        answer: str,
        matched_question: str,
        level: str,
        distance: float,
        source: dict[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "answer": answer,
            "matched_question": matched_question,
            "level": level,
            "distance": distance,
        }
        prompt_tokens = self._non_negative_int_or_none(
            source.get("estimated_prompt_tokens")
        )
        if prompt_tokens is not None:
            payload["estimated_prompt_tokens"] = prompt_tokens
        completion_tokens = self._non_negative_int_or_none(
            source.get("estimated_completion_tokens")
        )
        if completion_tokens is not None:
            payload["estimated_completion_tokens"] = completion_tokens
        cost_usd = self._non_negative_float_or_none(source.get("estimated_cost_usd"))
        if cost_usd is not None:
            payload["estimated_cost_usd"] = cost_usd
        model = source.get("model")
        if isinstance(model, str) and model:
            payload["model"] = model
        return payload

    def _query_one(self, filter_expr: str) -> dict[str, Any] | None:
        try:
            rows = self._client.query(
                collection_name=COLLECTION_NAME,
                filter=filter_expr,
                output_fields=METADATA_OUTPUT_FIELDS,
                limit=1,
            )
            if rows:
                return rows[0]
            return None
        except Exception:
            return None

    def _ensure_collection(self) -> None:
        from pymilvus import DataType

        if self._client.has_collection(COLLECTION_NAME):
            return

        schema = self._client.create_schema()
        schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
        schema.add_field("question", DataType.VARCHAR, max_length=2048)
        schema.add_field("question_norm", DataType.VARCHAR, max_length=2048)
        schema.add_field("answer", DataType.VARCHAR, max_length=8192)
        schema.add_field("scope", DataType.VARCHAR, max_length=16)
        schema.add_field("user_id", DataType.VARCHAR, max_length=128)
        schema.add_field("enabled", DataType.INT8)
        schema.add_field("estimated_prompt_tokens", DataType.INT64)
        schema.add_field("estimated_completion_tokens", DataType.INT64)
        schema.add_field("estimated_cost_usd", DataType.DOUBLE)
        schema.add_field("model", DataType.VARCHAR, max_length=128)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type="IVF_FLAT",
            metric_type="COSINE",
            params={"nlist": 256},
        )

        self._client.create_collection(
            collection_name=COLLECTION_NAME,
            schema=schema,
            index_params=index_params,
        )


semantic_cache = SemanticCache()
