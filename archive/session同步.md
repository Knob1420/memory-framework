# opencode Session 同步系统设计

- **日期**: 2026-07-21
- **状态**: 已与用户确认，待编写实现计划
- **范围**: opencode 插件（采集）+ Python 后端（接收/存储/查询）

---

## 1. 背景与目标

构建一个 opencode session 同步系统，用于**后期分析总结，作为团队共享的知识库**。系统由两部分组成：

1. **opencode 插件**：订阅 opencode session 中的所有事件，同步到后端。
2. **Python 后端服务**：接收并持久化插件发送的 session 数据，提供查询接口。

本期聚焦**采集与存储**（团队知识库的存储底座）。总结/分析/检索增强是后续阶段，不在本期范围。

### 关键事实（已通过 SDK 确认）

- opencode 插件通过 `@opencode-ai/plugin` 提供 `Hooks.event` 入口，opencode 会把**所有** `Event` 推送给插件（`index.d.ts:175`）。
- 事件类型覆盖 session 全生命周期：`session.*`、`message.updated/removed`、`message.part.updated/removed`、`file.edited`、`file.watcher.updated`、`todo.updated`、`command.executed`、`permission.*`，以及噪声类 `lsp.*`、`pty.*`、`tui.*`、`installation.*`（`types.gen.d.ts:602`）。
- **关键张力**：`message.part.updated` 是流式 token 增量，一条助手回复可能触发数百次。直接全量转发会产生极高 QPS。

---

## 2. 关键决策记录

| # | 问题         | 决策                                                  | 理由                                          |
| - | ------------ | ----------------------------------------------------- | --------------------------------------------- |
| 1 | 核心用途     | 团队共享知识库，后期分析总结                          | 决定存储优先、实时性其次                      |
| 2 | 协作形态     | 事后浏览/检索（非实时围观）                           | 决定不需实时推送通道                          |
| 3 | 同步时机     | **事件级实时增量**                              | 用户选择；兼顾数据完整性                      |
| 4 | 同步范围     | **全量原始事件**                                | 团队知识库最怕数据残缺                        |
| 5 | 总结由谁生成 | 先只存储，后期再做总结模块                            | 本期边界                                      |
| 6 | 后端框架     | **FastAPI**                                     | async 友好，生态成熟                          |
| 7 | 后端存储     | **存储抽象层 + 默认 SQLite**，后续可换 PG/Mongo | 用户意向"存储后续考虑"；SQLite 零运维便于起步 |
| 8 | 通信协议     | **HTTP POST**（非 MCP）                         | 见下方 MCP 分析                               |
| 9 | 事件转发策略 | **方案 B：事件聚合 + 可靠投递**                 | 平衡实时性与实用性                            |

### 2.1 为什么不用 MCP（决策 #8 详述）

MCP 是 `client → server 调用 tool` 的模型，方向与本期需求（**插件作为生产者把实时事件流推给后端**）相反：

1. opencode 插件 API（`@opencode-ai/plugin`）是 hook-based 的，**不提供 MCP client 能力**。插件只能通过 `Hooks.event` 接收事件，没有内置通道把事件喂给 MCP server。
2. MCP tool call 有 JSON-RPC 封装 + schema 协商开销，对"每秒数十次小事件"不友好；它面向低频、语义化的 AI 工具调用。
3. 语义错位：事件采集是基础设施职责，不应依赖 AI 主动调用工具。

**MCP 的正确位置在后期**：当后端需要被 AI 客户端检索知识时，后端作为 MCP server 暴露 `search_sessions` / `get_session_summary` 这类 tool（见 §10 未来扩展）。那时方向（AI ← 知识库）才匹配 MCP 的语义。

---

## 3. 整体架构与仓库布局

```
┌─────────────────────────┐         HTTP POST          ┌──────────────────────────┐
│  opencode (带插件)       │  ───────────────────────►  │  Python 后端 (FastAPI)    │
│                          │   事件 envelope (JSON)     │                          │
│  session-sync 插件       │  ◄───────────────────────  │  接收 / 存储 / 查询       │
│  - 订阅 Hooks.event      │       ACK (200 + 结果)     │  - 事件去重(幂等)         │
│  - 内存聚合 part         │                            │  - 存储抽象层(默认SQLite) │
│  - 本地磁盘队列+重试     │                            │  - REST 查询 API         │
└─────────────────────────┘                            └──────────────────────────┘
        本地磁盘                                                                        可换 PG/Mongo
   ~/.cache/ycomem/queue/                                                              (repository 抽象)
```

**关键边界**：插件只做"采集 + 可靠投递"，不做业务逻辑；后端只做"接收 + 存储 + 查询"，不做实时推送。两边通过明确的**事件 envelope JSON 契约**（§6）解耦。

### 仓库布局（monorepo）

```
ycomem/
├── plugin/                    # opencode 插件 (TypeScript/Bun)
│   ├── package.json           # 依赖 @opencode-ai/plugin @1.15.13
│   ├── tsconfig.json
│   └── src/
│       ├── index.ts           # 插件入口，注册 Hooks.event
│       ├── router.ts          # 按 event.type 分流
│       ├── aggregator.ts      # part → message 聚合
│       ├── queue.ts           # 本地磁盘队列 + 重试
│       ├── sender.ts          # HTTP 客户端
│       ├── env.ts             # 来源标识(主机/实例)
│       ├── session_context.ts # 当前 session 上下文追踪 (为无 session_id 的事件注入)
├── backend/                   # Python 后端
│   ├── pyproject.toml         # FastAPI + uvicorn + SQLAlchemy Core
│   ├── app/
│   │   ├── main.py            # FastAPI 入口
│   │   ├── api/               # 路由: /events /sessions /health /projects
│   │   ├── models/            # Pydantic 事件 schema (与插件契约一致)
│   │   ├── storage/           # repository 抽象 + 默认 SQLite 实现
│   │   ├── dedup.py           # 幂等去重(基于 session_id+seq)
│   │   └── config.py          # 配置(端口/存储/限流)
│   └── tests/
├── contracts/                 # 共享 fixture (sample-envelopes.json)
├── docs/superpowers/specs/    # 本设计文档
└── .opencode/                 # 已有配置(保留)
```

### 插件分发

默认按**本地私有插件**做：团队内部共享代码，在 `opencode.json` 里通过本地路径引用，不上 npm。后续公开发布见 §10。

---

## 4. 插件详细设计

### 4.1 模块数据流

```
opencode Hooks.event ──► router ──┬──► aggregator ──► queue ──► sender ──► HTTP POST /events
  (所有 Event)        │            (聚合 part)    (磁盘)   (重试)
                     └──► queue (直接转发: session.*/file.edited/todo.*/command.*/permission.*)
                                                                       │
                                  flush 触发: message.updated / session.idle / dispose
```

### 4.2 入口与注册 (`src/index.ts`)

- 导出默认 `Plugin` 函数，签名 `(input: PluginInput, options?) => Promise<Hooks>`
- 返回 `{ event, dispose }` 两个 hook
- 利用 `input.project`（来源标识）、`input.serverUrl`、`input.client`（备用）
- 通过 `opencode.json` 的 `plugin` 字段加载（本地路径）

### 4.3 事件路由 (`src/router.ts`)

按 `event.type` 分流：

| 事件类型                                                                                   | 处理                                                                                  |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| `message.part.updated`                                                                   | 进 aggregator（按`messageID` 缓存 part，**不直接转发**）                      |
| `message.updated`                                                                        | 把 aggregator 里该`messageID` 的完整 parts 连同 info 打包为 envelope 入队，清理缓存 |
| `message.removed`                                                                        | 生成"删除"envelope 入队                                                               |
| `message.part.removed`                                                                   | 转 envelope 入队                                                                      |
| `session.created/updated/deleted/diff/error/status/idle/compacted`                       | 直接转 envelope 入队                                                                  |
| `file.edited`、`file.watcher.updated`、`vcs.branch.updated`（payload 无 session_id） | 从 session 上下文（§4.4）注入`session_id`；若无上下文则丢弃                        |
| `todo.updated`、`command.executed`                                                     | 直接转 envelope 入队                                                                  |
| `permission.updated/replied`                                                             | 直接转 envelope 入队                                                                  |
| `lsp.*`、`pty.*`、`tui.*`、`installation.*`、`server.*`（噪声）                  | 默认丢弃，配置`include_noisy_events` 可开启                                         |

### 4.4 Session 上下文追踪 (`src/session_context.ts`)

为给 `file.edited` / `file.watcher.updated` / `vcs.branch.updated` 等**payload 内无 session_id** 的事件注入 `session_id`，插件维护一个当前 session 上下文：

- 数据结构：`Map<sessionID, { lastActive: number }>`，记录已知 session 及最近活跃时间
- 更新来源：所有 `session.*` 事件、所有 payload 含 `sessionID` 的事件到达时，把该 sessionID 登记为已知并刷新 `lastActive`
- "当前 session" 定义：同一 opencode 实例内最近 5 分钟内活跃的 session。若同时有多个，取 `lastActive` 最新的
- 注入规则：无 session_id 的事件到达时，取当前 session 注入；若不存在（如启动后还没收到任何 session 事件），**丢弃该事件**并记警告日志
- 退出清理：`dispose` 时清空 Map

> 注：opencode 通常每实例同时只有一个活跃 session；本设计仍兼容多 session 情况，按"最近活跃"挑选。

### 4.5 聚合器 (`src/aggregator.ts`)

- `Map<messageID, Part[]>`，part.updated 来了就 append
- part 按 `part.id` 去重（幂等）
- 防内存泄漏：`maxPartsPerMessage`（默认 5000）、`maxAgeMs`（默认 10 分钟），超限先 flush 再清
- flush 时机：`message.updated`（正常完成）、超限/超时（强制）、`dispose`（插件卸载）

### 4.6 本地磁盘队列 (`src/queue.ts`)

- 落盘路径：`~/.cache/ycomem/queue/`，按时间切片的 `.jsonl` 文件（每行一个 envelope）
- 入队即 `fs.appendFile` 落盘 → 进程崩溃不丢
- 发送成功后删除该行（或滚动到下一文件）
- 容量上限：`maxQueueBytes`（默认 100MB），超限丢**最旧**的（保护宿主机磁盘，优先保近事）

### 4.7 HTTP sender (`src/sender.ts`)

- 单 worker 循环：取队列 → 批量 POST `/events`（每批 `batch_size` 条或每 `batch_interval_ms` 一批）→ 成功 ACK 删除、失败指数退避（1s → 2s → 4s … 上限 60s）
- 用 `fetch`（opencode 跑在 Bun 上，`fetch` 可用）
- 超时 10s
- 错误分级（见 §7）

### 4.8 来源标识 (`src/env.ts`)

每个 envelope 头部带 `source`，标识"由哪个成员的哪个 opencode 实例产生"：

```json
{
  "source": {
    "project_name": "<input.project.name>",
    "user_id": "<成员工号, 如 00123>",
    "directory": "<input.directory>"
  }
}
```

`user_id`（成员工号）从 `opencode.json` 的 plugin 配置项传入，示例：

```jsonc
{
  "plugin": [
    ["./plugins/ycomem", {
      "endpoint": "http://localhost:7302",
      "projectName": "satest",
      "user_id": "00123"
    }]
  ]
}
```

> 注：`source` 承载"成员 + 项目"身份信息，便于后端在 envelope 顶层直接按成员/项目过滤。project_id（SDK 内部 ID）仍由 session payload 的 `projectID` 提供，用于精确关联；host 不单独追踪（多机场景靠 user_id + 多次启动区分）。`user_id` 优先从 plugin options 的 `user_id` 字段读取，备选环境变量 `YCOMEM_USER_ID`；同一成员在不同机器/不同时间启动 opencode，都用同一 `user_id`，保证事件归属稳定。

### 4.9 配置项（通过 opencode.json 的 plugin options 传入）

```jsonc
"plugin": [["./plugin", {
  "endpoint": "http://localhost:8848",   // 后端地址（必填）
  "user_id": "00123",                    // 成员工号（必填）
  "batch_size": 50,
  "batch_interval_ms": 200,
  "max_queue_bytes": 104857600,
  "include_noisy_events": false           // 是否采集 lsp/pty/tui 等
}]]
```

### 4.10 dispose hook

插件卸载时：flush aggregator 所有残留 message → 等队列排空（最多 5s）→ 退出。

---

## 5. 后端详细设计

### 5.1 分层

```
FastAPI app
├── api/        路由层  (接收请求, 校验, 返回)
├── models/     Pydantic schema (与插件 envelope 契约一致)
├── dedup/      幂等去重 (基于 session_id+seq)
├── storage/    repository 抽象 + SQLite 默认实现
└── config/     配置 (端口/存储/限流)
```

### 5.2 API 路由

| 方法 | 路径                        | 用途                                                                                                                                              |
| ---- | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| POST | `/events`                 | 批量接收事件 envelope（插件唯一写入入口）。请求体`{"events": [envelope, ...]}`。**幂等**：重复 `(session_id, seq)` 静默忽略，返回 200。 |
| GET  | `/health`                 | 存活探针；返回队列长度、最近入库时间、存储大小                                                                                                    |
| GET  | `/sessions`               | 列出 session，支持`?project_id=&since=&until=&limit=&offset=`（`project_id` 来自 session payload 的 `projectID`）                           |
| GET  | `/sessions/{id}`          | 取单个 session 详情 + 元信息                                                                                                                      |
| GET  | `/sessions/{id}/messages` | 取该 session 全部消息（按时间排序，含 parts）                                                                                                     |
| GET  | `/sessions/{id}/events`   | 取该 session 的原始事件流（调试用）                                                                                                               |
| GET  | `/projects`               | 列出已知 project（从 session payload 的`projectID` 聚合）                                                                                       |

查询 API 为后期分析总结打基础；后端第一阶段不实现总结逻辑。

### 5.3 事件入库策略

不同事件类型写入不同逻辑表（SQLite 默认实现）：

- `sessions` 表：`session.created/updated/deleted` upsert；存原始 JSON + 提取的标题/时间/project_id
- `messages` 表：来自聚合后的 `message.updated`；存 info JSON + 提取的 role/session_id/created
- `parts` 表：消息的每个 part（text/tool/file/reasoning 等）单独一行，便于按 part 类型检索
- `raw_events` 表：**所有** envelope 原样落盘一份（保真，后期分析兜底，按复合主键 `(session_id, seq)` 去重）
- `files` / `todos` / `commands` / `permissions`：各一张窄表存关键提取字段 + 原始 JSON

既有关键字段便于 SQL 查询，又有 raw 兜底，符合"全量原始事件"要求。

### 5.4 幂等去重 (`app/dedup.py`)

- envelope 通过 `(session_id, seq)` 复合主键去重（`seq` 在每个 session 内从 0 递增，见 §6）
- 入库前查 `raw_events` 是否已存在相同 `(session_id, seq)`；存在则跳过该条（批内某条已存在不影响其他条）
- SQLite 用 `INSERT OR IGNORE`；后续换 PG 用 `ON CONFLICT (session_id, seq) DO NOTHING`

### 5.5 存储抽象层 (`app/storage/`)

```python
class EventRepository(Protocol):
    def ingest(self, envelopes: list[Envelope]) -> IngestResult: ...
    def list_sessions(self, filters: SessionFilters) -> list[SessionRow]: ...
    def get_session(self, id: str) -> SessionRow | None: ...
    def get_messages(self, session_id: str) -> list[MessageRow]: ...
    def get_events(self, session_id: str) -> list[Envelope]: ...
    # ... 其他查询
```

- 默认实现 `SqliteRepository`，数据库文件 `~/.local/share/ycomem/ycomem.db`
- 用 **SQLAlchemy Core**（不是 ORM，避免事件 schema 异构的映射负担）；JSON 字段用 `JSON` 类型
- 后续换 PG/Mongo 只需新增 `PostgresRepository` / `MongoRepository`，通过配置 `storage.backend` 切换

### 5.6 配置 (`app/config.py`, pydantic-settings)

```python
YCOMEM_PORT = 8848
YCOMEM_STORAGE_BACKEND = "sqlite"           # sqlite | postgres | mongo（后期）
YCOMEM_STORAGE_URL = "~/.local/share/ycomem/ycomem.db"
YCOMEM_MAX_BATCH = 200                       # 单批最大事件数
YCOMEM_RATE_LIMIT_PER_MIN = 0                # 0=不限流（团队内部信任）
```

通过环境变量或 `.env` 覆盖。

### 5.7 错误处理

- 请求体校验失败 → 422 + 详细错误（便于插件调试）
- 批内部分失败 → 仍 200，response body 标明哪些 `(session_id, seq)` 已存在/失败（插件据此清理队列）
- 后端内部异常 → 500 + 日志（插件会重试，因为 5xx 触发重试）

### 5.8 日志与可观测

- 结构化日志（JSON），含 `session_id` / `seq` / `user_id` / `project_name` 便于追踪
- `/health` 返回队列长度、最近入库时间、存储大小

---

## 6. 数据契约（事件 Envelope）

插件与后端之间唯一的耦合点，两边必须一致。

### 6.1 Envelope 顶层结构

```typescript
// 插件侧 (TypeScript)
interface Envelope {
  event_type: string;          // 原始 event.type (如 "message.updated")
  seq: number;                 // session 内从 0 单调递增; 每个新 session 重置
  ts: number;                  // 插件收到事件的 epoch ms
  source: Source;              // 成员级来源标识
  session_id: string;          // 该事件所属 session (必填, payload 无则由插件从上下文注入)
  payload: unknown;            // 原始 event.properties (透传 SDK 类型)
}
interface Source {
  project_name: string;        // 项目名, 来自 input.project.name
  user_id: string;             // 成员工号, 由 opencode.json plugin 配置传入
  directory: string;           // 工作目录, 来自 input.directory
}
```

```python
# 后端侧 (Pydantic, 字段一一对应)
class Source(BaseModel):
    project_name: str      # 项目名, 来自 input.project.name
    user_id: str           # 成员工号, 由 opencode.json plugin 配置传入
    directory: str         # 工作目录, 来自 input.directory


class Envelope(BaseModel):
    event_type: str        # 原始 event.type (如 "message.updated")
    seq: int               # session 内从 0 单调递增; 每个新 session 重置
    ts: int                # 插件收到事件的 epoch ms
    source: Source         # 成员级来源标识
    session_id: str        # 该事件所属 session (必填)
    payload: dict[str, Any]        # 原始 event.properties, 不强校验内部, 保真存储
```

### 6.2 关键设计决策

- **`payload` 不做强 schema 校验**：opencode SDK 的 `Part` 有十几种变体（text/tool/file/reasoning/...），后端校验它们等于跟 SDK 版本绑死。改为保真存储原始 dict，查询时再按 `event_type` 解读。
- **去重主键 `(session_id, seq)`**：`seq` 在每个 session 内从 0 单调递增，新 session 自动重置；幂等去重基于 `(session_id, seq)` 复合主键判断。这样 session 内顺序天然保证、跨 session 互不影响，且 envelope 不再需要单独的 `event_id` 字段。
- **`session_id` 必填且提到顶层**：所有事件都归属于某个 session，session 是后端检索/聚合的基本单位。提到顶层避免每次解析 payload。
- **`session_id` 提取/注入规则**：插件 router 知道每种 event_type 的 session_id 在 payload 哪个字段（如 `message.updated` 在 `properties.info.sessionID`），优先从 payload 提取；payload 无该字段的事件类型（`file.edited` / `file.watcher.updated` / `vcs.branch.updated`）由 §4.4 的 session 上下文注入；上下文为空则丢弃事件。

### 6.3 批量请求格式

```http
POST /events
Content-Type: application/json

{
  "events": [
    { "event_type": "session.created", "seq": 0, "session_id": "s1", ... },
    { "event_type": "message.updated", "seq": 1, "session_id": "s1", ... }
  ]
}
```

### 6.4 响应格式（200，含逐条结果供插件清理队列）

```json
{
  "ingested": 48,
  "duplicated": 2,
  "failed": 0,
  "details": [
    { "session_id": "s1", "seq": 0, "status": "ingested" },
    { "session_id": "s1", "seq": 1, "status": "duplicated" }
  ]
}
```

`details` 仅在 `duplicated > 0 || failed > 0` 时填充，正常情况下省略以减小响应体。

### 6.5 字段稳定性承诺

- `event_type` / `seq` / `ts` / `source` / `session_id` / `payload` 这 6 个顶层字段是**契约**，不随 SDK 版本变。
- `payload` 内部结构随 SDK 演进，后端只做透传 + 按 event_type 的"尽力解析"。
- 这条边界让插件升级 opencode SDK 后不用改后端。

---

## 7. 错误处理与可靠性

### 7.1 可靠性分级

| 场景                           | 插件行为                                                                            | 后端行为                              |
| ------------------------------ | ----------------------------------------------------------------------------------- | ------------------------------------- |
| 后端短暂不可达（网络/重启）    | 磁盘队列缓冲，指数退避重试                                                          | —                                    |
| 后端返回 5xx                   | 同上，重试                                                                          | 日志 + 内部异常隔离                   |
| 后端返回 4xx（非 408/429）     | **丢弃 + 写死信文件** `~/.cache/ycomem/dead/<ts>.jsonl`，避免脏数据无限重试 | 422 给详细校验错误                    |
| 后端返回 408/429               | 退避重试                                                                            | 限流时返回 429 +`Retry-After`       |
| 插件进程崩溃                   | 磁盘队列已落盘，重启后续传                                                          | —                                    |
| 队列超容量上限                 | 丢最旧事件 + 警告日志（保近事优先）                                                 | —                                    |
| 重复发送同一 (session_id, seq) | 无害（幂等）                                                                        | `INSERT OR IGNORE`，返回 duplicated |
| part 聚合超时未收尾（10min）   | 强制 flush 残留 message                                                             | 正常入库                              |

### 7.2 死信处理

插件死信文件手动可恢复（运维把文件挪回 queue 目录即可重投）。后端不做死信——所有到达后端的事件要么入库要么 4xx 拒绝，没有"后端侧死信"。

### 7.3 关键不变量（测试必须覆盖）

1. **不丢事件**（在队列容量内）：杀进程 → 重启 → 队列续传 → 后端最终收到全部事件
2. **不重复入库**：同一 `(session_id, seq)` 发 N 次，`raw_events` 只有一行
3. **顺序**：同一 session 的事件按 `seq` 在后端可还原时序（即便 HTTP 乱序到达）
4. **part 聚合正确**：流式 part.updated 经聚合后，等于一次 message.updated 的内容
5. **来源可区分**：两个不同 instance 的事件，后端能按 source 分组
6. **session_id 必填**：到达后端的每条 envelope 都有 session_id；无上下文的 `file.edited` 等事件在插件侧被丢弃，不会到达后端

---

## 8. 测试策略

### 8.1 插件（TypeScript / Bun test）

- 单元：aggregator（part 去重、超限 flush）、queue（落盘/读取/滚动）、router（事件分流）、env（source 提取）
- 集成：用 mock opencode client 喂事件流 → 断言发往 mock HTTP server 的 envelope 正确
- 可靠性：模拟后端 500/超时/进程重启，断言队列续传、死信写入

### 8.2 后端（Python / pytest）

- 单元：dedup、envelope 解析、各 event_type 的字段提取
- 集成：FastAPI TestClient → SQLite，覆盖 `/events` 批量、去重、各查询 API
- 契约一致性：**共享一份 fixture（JSON envelope 样本）**，插件和后端测试都加载它，确保两边对同一 payload 解读一致

### 8.3 共享 fixture

在 `contracts/sample-envelopes.json` 放每种 event_type 各一条真实样例，插件和后端测试都引用。这是跨语言契约的核心保障。

### 8.4 端到端（可选，后期）

起一个真实 opencode + 插件 + 后端，跑一段 scripted 对话，断言后端能查到完整 session。

---

## 9. 分阶段交付计划

每阶段可独立验证、独立交付价值。

### 阶段 1 — 最小可用闭环（MVP）

**目标**：跑通"插件采集 → 后端存储 → 能查到"，证明契约正确。

- 插件：`Hooks.event` 订阅 + router（只处理 `session.*`、`message.updated`、`message.part.updated` 聚合）+ 内存队列 + 简单 HTTP sender（无重试）
- 后端：`POST /events`（批量和去重）+ `GET /health` + SQLite repository + 基础 schema（sessions/messages/raw_events 三表）
- **验证**：本地起 opencode 跑一段对话 → 后端能 `GET /sessions` 列出、`GET /sessions/{id}/messages` 取到完整消息

### 阶段 2 — 可靠性加固

- 插件：磁盘队列、指数退避重试、死信、dispose flush、配置项、噪声事件过滤
- 后端：parts/files/todos/commands/permissions 各表、完整查询 API、结构化日志、`/health` 增强
- **验证**：杀进程/模拟后端宕机，断言 §7.3 五个不变量全部成立

### 阶段 3 — 查询完善（为分析总结铺路）

- 后端：分页/排序/过滤完善、按 project 聚合统计、按时间范围导出
- 可选：简单 web 列表页（FastAPI + 静态 HTML，无前端框架，最小依赖）
- **验证**：能按 project/时间筛选并导出 session 列表

> 阶段 1+2 完成即满足核心需求（团队知识库的存储底座）。阶段 3 为后续"总结分析"做准备，但总结逻辑本身不在本期范围。

---

## 10. 未来扩展（不在本期，仅记录方向）

1. **MCP 检索 server**：后端作为 MCP server 暴露 `search_sessions` / `get_summary`，让团队成员的 AI 客户端查知识库（§2.1 已论证这是 MCP 的正确位置）。
2. **自动总结模块**：后端接 LLM，在 `session.idle` 或定时触发，生成标题/标签/摘要。
3. **存储切换**：新增 `PostgresRepository`（带 pgvector）支持语义检索。
4. **权限**：多用户认证、按 project 分权。
5. **插件 npm 发布**：从本地插件转公开发布。

---

## 11. 本期明确不做

- 实时推送（SSE/WebSocket）给前端
- 前端 SPA / 复杂 UI
- 自动总结逻辑
- 多租户 / 权限系统
- MCP server（留待后期检索阶段）

---

## 12. 技术选型汇总

| 组件             | 选型                                | 备注                  |
| ---------------- | ----------------------------------- | --------------------- |
| 插件语言/运行时  | TypeScript / Bun                    | opencode 插件原生环境 |
| 插件 SDK         | `@opencode-ai/plugin@1.15.13`     | 已安装                |
| 插件测试         | Bun test                            |                       |
| 后端语言         | Python                              |                       |
| 后端框架         | FastAPI                             | async 友好            |
| 后端 ASGI        | uvicorn                             |                       |
| 后端 ORM/Core    | SQLAlchemy Core（非 ORM）           | 事件 schema 异构      |
| 后端存储（默认） | SQLite                              | 零运维，可换 PG/Mongo |
| 后端配置         | pydantic-settings                   |                       |
| 后端测试         | pytest + httpx (TestClient)         |                       |
| 通信             | HTTP POST + JSON                    | 单向投递              |
| 共享契约         | `contracts/sample-envelopes.json` | 跨语言一致性          |
