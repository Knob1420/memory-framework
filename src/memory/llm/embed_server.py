"""本地 embedding 服务（bge-m3，移植自 rag-clean api/embedding.py）。

独立进程运行（torch/FlagEmbedding 在 conda env memory，与 MinerU 共用；见 docs/environments.md）：
    EMBED_MODEL_PATH=/path/to/bge-m3 CUDA_VISIBLE_DEVICES=0 \
        conda run --no-capture-output -n memory uvicorn memory.llm.embed_server:app --port 8001

只保留 memory.EmbeddingClient 消费的部分：POST /v1/embeddings（OpenAI 兼容）+ /health。
砍掉 rag-clean 的 ModelScope/sentence-transformers 三级回退与 mock——路径错了就该报错。
语料侧编码不加检索指令（deriver 批量向量化是语料场景；查询侧指令将来在 search 里加前缀）。
"""

import logging
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger("memory.embed_server")

MODEL_PATH = os.environ.get(
    "EMBED_MODEL_PATH",
    "/home/zjlab/Documents/build_LLMs/NLP_course_hf/pretrain_model/BAAI/bge-m3",
)

app = FastAPI(title="memory embedding server")
_model = None


def _load():
    global _model
    if _model is not None:
        return _model
    import torch
    from FlagEmbedding import FlagModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("loading bge-m3 from %s (device=%s, fp16)", MODEL_PATH, device)
    _model = FlagModel(MODEL_PATH, use_fp16=True, device=device)
    log.info("model ready")
    return _model


class EmbeddingsRequest(BaseModel):
    model: str
    input: list[str]


class EmbeddingsResponse(BaseModel):
    data: list[dict]
    model: str
    usage: dict


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_PATH, "loaded": _model is not None}


@app.post("/v1/embeddings", response_model=EmbeddingsResponse)
def embeddings(req: EmbeddingsRequest):
    if not req.input:
        raise HTTPException(400, "input 不能为空")
    import numpy as np

    out = _load().encode(req.input)
    # 新版 FlagEmbedding 的 encode 不收 normalize_embeddings；BGEM3 变体返回 dict
    vecs = np.asarray(out["dense_vecs"] if isinstance(out, dict) else out, dtype=np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)  # L2 归一化（余弦=点积）
    return EmbeddingsResponse(
        data=[{"embedding": v.astype(float).tolist(), "index": i} for i, v in enumerate(vecs)],
        model=req.model,
        usage={"count": len(req.input)},
    )
