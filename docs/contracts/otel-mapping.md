# OTel span → 事件信封映射规则

> **消费者：Phoenix 同步器（REST 拉型，唯一路径）**。
> （OTLP receiver 已于 2026-08-24 删除——docgen 数据直落 Phoenix，永不推送，见 decisions.md。）
> 翻译结果是统一事件信封（见 http-api.md 事件信封一节）。
> 统一实现位于 src/memory/ingestion/span_map.py，词表已对 tc03 真实数据（901 span）核对。

## 结构映射（五条）

| OTel 侧 | 信封侧 | 说明 |
|---|---|---|
| `resource.attributes["service.name"]` | `workspace` | 如 "docgen" |
| `trace_id`（bytes → hex） | `session_id` | 一个 trace = 一个 session |
| `span_id` | 幂等去重键 | **代替 seq**：同一 spanId 重复到达 → 丢弃，不报错 |
| span 按 `start_time_unix_nano` 排序 | 派生 `seq` | OTel span 乱序到达（并发 worker），seq 由 receiver 统一编号 |
| `name="done"` 的 span | `kind="session_end"` 事件 | OTel 无会话结束概念，用 docgen 的 done span 作为触发器 |

## kind 对照

| OTel span | 信封 kind | data 内容 |
|---|---|---|
| `name="llm_call"` | `llm_call` | {tokens_in, tokens_out, docgen} ← llm.token_count.* / attributes.docgen |
| `name="tool:*"` | `tool_call` | {name} ← span 名的 tool: 后缀 |
| stage 型 span（11 个名单，tc03 全量核对） | `stage` | {name} |
| `name="done"` | `session_end` | {} |
| 其余 | `span.<name>` | attrs 原样透传（kind 开放枚举） |

## span 树关系的保留

信封是扁平的，OTel 是树（stage 嵌套 llm_call 嵌套 tool）。翻译时在 `data` 里保留
保留键 `"_span": {"id": "...", "parent": "..."}`（可选）——TraceDeriver 可用它重建
"哪个调用属于哪个阶段"，不用也可以忽略。02c_plan 等阶段产物如随 span 属性上报，
翻译为 `kind="artifact"` 事件（data.content 带结构化 items 及 reason 字段）；
历史 run 的 02c_plan.json 离线导入时复用同一翻译。

## 接入拓扑

```
当前（拉型）: docgen SDK → OTLP/protobuf → Phoenix(postgres) ←只读─ PhoenixSyncer(每5min)
备选（push）: docgen SDK → OTLP → collector →┬→ Phoenix
                                             └→ memory /otlp/v1/traces（receiver）
```

receiver 正文支持 protobuf（默认）与 OTLP/JSON 两种编码，按 Content-Type 分流
（JSON 路径注意 hex/base64 坑，见 receiver `_fix_ids`）。protobuf 解析用官方
`opentelemetry-proto` 包。
