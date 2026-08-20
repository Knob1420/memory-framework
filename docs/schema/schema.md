# Schema 设计（#5）

> SQLite 单文件，FTS5（自带）+ sqlite-vec（扩展）。
> 共 9 张主表 + 影子表。影子表（`*_fts` / `*_vec`）由 StorageEngine 在写入主表时同步维护，
> 业务代码不可见、不可直接访问。

## 全局约定

- 主键统一 TEXT（uuid 或内容 hash），不用自增 int——便于导出、合并、跨库去重
- 所有业务表带 `workspace TEXT NOT NULL`，索引包含 workspace；`'shared'` 表示跨 workspace 共享
- 时间戳 ISO8601 文本
- 换 embedding 模型 = 换维度 = 重建全部 vec 影子表（EMBEDDING_DIM 第一天定死）

---

## L0 层

### l0_records — L0 万物元数据（异步管线的调度锚点）

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

### doc_chunks — 文档切片

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

### code_nodes — 代码符号

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

### code_edges — 符号关系边（无影子表，SQL 直查）

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

### code_files — 仓库文件清单（增量解析依据）

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

### traces — session 叙事摘要

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

### l1_artifacts — 派生产物缓存（md 类，"只存不算"的落点）

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

### facts — 单条经验事实（append-merge 型）

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

### scenes — 场景口袋书索引

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
