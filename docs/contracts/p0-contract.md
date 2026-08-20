# P0 对接契约（唯一需要读的文档）

> P0 目标：**采集端到端**——事件推入 memory，落成 L0 的 jsonl + pending 记录。
> 本文档自包含 P0 全部对接内容。P1（演化引擎/检索/L1/L2）的契约见 contracts/ 其余文档，
> **P0 结束后再引入，现在不用读**。

**协作标准：联调之前，双方不需要互相询问。** 契约即答案。

## P0 工作项

| 工作项 | 内容 | 对接依据 |
|---|---|---|
| 框架（环境 / FastAPI 骨架 / 编排门） | 统一设计搭建 | — |
| storage L0 实现 | 方法实现与 jsonl/l0_records 落盘 | §4 |
| TS 插件（opencode 侧） | 抓事件 → 翻译 → 推送，curl 即可自测，不依赖 memory 代码 | §1 |
| /events、/ingest_doc 端点 + ingestion 服务 | 端点薄壳 + store_events | §2、§3、§4 |
| OTLP receiver（docgen/langgraph 数据接入） | 收 OTel 流 → 翻译成信封 → 同一 ingestion 入口 | §5、otel-mapping.md |
| docgen 侧 collector 配置 | 加一个 exporter 指向 memory | §5 |

---

## §1 事件契约（TS 插件的对接面）

### 请求

```
POST /events
Headers: { "X-Workspace": "codegen", "Content-Type": "application/json" }

{
  "session_id": "s_9f2a",                      // 会话开始生成 uuid，全程不变
  "events": [
    { "seq": 1, "ts": "2026-08-18T14:00:01+08:00", "kind": "session_start",
      "data": { "agent": "opencode", "task_hint": "修CRC" } }
  ]
}
```

### 插件五条职责

1. session_id 全程不变
2. seq 单调递增（可重复、不可跳号——跳号 = 丢事件）
3. 本地攒批（10 条或 30 秒）再发
4. 失败整批重发，**无需记录哪些成功过**（幂等键 (session_id, seq)，重复返回 duplicates 不报错）
5. 会话结束（退出/idle）必发 `kind: "session_end"`——**不发则数据永不进入演化**

### 响应

```jsonc
// 成功
{ "stored": 3, "duplicates": 0 }
// 失败（缺 workspace 头 / body 字段缺失或类型错）
{ "error": { "code": "WORKSPACE_REQUIRED | BAD_REQUEST", "message": "..." } }
```

### kind 词汇表（开放枚举：未知 kind 收下落盘 + 警告，不拒收）

| kind | 何时发 | data 必填 | data 可选 |
|---|---|---|---|
| `session_start` | 会话开始 | `agent` | `task_hint` |
| `llm_call` | 模型调用完成 | `model`, `tokens_in`, `tokens_out` | `stage`, `duration_ms` |
| `tool_call` | 工具调用 | `name`, `outcome` | `args_summary` |
| `file_write` | 文件修改落地 | `path` | `summary` |
| `hitl` | 用户打断/纠偏 | `action` | `note` |
| `error` | 失败 | `message` | `recoverable` |
| `session_end` | 会话结束 | 无 | |

> TS 插件的核心工作 = 把 opencode 的 hook 事件翻译到这张表。翻译规则是插件内部知识，不由框架约定。

### 透传约定（核心映射 + 其余透传，零信息丢失）

上表 7 种是"核心 kind"（代码分支 + 两场景公共语义）。**opencode 其余事件不丢弃，
按本约定透传**：

```
认识的：kind = 标准kind，data = 提炼后的字段（按上表 schema）
不认识：kind = "opencode.<opencode原生事件名>"，data = 原事件整个 JSON 原样
```

插件实现即一张小映射表 + 一个兜底：

```ts
kind = KIND_MAP[opencodeEvent.name] ?? `opencode.${opencodeEvent.name}`
```

- L0 全量落盘，筛与不筛推迟到消费端（TraceDeriver 把透传事件折叠为统计行，不逐条进 prompt）
- 某类透传事件被证明重要后，晋升为正式 kind = 插件映射表加一行（词汇表自进化）
- 透传事件同样有 seq/session_id，遵守同一幂等规则

---

## §2 HTTP 端点契约（transport 层的两个采集端点）

全局：workspace 一律走 `X-Workspace` 头（编排门统一校验，端点内不重复校验）；
错误统一 `{"error":{"code","message"}}`；成功直接返回本体。

### POST /events
解析 body → 校验形状（字段名/类型，kind 是开放枚举**不校验取值**）→ 调
`ingestion.store_events(workspace, session_id, events)` → 返回 `{"stored", "duplicates"}`。

### POST /ingest_doc（multipart）

```bash
curl -X POST -H "X-Workspace: docgen" -F "file=@xx.xlsx" -F 'meta={"source":"..."}' \
  http://localhost:8000/ingest_doc
# → { "l0_id": "a3f...", "hash_hit": false }
```

解析 multipart → 调 `storage.put_doc(content, meta, workspace)` → 透传返回。
hash_hit=true 表示文档已存在（同内容 hash 命中），调用方走缓存分支。

### 端点纪律（review 检查项）

端点函数只做三件事：解析请求 → 调服务层/storage → 透传响应。
**transport 目录里不出现 SQL、不出现 LLM 调用。**

---

## §3 ingestion 语义（store_events）

```
输入: (workspace, session_id, events)
1. 维护本 session 的已见 seq 集合（内存缓存，首包从 jsonl 读）
2. 逐条过滤：seq 已见 → duplicates++；新事件攒批
3. storage.put_session 落盘
4. 本批含 session_end 且 l0_records 无该 session 行 → 插 pending 行（storage 内完成）
返回: (stored, duplicates)
```

不做的事：不解析 kind 语义、不排序（jsonl 按到达顺序，消费端按 seq 排）、
不派生（演化引擎异步消费 pending）。

---

## §4 storage L0 签名

```python
def put_doc(self, content: bytes, meta: dict, workspace: str) -> L0Record
    # 算 content_hash：命中返回已有记录(hash_hit=True)；未命中写文件+插记录(pending)

def put_repo(self, repo_path: str, workspace: str) -> L0Record   # P3 用，P0 可缓

def put_session(self, workspace: str, session_id: str, events: list[Event]) -> None
    # append jsonl，幂等由调用方(seq 集合)保证；含 session_end 时插 l0_records

@dataclass
class L0Record:
    id: str; type: str; workspace: str; path: str
    content_hash: str | None; derived_state: str; hash_hit: bool
```

`derived_state`: `pending → derived | failed`。**ingestion 只制造 pending，
演化引擎（P1）消费它**——P0 期间没有消费者，pending 行堆积是正常预期状态。

---

## §5 OTLP receiver（push 型，已实现、待命）

docgen 侧统一 OTel 格式 + OTel SDK 传输。**当前实际接入走拉型（见下）**，receiver
在对方配 collector 或 SDK 直发时启用——两路产出同一信封，汇于同一 store_events。

### 链路（push 型启用时）

```
docgen (OTel SDK) → OTLP → collector → Phoenix（他们现有观测后端，是 sink 不转发）
                              └──exporter──→ memory POST /otlp/v1/traces
```

### 当前实际链路：Phoenix 同步器（拉型）

```
docgen SDK ──OTLP/protobuf──→ Phoenix（postgres 库）←── 只读账号，每5分钟拉
                                     └────────→ PhoenixSyncer → 信封 → store_events
```

docgen 侧成本 = 一个只读数据库账号。规则：只导含 done 的完整 trace、按 trace_id
全量一次导入；水位存 data/<ws>/phoenix_sync.json（last_id + incomplete 名单）。

### 端点

```
POST /otlp/v1/traces
Content-Type: application/x-protobuf   ← 默认
                 application/json      ← OTLP/JSON，开发期可要求 docgen 配此编码
```

protobuf 解析用官方 `opentelemetry-proto` 包的生成类，不手写解析。

### 翻译（receiver 内部完成，规则冻结于 otel-mapping.md）

```
resource.service.name  → workspace（同样过注册表校验）
trace_id               → session_id（注意：一个请求可能混装多个 trace，
                          须按 trace_id 分组，每组各调一次 store_events）
span 按 startTime 排序  → 派生 seq（幂等由此成立：重发批次派生出相同 seq，
                          被 ingestion 的 seq 集合判重，receiver 无状态）
name="done" 的 span     → session_end 事件
span name/attributes   → kind（对照表见 otel-mapping.md）
```

**产出与 §1 同一信封**：翻译完直接构造 §2 的 Event/EventsRequest pydantic 类
（不拼 dict——构造即校验，两门产出同类型由类型系统保证），
调用同一个 `ingestion.store_events`——ingestion 不感知 OTLP 的存在。

### 响应（注意：不是 JSON）

成功 = HTTP 200 + **空 protobuf** `ExportTraceServiceResponse`——OTLP 协议规定，
collector 靠它确认；返回 JSON 会被当协议错误反复重发。
解析失败 / service.name 未注册 = 400（collector 记日志丢弃，不重试）。

### receiver 纪律

receiver 属于 transport 层：只做协议解析 + 翻译 + 调 ingestion，
不解析 kind 语义、不碰 SQL、不调 LLM。

---

## P0 验收

```
插件(或 curl 模拟)推一批事件 → /events → {stored:N, duplicates:0}
重发同一批                     → /events → {stored:0, duplicates:N}
最后一批含 session_end         → data/<ws>/l0/session/<id>.jsonl 48 行
                                + l0_records 出现一行 pending
/ingest_doc 上传同一文件两次   → 第二次 hash_hit=true
OTLP/JSON 样例 trace 发 receiver → 同样落成 jsonl + pending（与 /events 产物同构）
```
