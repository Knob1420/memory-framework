-- 0001: L0 层（契约 docs/contracts/storage.md / 表结构 docs/schema/schema.md）
CREATE TABLE IF NOT EXISTS l0_records (
    id            TEXT PRIMARY KEY,     -- session 类型直接用 session_id
    type          TEXT NOT NULL,        -- 'doc' | 'code' | 'session'
    workspace     TEXT NOT NULL,
    path          TEXT NOT NULL,
    content_hash  TEXT,                 -- doc/code 有；session 无（幂等靠 session_id 主键）
    meta          TEXT,                 -- JSON 开放字段
    derived_state TEXT NOT NULL DEFAULT 'pending',
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_l0_hash ON l0_records(content_hash);
CREATE INDEX IF NOT EXISTS idx_l0_state ON l0_records(type, derived_state);
