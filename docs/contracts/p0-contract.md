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
| Phoenix 同步器（docgen 数据接入，REST 拉型） | 拉 span → 翻译成信封 → 同一 ingestion 入口 | §5、otel-mapping.md |

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

## §5 docgen 采集：Phoenix 同步器（拉型，REST 版）

> 2026-08-24：OTLP receiver **已删除**（decisions.md）。真实数据核验发现 docgen 的 span
> 直落 Phoenix 服务端、永不走 OTLP 推送——receiver 无真实消费者，protobuf 路也无法验证，
> 预留代码成了纯负担。将来若出现直推 OTLP 的 agent，按 git 历史恢复即可。

### 链路

```
docgen SDK ──OTLP──→ Phoenix 服务端 ←── REST POST /v1/spans（Arrow IPC 响应），每 5 分钟拉
                                     └──→ PhoenixRestReader → span_map → 信封 → store_events
```

docgen 侧成本 = 一个 HTTP 地址，**零账号零改动**（原 psycopg 读库方案因账号卡点被替代）。

### REST 接口

```
POST /v1/spans?project_name=<project>
Content-Type: application/json
{"queries": [{}], "limit": 1000}
→ 200 application/x-pandas-arrow（Arrow IPC，pyarrow 三行解码成 DataFrame）
```

行结构样例：docs/example/docgen-real-tc03-spans.arrow（901 span / 16 trace）。
列即展平的 span：name / context.trace_id / context.span_id / parent_id /
start_time / attributes.*（含领域负载 attributes.docgen）。

### 翻译规则

冻结于 [otel-mapping.md](otel-mapping.md)，实现唯一：`ingestion/span_map.py`。
词表已对 tc03 真实数据核对：`llm_call`→llm_call（tokens 取 llm.token_count.*）、
`tool:*`→tool_call、`done`→session_end、stage 名单 11 个全量在册、其余透传 `span.<name>`。

### 核心规则（不变）

**只导含 done 的完整 trace，按 trace 一次成型**：seq 从 trace 全量派生（分轮增量导入
会 seq 撞车 → 幂等误杀）；崩溃重导 → 相同 seq → store_events 幂等去重。
水位 = 已导 trace_id 名单，存 data/<ws>/phoenix_sync.json（人可改，删掉即重拉）。

ponytail: REST 无增量游标，每轮全量拉 limit=1000 再按名单跳过——tc03 量级够用；
量级上来后换服务端时间过滤（queries.filter 语法待验证）。
