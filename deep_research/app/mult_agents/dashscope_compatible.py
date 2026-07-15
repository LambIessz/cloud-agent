"""DashScope clients through its OpenAI-compatible API."""

import os

from langchain_openai import ChatOpenAI, OpenAIEmbeddings


DEFAULT_DASHSCOPE_COMPATIBLE_BASE_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1"
)


def _api_key(value: str | None) -> str | None:
    return value or os.getenv("DASHSCOPE_API_KEY")


def _base_url() -> str:
    return os.getenv(
        "DASHSCOPE_COMPATIBLE_BASE_URL",
        DEFAULT_DASHSCOPE_COMPATIBLE_BASE_URL,
    ).rstrip("/")


def build_chat_model(model: str, temperature: float, api_key: str | None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=_api_key(api_key),
        base_url=_base_url(),
    )


def build_embeddings(model: str, api_key: str | None) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=model,
        api_key=_api_key(api_key),
        base_url=_base_url(),
    )
