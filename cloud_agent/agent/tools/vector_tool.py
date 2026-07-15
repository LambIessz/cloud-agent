import os
import re
from dotenv import load_dotenv
from langchain_core.tools import tool
from core.workflow.error_sanitizer import sanitized_tool_error_text

_milvus_fetch_patched = False

def _patch_pymilvus_fetch() -> None:
    global _milvus_fetch_patched
    if _milvus_fetch_patched:
        return

    from pymilvus import connections

    original_fetch = connections._fetch_handler

    def patched_fetch(alias):
        try:
            return original_fetch(alias)
        except Exception:
            from pymilvus.client.connection_manager import ConnectionManager
            mgr = ConnectionManager.get_instance()
            for mc in mgr._registry.values():
                if f"cm-{id(mc.handler)}" == alias:
                    return mc.handler
            for mc in mgr._dedicated.values():
                if f"cm-{id(mc.handler)}" == alias:
                    return mc.handler
            raise

    connections._fetch_handler = patched_fetch
    _milvus_fetch_patched = True

def patched_fetch(alias):
    """Compatibility shim kept for callers that imported this helper directly."""
    _patch_pymilvus_fetch()
    from pymilvus import connections
    try:
        return connections._fetch_handler(alias)
    except Exception:
        raise

dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path)

_milvus_instance = None

def _env_enabled(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def _keyword_fallback_search(query: str) -> str:
    mock_data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "mock_data")
    doc_path = os.path.join(mock_data_dir, "ecs_product_info.md")
    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return f"本地产品文档检索不可用: {exc.__class__.__name__}"

    query_terms = re.findall(r"[a-zA-Z0-9._-]+|[\u4e00-\u9fff]{2,}", query.lower())
    query_terms = [term for term in query_terms if len(term.strip()) >= 2]
    chunks = [chunk.strip() for chunk in re.split(r"\n(?=##|\d+\.|\*\*)", text) if chunk.strip()]
    scored = []
    for chunk in chunks:
        lower_chunk = chunk.lower()
        score = sum(1 for term in query_terms if term in lower_chunk)
        if score:
            scored.append((score, chunk))

    if not scored:
        scored = [(1, chunk) for chunk in chunks[:3]]

    snippets = []
    for _, chunk in sorted(scored, key=lambda item: item[0], reverse=True)[:3]:
        cleaned = re.sub(r"\s+", " ", chunk).strip()
        snippets.append(cleaned[:800])

    return "【来源: ecs_product_info.md】\n" + "\n\n".join(snippets)

def _get_milvus_store():
    global _milvus_instance
    if _milvus_instance is not None:
        return _milvus_instance

    _patch_pymilvus_fetch()
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_milvus import Milvus

    milvus_db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "milvus_lite_cloud.db")

    print(f"🔌 [Init] 正在连接 Milvus 向量数据库 (Lite模式): {milvus_db_path}")
    embeddings = HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-zh-v1.5",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )

    _milvus_instance = Milvus(
        embedding_function=embeddings,
        connection_args={"uri": milvus_db_path},
        collection_name="cloud_product_docs",
        auto_id=True,
        drop_old=False
    )
    return _milvus_instance

@tool
def query_vector_db(query: str) -> str:
    """
    通过语义搜索查询云产品的说明文档（RAG）。
    当用户询问大段的概念、操作步骤、详细规则（例如：退款规则、什么是专有网络VPC、如何创建实例）时，使用此工具。
    """
    if not _env_enabled("CLOUD_AGENT_VECTOR_SEARCH_ENABLED", True):
        return _keyword_fallback_search(query)

    try:
        store = _get_milvus_store()
        results = store.similarity_search_with_score(query, k=3)
        
        if not results:
            return "未在文档中检索到相关信息。"

        formatted_results = []
        for i, (doc, score) in enumerate(results):
            source = os.path.basename(doc.metadata.get('source', 'Unknown'))
            content = doc.page_content.strip()
            formatted_results.append(f"【来源: {source}】\n{content}")
            
        return "\n\n".join(formatted_results)
    except Exception as e:
        return sanitized_tool_error_text("查询向量数据库", e)
