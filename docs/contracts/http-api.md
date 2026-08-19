# HTTP API 契约（#8）

> 8 个端点 + /health。签名冻结：改接口必须走 PR 并同步更新本文件。
> 每个端点都是薄壳：解析请求 → 调 storage/服务层 → 透传结果。
> HTTP 层不出现一行 SQL、不出现一次 LLM 调用。

## 全局约定

| 项 | 约定 | 理由 |
|---|---|---|
| workspace | 请求头 `X-Workspace: <name>`，必填 | 将来 OTLP 的 service.name 映射到同一概念 |
| 成功响应 | 直接返回数据本体，不包信封 | 失败有专用格式 |
| 失败响应 | `{"error": {"code": "...", "message": "..."}}` | code 机器可判，message 给人/agent |
| 鉴权 | v0 无（本机）；预留 `X-Api-Key` 头 | 对外开放时再加 |
| 同步/异步 | 入库立即返回 l0_id，派生异步 | 采集不等演化；状态在 l0_records |

错误码枚举：`WORKSPACE_REQUIRED` / `BAD_REQUEST` / `UNSUPPORTED_TYPE` / `NOT_FOUND` / `INTERNAL`。
HTTP 状态码只用 400 / 404 / 422 / 500。

---

## 工具接口（agent 主动拉）

### POST /search

```jsonc
// 请求
{ "query": "帧解析", "type": "docs", "k": 5 }   // type: facts | docs | scenes | code（traces 预留）
// 响应
{ "hits": [
    { "id": "chunk_017", "type": "docs", "title": "魔方指令表 > 数据对照",
      "content": null,        // ≤200 字给全文，>200 给 None（agent 再调 /get）
      "score": 0.83 }
]}
```

截断规则由 storage 实现，本端点透传。agent 契约：content 有值直接用，None 才调 get。

### POST /get

```jsonc
{ "type": "docs", "id": "chunk_017" }           // id 或场景名二选一
{ "type": "scenes", "name": "CAN" }
// 响应：完整记录（docs=chunk 全文；scenes=md 全文；code=符号+源码）
```

### POST /code_explore

```jsonc
{ "query": "帧解析" }
{ "files": [
    { "path": "src/packet.c", "symbols": ["parse_packet", "check_crc"], "source": "..." },
    { "path": "src/crc.c",   "symbols": ["crc16"], "source": "..." }
]}
```

### POST /code_graph

```jsonc
{ "symbol": "parse_packet", "relation": "impact", "depth": 1 }
// relation: callers | callees | impact（= 反向 callers 递归）
{ "nodes": [ { "full_name": "main", "kind": "function", "file_path": "src/main.c",
               "line_start": 10, "source": "...", "depth": 1 } ],
  "edges": [ { "src": "main", "dst": "parse_packet", "kind": "calls" } ] }
```

## 注入接口（编排层调用）

### POST /prompt

```jsonc
{ "task_desc": "数传协议生成" }       // + X-Workspace
{ "injection": "通用.md 全文 + top-1 scene 全文 + 其余场景摘要",
  "top_scene": "数传" }              // 命中场景名，编排层可用于埋点
```

内部执行 load_always_on + load_catalog，命中即内部 bump heat（编排层不回写）。
opencode prompt hook 与 docgen plan 编排层调用的都是它。

## 采集接口（agent / 插件推送）

### POST /events（幂等）

```jsonc
{ "session_id": "exp-016", "events": [
    { "seq": 1, "ts": "...", "kind": "llm_call", "data": { "stage": "plan", "tokens_in": 10720 } },
    { "seq": 2, "ts": "...", "kind": "tool_call", "data": { "name": "tool_save_plan" } }
]}
{ "stored": 2, "duplicates": 0 }
```

- 幂等键 (session_id, seq)：重推已存在的 seq 计入 duplicates，不报错，插件可无脑重发
- 支持单条和批量（plugin 逐条推或攒批灌）
- 特殊事件 `kind: "session_end"`：写 l0_records(type=session, pending) → 进入演化管线。
  **这是异步演化的触发开关**

## 事件信封与 kind 词汇表

信封 = `{session_id, events: [{seq, ts, kind, data}]}`。`seq` 单调递增（可容忍重发，
不可跳号）；`ts` ISO8601；`kind` **开放枚举**——未知 kind 收下落盘并日志警告
（TraceDeriver 对不认识的 kind 按"发生过一件事"处理），场景可自定义私有 kind。

| kind | 何时产生 | data 必填 | data 可选 |
|---|---|---|---|
| `session_start` | 会话开始 | `agent` | `task_hint` |
| `stage` | 阶段边界（docgen） | `name` | |
| `llm_call` | 模型调用完成 | `model`, `tokens_in`, `tokens_out` | `stage`, `duration_ms` |
| `tool_call` | 工具调用 | `name`, `outcome` | `args_summary` |
| `file_write` | 文件修改落地 | `path` | `summary` |
| `hitl` | 人工介入（review/纠偏/标注修正） | `action` | `note`, `diff` |
| `artifact` | 阶段产物（如 docgen 的 02c plan） | `name`, `content` | |
| `error` | 失败 | `message` | `recoverable` |
| `session_end` | 会话结束（触发演化） | 无 | |

> 状态：**待与队友定稿**（TS 插件按此表组装；TraceDeriver 按此表解释）。
> OTel 路径的翻译见 [otel-mapping.md](otel-mapping.md)。

### POST /ingest_doc（multipart）

```bash
curl -X POST -H "X-Workspace: docgen" \
  -F "file=@微纳智算机遥测控数据表.xlsx" \
  -F 'meta={"source":"docgen tc03"}' \
  http://localhost:8000/ingest_doc
# → { "l0_id": "a3f...", "hash_hit": false }
```

hash_hit=true 时调用方走缓存分支（配 storage.put_artifact 查 summary 缓存）。

### POST /ingest_code

```jsonc
{ "repo_path": "/home/xxx/aerospace-srs" }
// → { "l0_id": "b7e...", "hash_hit": false }
```

v0 接受服务器本地路径；上传 tar 包是后期扩展，不改响应格式。

---

## 与 StorageEngine 的映射（review 检查表）

```
/search       → storage.search
/get          → storage.get（scenes 按名走 get_scene）
/code_explore → storage.search('code') + retrieval 层按 file_path 分组
/code_graph   → storage.graph_query
/prompt       → injection 层（load_always_on + load_catalog）→ storage 读
/events       → storage.put_session
/ingest_doc   → storage.put_doc
/ingest_code  → storage.put_repo
```
