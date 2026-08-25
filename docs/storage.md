# Storage 契约：接口 + 表结构（#6 / #5）

> 全项目唯一被允许碰 SQL 的模块。签名冻结：改接口必须走 PR 并同步更新本文件。
> 上半部分 = 接口签名，下半部分 = 表结构（原 schema/schema.md 合并于此）。
>
> **实现状态**：已实现 = put_doc / put_session / read_session / pending / put_chunks /
> mark_derived / mark_failed（L0 全部 + doc_chunks 三表）。其余为 P2/P3 规划签名。

## 三条铁律

1. **storage 永不调 LLM**——所有带 embedding 的方法，向量由调用方算好传入（mock 边界在 `src/memory/llm/`）
2. **影子表（`*_fts` / `*_vec`）对调用方不存在**——put 内部同步维护，失败整批回滚，主表与影子表永远一致
3. **所有读写隐含 workspace 过滤**——没有"查全部 workspace"的方法；跨 workspace 数据走 `workspace='shared'`

## 负设计：没有 delete

L0 append-only，L2 用 status 软删（merged/promoted/retired）。演化引擎找不到物理删除的 API——
"记忆只能沉淀和淘汰，不能篡改"压进类型系统。

---

## L0 入库

```python
def put_doc(self, content: bytes, meta: dict, workspace: str) -> L0Record
```
算 content_hash 查 l0_records：未命中 → 写文件 + 插记录（derived_state='pending'），返回
`hash_hit=False`；命中 → 不写任何东西，返回已有记录，`hash_hit=True`。
去重逻辑的全部复杂度封在此方法，调用方只看 hash_hit。

```python
def put_session(self, workspace: str, session_id: str, events: list[Event]) -> None
```
事件 append 到 data/<ws>/l0/session/<session_id>.jsonl（append-only，utf-8 显式）。

```python
def read_session(self, workspace: str, session_id: str) -> list[Event]
```
读回事件并按 seq 排序（到达序 ≠ 逻辑序，消费端排序）。幂等去重由 ingestion 层的 seq 集合完成。

```python
def pending(self, workspace: str | None = None) -> list[L0Record]
```
演化引擎轮询口：取 derived_state='pending' 的记录。异步管线的拉入口。

```python
def put_repo(self, repo_path: str, workspace: str) -> L0Record   # P3
```
拷贝仓库到 data/l0/code/<id>/，逐文件算 hash 写 code_files。不做解析（那是 CodeAstDeriver 的事）。

## L1 写入（整体替换语义：先删该 l0_id 旧数据再插入，重跑不留残渣）

```python
def put_chunks(self, chunks: list[Chunk]) -> None    # ✅ 已实现；Chunk 自带 l0_id 与 embedding
def put_code_graph(self, l0_id: str, nodes: list[CodeNode], edges: list[CodeEdge]) -> None  # P3
def put_trace(self, l0_id: str, trace: Trace) -> None                  # P2；scenario 在此落库
def put_artifact(self, l0_id: str, kind: str, cache_key: str,          # P2
                 md: str, summary: str) -> ArtifactHit
```

put_artifact 是缓存语义（与 put_doc 对称）：按 (l0_id, kind, cache_key) 查，命中返回
`hit=True`，未命中写 .md + 表行返回 `hit=False`。
docgen summary 缓存 = `put_artifact(l0_id, 'docgen_summary', f"{doc_hash}:{prompt_version}", ...)`。

四个方法成功后内部把 l0_records 置 derived（调用方不管状态位；当前实现由调度器
mark_derived / mark_failed 显式回写，P2 起收进 put_* 内部）。

## L2 读写（P2）

```python
def put_fact(self, fact: Fact) -> str          # 不做去重！去重需 LLM，是演化引擎的职责
def merge_facts(self, keep_id: str, drop_id: str) -> None   # keep.sample_count += drop 的，drop 置 merged
def promote_fact(self, fact_id: str) -> None   # 置 promoted（月级跨场景升级）
def retire_fact(self, fact_id: str) -> None    # 置 retired（confidence 掉底）
def bump_fact(self, fact_id: str, field: str) -> None
```

bump_fact 是反馈通道：field ∈ {sample_count, hitl_count, use_hits}——confidence 三路原始
计数从这里 +1（docgen fill_rate 回流、标注 HITL 确认最终都落到这）。

```python
def list_facts(self, workspace: str, scenario: str) -> list[Fact]   # SceneUpdater 的原料
def get_scene(self, workspace: str, name: str) -> Scene | None
def put_scene(self, workspace: str, name: str, md: str, summary: str) -> None
```

put_scene 写 .md + 更新表行（heat / fact_count / dirty 在此维护）。

## 检索（待开发——P1 检索口 / P3 code）

```python
@dataclass
class Hit:
    id: str
    table: str            # facts | doc_chunks | scenes | l1_artifacts | traces
    title: str
    content: str | None   # ≤200 字给全文，>200 给 None（agent 再调 get）
    score: float          # FTS rank + vec distance 融合分

def search(self, table: str, query: str, workspace: str, k: int = 5) -> list[Hit]
def get(self, table: str, id: str) -> dict | None
def graph_query(self, symbol: str, relation: str, depth: int, workspace: str) -> GraphResult
```

- search：vec 暴力扫 top-k×5（over-fetch）→ workspace 过滤 → 与 FTS 融合 → 200 字截断。
  table='code' 走 code_nodes_fts 特殊分支（只搜符号名）。
- get：按 id 拿全文，单条，无分页无批量。
- graph_query：callers / callees / impact 的递归 CTE + 返回节点（含 source）。
  code_explore 不单独给方法——它 = search('code') 后按 file_path 分组取 source，retrieval 层组装。

## 方法总账

| 类别 | 数量 |
|---|---|
| 写 | 12（各数据 put + 生命周期小方法） |
| 读 | 4（search / get / list_facts / graph_query） |
| 删 | 0（见"负设计"） |

## 数据类型（公共 dataclass，与 schema 列一一对应）

```python
@dataclass
class L0Record:    id, type, workspace, path, content_hash, meta, derived_state, hash_hit
@dataclass
class Chunk:       id, l0_id, workspace, parent_id, seq, title, summary, content, embedding
@dataclass
class CodeNode:    id, l0_id, workspace, kind, name, full_name, file_path, line_start, line_end, source
@dataclass
class CodeEdge:    id, l0_id, src_id, dst_id, kind
@dataclass
class Trace:       id, l0_id, workspace, scenario, summary, steps, errors, keywords
@dataclass
class Fact:        id, workspace, scenario, topic, type, content, llm_score, embedding
@dataclass
class Scene:       workspace, name, md_path, summary, heat, dirty, fact_count, updated_at
@dataclass
class ArtifactHit: hit: bool, path: str, summary: str
```

---

# 表结构（原 schema.md）

> SQLite 单文件，FTS5（自带）+ sqlite-vec（扩展）。
> 共 9 张主表 + 影子表。影子表（`*_fts` / `*_vec`）由 StorageEngine 在写入主表时同步维护，
> 业务代码不可见、不可直接访问。
>
> **实现状态**：全量设计，分阶段落地。当前已建 **2 张**（l0_records、doc_chunks），
> 每张表的标题行标注所属阶段（✅ = 已实现并有真实数据跑通）。

## 全局约定

- 主键统一 TEXT（uuid 或内容 hash），不用自增 int——便于导出、合并、跨库去重
- 所有业务表带 `workspace TEXT NOT NULL`，索引包含 workspace；`'shared'` 表示跨 workspace 共享
- 时间戳 ISO8601 文本
- 换 embedding 模型 = 换维度 = 重建全部 vec 影子表（EMBEDDING_DIM 第一天定死）

---

## L0 层

### l0_records — L0 万物元数据（异步管线的调度锚点）`✅ 0001_l0.sql`

```sql
CREATE TABLE l0_records (
    id            TEXT PRIMARY KEY,     -- uuid
    type          TEXT NOT NULL,        -- 'doc' | 'code' | 'session'
    workspace     TEXT NOT NULL,
    path          TEXT NOT NULL,        -- 本体位置(data 按 workspace 一级分目录):
                                        --   doc: data/<ws>/l0/doc/<id>/原始文件
                                        --   code: data/<ws>/l0/code/<id>/仓库拷贝
                                        --   session: data/<ws>/l0/session/<id>.jsonl
    content_hash  TEXT,                 -- doc/code 有；session append-only 不去重
    meta          TEXT,                 -- JSON: 文件名/大小/prompt_version 等开放字段
    derived_state TEXT DEFAULT 'pending', -- pending | derived | failed
    error         TEXT,                 -- failed 时的原因
    created_at    TEXT, updated_at    TEXT
);
CREATE INDEX idx_l0_hash ON l0_records(content_hash);
CREATE INDEX idx_l0_state ON l0_records(type, derived_state);
```

职责：put_doc 算 hash 查此表判命中；演化引擎扫 `pending` 决定派生什么；
成功置 derived，失败记 error 待重试。L0 本体在文件系统，此表只存元数据。

---

## L1 层

### doc_chunks — 文档切片 `✅ 0002_l1_doc.sql`

```sql
CREATE TABLE doc_chunks (
    id         TEXT PRIMARY KEY,
    l0_id      TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    parent_id  TEXT,                    -- parent-child 两级切片: 父=章节 子=段落
    seq        INTEGER NOT NULL,
    title      TEXT,                    -- 所属章节标题
    summary    TEXT NOT NULL,           -- 检索导向摘要（search 返回给 agent）
    content    TEXT NOT NULL,           -- 全文（get 返回）
    created_at TEXT
);
-- 影子: doc_chunks_fts(title, content, workspace UNINDEXED)
--       doc_chunks_vec(chunk_id, embedding float[1024])  -- embed content
```

### code_nodes — 代码符号 `P3`

```sql
CREATE TABLE code_nodes (
    id         TEXT PRIMARY KEY,        -- full_name 的 hash
    l0_id      TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    kind       TEXT NOT NULL,           -- function | class | method | module | struct
    name       TEXT NOT NULL,           -- 短名
    full_name  TEXT NOT NULL,           -- 限定名
    file_path  TEXT NOT NULL,
    line_start INTEGER, line_end INTEGER,
    source     TEXT NOT NULL,
    created_at TEXT
);
-- 影子: code_nodes_fts(name, workspace UNINDEXED)  -- 只索引符号名
-- 无 vec: 符号检索靠名字不靠语义
```

### code_edges — 符号关系边（无影子表，SQL 直查）`P3`

```sql
CREATE TABLE code_edges (
    id     TEXT PRIMARY KEY,
    l0_id  TEXT NOT NULL,
    src_id TEXT NOT NULL,               -- 调用方/导入方/容器
    dst_id TEXT NOT NULL,
    kind   TEXT NOT NULL                -- 'calls' | 'imports' | 'contains'
);
CREATE INDEX idx_edges_src ON code_edges(src_id, kind);  -- callees
CREATE INDEX idx_edges_dst ON code_edges(dst_id, kind);  -- callers / impact
```

impact = 反向 callers 递归 CTE：

```sql
WITH RECURSIVE up(id, depth) AS (
    SELECT src_id, 1 FROM code_edges WHERE dst_id = :target AND kind='calls'
    UNION SELECT e.src_id, up.depth+1 FROM code_edges e JOIN up ON e.dst_id=up.id
    WHERE up.depth < :max_depth
)
SELECT n.*, up.depth FROM up JOIN code_nodes n ON n.id = up.id;
```

### code_files — 仓库文件清单（增量解析依据）`P3`

```sql
CREATE TABLE code_files (
    id           TEXT PRIMARY KEY,
    l0_id        TEXT NOT NULL,
    path         TEXT NOT NULL,         -- 仓库内相对路径
    content_hash TEXT NOT NULL,         -- 上次解析时的 hash
    lang         TEXT,                  -- tree-sitter 语言名
    updated_at   TEXT
);
```

唯一职责：二次 /ingest_code 逐文件比 hash，只有变化的文件重新解析。

### traces — session 叙事摘要 `P2`

```sql
CREATE TABLE traces (
    id         TEXT PRIMARY KEY,        -- 即 session_id
    l0_id      TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    scenario   TEXT,                    -- LLM 判定的场景标签（缝合 L1/L2 的关键字段）
    summary    TEXT NOT NULL,           -- "这次任务干了什么"
    steps      TEXT,                    -- JSON 步骤列表
    errors     TEXT,                    -- JSON 报错记录
    keywords   TEXT,                    -- JSON 关键词
    created_at TEXT
);
-- 影子: traces_fts(summary, keywords, workspace) + traces_vec(embed summary)
```

L2Extractor 的唯一原料（只读此表不读原始事件流）；也是 agent 可检索的历史记录。

### l1_artifacts — 派生产物缓存（md 类，"只存不算"的落点）`P2`

```sql
CREATE TABLE l1_artifacts (
    id         TEXT PRIMARY KEY,
    l0_id      TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    kind       TEXT NOT NULL,           -- 'code_wiki' | 'docgen_summary' | 'doc_wiki'(未来)
    cache_key  TEXT NOT NULL,           -- 如 '<doc_hash>:<prompt_version>'
    path       TEXT NOT NULL,           -- .md 文件（真源）
    summary    TEXT NOT NULL,
    created_at TEXT, updated_at TEXT
);
CREATE UNIQUE INDEX idx_artifact_key ON l1_artifacts(l0_id, kind, cache_key);
-- 影子: l1_artifacts_fts(summary, workspace) + vec
```

code_wiki（我们生成）与 docgen 任务向 summary（他们生成我们缓存）同住，
靠 kind 区分。hash 命中即跳过上游阶段；prompt 升版 → cache_key 变 → 自然失效。

---

## L2 层

### facts — 单条经验事实（append-merge 型）`P2`

```sql
CREATE TABLE facts (
    id           TEXT PRIMARY KEY,
    workspace    TEXT NOT NULL,
    scenario     TEXT NOT NULL,
    topic        TEXT,
    type         TEXT NOT NULL,         -- 'workflow' | 'error' | 'finding'
    content      TEXT NOT NULL,
    sample_count INTEGER DEFAULT 0,     -- 验证次数 (confidence ×0.4)
    llm_score    REAL,                  -- LLM 自评 (×0.3)
    hitl_count   INTEGER DEFAULT 0,     -- 人工确认 (×0.2)
    use_hits     INTEGER DEFAULT 0,     -- recall 命中+任务成功 (×0.1)
    status       TEXT DEFAULT 'active', -- active | merged | promoted | retired
    created_at   TEXT, updated_at TEXT
);
-- 影子: facts_fts(content, workspace) + vec
```

同一事实反复出现不插新行，sample_count += 1。status 对应生命周期出口：
merged（去重合并留尸）、promoted（升级通用）、retired（confidence 掉底）。

### scenes — 场景口袋书索引 `P2`

```sql
CREATE TABLE scenes (
    workspace  TEXT NOT NULL,
    name       TEXT NOT NULL,
    md_path    TEXT NOT NULL,           -- .md 是真源，四分区: 工作流/踩坑/关键发现/演变过程
    summary    TEXT NOT NULL,
    heat       REAL DEFAULT 0,          -- 命中热度，load_catalog 排序
    dirty      INTEGER DEFAULT 0,       -- 跨场景升级后标脏
    fact_count INTEGER DEFAULT 0,       -- ≥3 触发 SceneUpdater 重写
    updated_at TEXT,
    PRIMARY KEY (workspace, name)
);
-- 影子: scenes_fts(summary, workspace) + vec
-- 文件位置: data/<ws>/scenes/<name>.md，≤2000 字，每 workspace ≤15 个
```

---

## 表间关系

```
l0_records ──┬── doc_chunks   (1:N)
             ├── code_files   (1:N)    code_nodes ── code_edges
             ├── traces       (1:1)
             └── l1_artifacts (1:N)
traces ──(scenario)── facts ──(scenario 聚合)── scenes
```

trace.scenario 是缝合 L1 与 L2 的关键字段——它决定 facts 归类和 scene 聚合质量，
是演化链上最需盯的一环。
