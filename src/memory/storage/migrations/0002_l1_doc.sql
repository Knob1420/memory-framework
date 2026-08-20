-- 0002: L1 文档链（doc_chunks + FTS 影子表；vec 影子表维度依赖配置，在 engine 内创建）
CREATE TABLE IF NOT EXISTS doc_chunks (
    id         TEXT PRIMARY KEY,     -- {l0_id}_p{i} / {l0_id}_c{j}（chunker 产出的 chunk_id）
    l0_id      TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    parent_id  TEXT,                 -- NULL=parent chunk；非NULL=child（检索主力）
    seq        INTEGER NOT NULL,     -- parent 内 child 序号
    title      TEXT,                 -- 所属章节标题（path prefix）
    summary    TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_l0 ON doc_chunks(l0_id);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
    content,
    title,
    chunk_id UNINDEXED,
    workspace UNINDEXED
);
