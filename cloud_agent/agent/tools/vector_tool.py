import os
import json
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_milvus import Milvus
from pymilvus import connections
from langchain_core.tools import tool

# ==============================================================================
# 修复 pymilvus 2.6.x 与 langchain-milvus 0.3.x 之间的兼容性问题
# ==============================================================================
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
# ==============================================================================

dotenv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(dotenv_path)

_milvus_instance = None

def _get_milvus_store():
    global _milvus_instance
    if _milvus_instance is not None:
        return _milvus_instance

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
        return f"查询向量数据库时发生错误: {str(e)}"
