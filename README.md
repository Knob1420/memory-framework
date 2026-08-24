# memory-framework

AI agent 记忆框架：跨 session 经验积累。agent 干过的活（文档、轨迹、代码）沉淀成可检索的记忆，
下次干同样的活直接站在上次的肩膀上。首个落地场景：docgen（文档生成）与 codegen（代码生成）。

和 RAG 的区别一句话：RAG 检索的是**人写好的静态文档**；这里的核心是**从 agent 自己的干活轨迹
演化出经验**（L2 的 facts/scenes，带置信度、可被人工修正淘汰）——文档链（L1）只是地基。

一句话技术形态：**FastAPI + 单 SQLite 文件（FTS5 + sqlite-vec）+ 后台演化线程**。部署即一个进程加一个数据目录。

## 设计思路

### 分层：L0 / L1 / L2

```
L0 原始层    采集到的一切，原样保存（append-only）      文件系统本体 + SQLite 元数据
L1 派生层    L0 加工出的检索结构                        doc_chunks（chunk + 全文/向量索引）
L2 经验层    演化出的结论（facts / scenes）             P2 起
```

原则：**L0 永远是真相源**——L1/L2 都是派生物，派生算法升级后可从 L0 重刷，不需要重新采集。

### 演化是异步的

采集与演化彻底解耦：入库接口只写 L0 + 状态置 `pending`，立即返回；后台调度器轮询 pending 池，
逐条派生（转换→清洗→分块→嵌入→落 L1），成功 `derived`、失败 `failed`（错误留痕）。
MinerU 跑一份 109 页 PDF 要 4 分钟——任何慢操作都不该占着 HTTP 请求。

### 统一信封

事件是内部唯一格式：`{session_id, events: [{seq, ts, kind, data}]}`。
两条采集路殊途同归——TS 插件推 JSON 到 `/events`；docgen 的 agent 轨迹由 Phoenix 同步器
定时拉回（span 树 → 信封，LLM 完整对话与思考过程都在内）——都进同一个 `store_events`。
kind 是开放枚举，未知 kind 收下落盘 + 警告。

### workspace 隔离

codegen 和 docgen 的记忆物理分开（`data/<ws>/...`）+ 逻辑隔离（所有查询隐含 workspace 过滤）。
对外表现为每个请求带 `X-Workspace` 头。

### 七模块

```
transport（HTTP 收发）        orchestrator（workspace 门）
ingestion（外部格式→信封）     storage（唯一碰 SQL 的模块）
evolution（L0→L1 演化，心脏）  llm（唯一外部模型出口）
retrieval / injection（检索与注入，待开发）
```

## 数据的旅程

项目吃两种数据，各有一条链，汇于同一个 L0：

**文档链**（上传的参考文档 → 可检索的 chunk）：

```
curl /ingest_doc ─► gate 验 workspace ─► put_doc（hash 去重、落盘、插 pending）
  ─► 调度器 10s 轮询 ─► derive_doc ─► convert（markitdown / MinerU / libreoffice → md）
  ─► clean 七步清洗 ─► chunker（表格归一 → 标题骨架 → parent/child → 上下文注入）
  ─► embedder.embed ─► put_chunks（三表同写：本体+FTS+vec）─► mark_derived
  ─►（待开发）/search 命中 child → 回溯 parent 全文给 LLM
```

**事件链**（agent 干活的轨迹 → L0，P2 起演化为经验）：

```
docgen agent ──OTel──► Phoenix（他们的观测后端）◄── 每 5 分钟拉（REST/导出文件）
                                        └─► 只导含 done 的完整 trace（失败运行不进记忆）
              ──► span_map 翻译成信封（LLM 完整对话 + thinking + 工具调用全保留）
              ──► store_events ──► data/<ws>/l0/session/<trace_id>.jsonl（append-only）
TS 插件（codegen）──► POST /events（同一信封格式）──► 同一个 store_events
```

## 接口

### HTTP（签名冻结于 [docs/contracts/http-api.md](docs/contracts/http-api.md)）

| 端点 | 方法 | 作用 | 状态 |
|---|---|---|---|
| `/events` | POST | TS 插件推事件信封（JSON） | ✅ |
| `/ingest_doc` | POST | 上传文档（multipart），hash 去重 | ✅ |
| `/search` | POST | 检索（FTS+vec RRF 融合） | 待开发 |
| `/get/{id}` | GET | 取 chunk / parent 全文 | 待开发 |

全局约定：请求头 `X-Workspace` 必填；失败统一 `{"error": {code, message}}`；入库立即返回、派生异步。

### storage（签名冻结于 [docs/contracts/storage.md](docs/contracts/storage.md)）

按动词记，不背方法名：

| 动词 | 方法 | 谁调用 |
|---|---|---|
| put（收） | `put_doc` / `put_session` / `put_chunks` | 端点、syncer、deriver |
| read（给） | `read_session` / `pending`（+search_docs） | 演化引擎、调度器 |
| mark（记状态） | `mark_derived` / `mark_failed` | 调度器 |

### 内部关键接缝

- `derive_doc(l0, storage, embedder)`：调度器 ↔ 演化链的接口
- `embedder.embed(texts)`：鸭子类型，生产 `EmbeddingClient`（本地部署），测试 `FakeEmbedding`

## 铁律（违反即全盘坏）

1. **storage 永不调 LLM**——向量由调用方算好传入（成本边界 + 测试不需要 key）
2. **影子表对调用方不存在**——FTS/vec 是 storage 的私事
3. **storage 无 delete API**——记忆只能沉淀和淘汰，不能篡改
4. **外部模型调用只出现在 `llm/` 包**——CI 的 mock 边界
5. **span→信封映射只有 span_map.py 一个实现**——所有数据源共享，禁止各自翻译
6. 文件读写显式 `encoding="utf-8"`，路径一律 `pathlib`（跨 Windows/Linux 硬规则）

完整决策记录（含被否掉的方案）：[docs/decisions.md](docs/decisions.md)。

## 快速开始

```bash
uv sync                          # 严格按 uv.lock
cp .env.example .env             # 填 LLM/embedding 配置（EMBEDDING_DIM 第一天定死）
uv run uvicorn memory.main:app   # 启动（调度器/Phoenix 同步随进程自动起）
```

关键环境变量：`LLM_BASE_URL/MODEL`（OpenAI 兼容）、`EMBEDDING_BASE_URL/MODEL/DIM`（本地部署的
OpenAI 兼容端点；**维度建表时定死，换模型 = 重建向量表**）。

PDF/图片转换依赖 MinerU（PDF/图片 → markdown 的转换工具，独立 conda 环境，可选）：
安装见 [docs/environments.md](docs/environments.md)。不装不影响其他格式，pdf/image 派生失败留痕。
本地 embedding 服务（bge-m3，显存 ~1.6GB）同见该文档。

提交前本地跑全 CI 序列：

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

## 目录与文档索引

```
src/memory/     七模块源码
docs/contracts/ 契约（http-api / storage / otel-mapping / components）
docs/schema/    表结构
docs/design/    总体方案、文档转换方案
docs/decisions.md  决策记录（模糊时先翻这里）
data/           运行时数据（gitignore）
```

## 协作约定

- **main 常绿**：CI 红了优先修，再开新分支
- **PR 必须互审**：两人团队，互审是知识同步机制，不是质检
- 改接口/schema 的 PR 必须同时改 `docs/contracts/` 或 `docs/schema/`（契约随代码版本走）
- commit 前缀：`transport / orchestrator / retrieval / injection / ingestion / evolution / storage / components / llm / docs / ci`

## 路线图

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 采集链全通（/events + Phoenix 拉取）、L0、契约 | ✅ |
| P1 | doc 链：转换/清洗/分块/派生/调度器 + embedding 服务 | ✅ |
| 检索口 | search_docs + /search /get | 🔜 下一步 |
| P2 | L2：TraceDeriver、facts/scenes 演化（事件链的消费端） | |
| P3 | codegen 链：code_graph、repo 采集 | |
| P4 | TS 插件、注入链 | |
