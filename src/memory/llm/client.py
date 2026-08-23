"""模型客户端封装：全项目唯一允许触达外部模型 API 的地方（decisions.md 铁律）。

- LLMClient:      chat 走 OpenAI 兼容 API（大模型）
- EmbeddingClient: embedding 走本地部署（OpenAI 兼容 /v1/embeddings，vLLM/TEI 等）
P1 补 schema 强制解析与解析重试。
"""

import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from memory.config import Config


@dataclass
class LLMResult:
    content: str | None
    parsed: Any | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    model: str = ""


class LLMClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = OpenAI(api_key=cfg.llm_api_key, base_url=cfg.llm_base_url)

    def chat(self, messages: list[dict], model: str | None = None) -> LLMResult:
        model = model or self.cfg.llm_model
        t0 = time.monotonic()
        resp = self._client.chat.completions.create(model=model, messages=messages)
        return LLMResult(
            content=resp.choices[0].message.content,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            duration_ms=int((time.monotonic() - t0) * 1000),
            model=model,
        )


class EmbeddingClient:
    """本地部署的 embedding 服务（OpenAI 兼容端点）。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = OpenAI(
            api_key=cfg.llm_api_key or "local",  # 本地服务通常不校验 key
            base_url=cfg.embedding_base_url or cfg.llm_base_url,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.cfg.embedding_model, input=texts)
        vecs = [d.embedding for d in resp.data]
        if len(vecs[0]) != self.cfg.embedding_dim:
            raise ValueError(
                f"embedding 维度 {len(vecs[0])} != 配置 {self.cfg.embedding_dim}，"
                "写库前拦截（decisions: 换模型=重建向量表）"
            )
        return vecs


class FakeLLM:
    """测试替身：CI 无 key，演化引擎全链路可测。"""

    def __init__(self, canned: dict | None = None):
        self.canned = canned or {}

    def chat(self, messages: list[dict], model: str | None = None) -> LLMResult:
        return LLMResult(content="ok", model="fake")


class FakeEmbedding:
    """测试替身：8 维零向量（维度由测试里 Config 配成一致）。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]
