"""采集端点：/events（事件流）+ /ingest_doc（文件）——契约 docs/http-api.md。

transport 层纪律：解析 → 调服务/storage → 透传。不 SQL、不 LLM、不重复校验 workspace
（编排门已做，从 request.state.workspace 取）。
"""

import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from memory.ingestion.models import KNOWN_KINDS, EventsRequest
from memory.ingestion.service import store_events

log = logging.getLogger("memory.events")
router = APIRouter()

_REQUIRED = File(...)  # 模块级单例，避免 ruff B008（默认参数里调函数）


@router.post("/events")
async def events(request: Request, body: EventsRequest) -> dict:
    for e in body.events:
        if e.kind not in KNOWN_KINDS:  # 开放枚举：只警告不拒收（透传约定）
            log.warning("unknown kind=%s session=%s seq=%s", e.kind, body.session_id, e.seq)

    stored, duplicates = store_events(
        request.app.state.storage, request.state.workspace, body.session_id, body.events
    )
    return {"stored": stored, "duplicates": duplicates}


@router.post("/ingest_doc")
async def ingest_doc(
    request: Request, file: UploadFile = _REQUIRED, meta: str = Form("{}")
) -> dict:
    try:
        meta_dict = json.loads(meta)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"meta 不是合法 JSON: {e}") from e
    meta_dict["filename"] = file.filename or "file"

    content = await file.read()
    rec = request.app.state.storage.put_doc(content, meta_dict, request.state.workspace)
    return {"l0_id": rec.id, "hash_hit": rec.hash_hit}
