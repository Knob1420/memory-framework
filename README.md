# memory-framework

AI agent 记忆框架：跨 session 经验积累。SQLite（FTS5 + sqlite-vec）单文件存储，workspace 逻辑隔离。

七模块：transport（接入）/ orchestrator（编排）/ retrieval（检索）/ injection（注入）/ ingestion（采集）/ evolution（演化）/ storage（存储）。设计文档见 `docs/`。

## 开发环境

```bash
uv sync                          # 安装依赖（严格按 uv.lock）
cp .env.example .env             # 填入 LLM key
uv run uvicorn memory.main:app   # 启动服务
```

## 协作约定

- **main 常绿**：CI 红了优先修，再开新分支
- **PR 必须互审**：两人团队，互审是知识同步机制，不是质检
- **文件读写显式 `encoding="utf-8"`，路径一律 `pathlib`**（跨 Windows/Linux 开发的硬规则）
- 改接口/schema 的 PR 必须同时改 `docs/contracts/` 或 `docs/schema/`（契约随代码版本走）
- commit 前缀：`transport / orchestrator / retrieval / injection / ingestion / evolution / storage / components / llm / docs / ci`

## 目录

```
src/memory/     七模块源码 + components（可插拔组件 Protocol）+ llm（唯一 mock 边界）
docs/           契约、schema、设计文档（真源）
archive/        设计期过程产物（不参与构建）
data/           运行时数据目录（gitignore）
```
