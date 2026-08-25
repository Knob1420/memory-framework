# Phoenix 采集与 span → 信封映射规则

> docgen 的 agent 内嵌 OTel SDK，span 直落他们的 Phoenix 服务端（sink，不对外转发）。
> 我们拉型采集：定时拉 Phoenix → 翻译成统一事件信封（见 http-api.md 事件信封一节）→ store_events。
> 映射唯一实现：src/memory/ingestion/span_map.py，词表已对 tc03 真实数据（901 span）核对。
> （OTLP receiver 已于 2026-08-24 删除——docgen 数据永不推送、无真实消费者，见 decisions.md。）

## 数据出口（两种格式，reader 归一为同一 span dict）

| 出口 | 格式 | 真实样例（docs/example/） | 属性完整度 |
|---|---|---|---|
| REST `POST /v1/spans?project_name=<project>` | Arrow IPC（`application/x-pandas-arrow`） | docgen-real-tc03-spans.arrow | v1 埋点：元数据 |
| GraphQL 导出 JSON | dotted 拍平键 | export-otel-v2-thinking-think-rec-1.json | v2 埋点：**消息全文 + thinking** |

```jsonc
// REST 请求体
{ "queries": [{}], "limit": 1000 }
// 响应是 Arrow 字节流，pyarrow 三行解码成 DataFrame，列即展平的 span
```

## 同步规则（PhoenixSyncer）

- **只导含 done span 的完整 trace，按 trace 一次成型**：seq 从 trace 全量派生——
  分轮增量导入会 seq 撞车 → 幂等误杀 = 静默丢数据
- 失败的运行（无 done）不导。真实数据验证：16 条 trace 拒 2 条——
  1 条单 span + 1 条 46 span 的中途失败运行（失败的运行不该进记忆）
- 水位 = 已导 trace_id 名单，`data/<ws>/phoenix_sync.json`（人可改，删掉即重拉）
- 崩溃重导 → 相同排序 → 相同 seq → store_events 幂等去重，数据不翻倍
- ponytail: REST 无增量游标，每轮全量拉 limit=1000 再按名单跳过；
  量级上来后换服务端时间过滤（queries.filter 语法待验证）

## 结构映射

| Phoenix 侧 | 信封侧 | 说明 |
|---|---|---|
| `trace_id` | `session_id` | 一个 trace = 一个 session |
| span 按 `start_time` 排序 | 派生 `seq` | 并发 worker 乱序到达，统一编号 |
| `name="done"` 的 span | `kind="session_end"` | docgen 的结束标记，同时是 trace 完整性判据 |

## kind 对照

| span 名 | 信封 kind | data 内容 |
|---|---|---|
| `llm_call` | `llm_call` | {tokens_in/out, messages_in/out, thinking, call_site} ← llm.token_count.* / llm.input|output_messages.* / docgen.llm.* |
| `tool:*` | `tool_call` | {name} ← span 名的 `tool:` 后缀 |
| stage 型 span（11 个名单） | `stage` | {name} |
| `done` | `session_end` | {} |
| 其余 | `span.<name>` | attrs 原样透传（kind 开放枚举，tool 的 input/output.value 在此保留） |

## span 树关系的保留

信封是扁平的，span 是树（stage 嵌套 llm_call 嵌套 tool）。翻译时在 `data` 里保留
`"_span": {"id": "...", "parent": "..."}`——TraceDeriver 可用它重建"哪个调用属于哪个阶段"。
02c_plan 等阶段产物如随 span 属性上报，翻译为 `kind="artifact"` 事件（data.content
带结构化 items）。
