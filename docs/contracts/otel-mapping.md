# OTel span → 事件信封映射规则

> **消费者：OTLP receiver（P0）**。翻译结果是统一事件信封（见 http-api.md 事件信封一节）。
> 历史 run 的 trace.json 离线导入（如有）复用同一规则，不允许另行实现。

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
| `name="llm_call"` | `llm_call` | {stage, tokens_in, tokens_out} ← gen_ai.usage.* |
| `name="tool"` | `tool_call` | {name} ← tool.name 属性 |
| stage 型 span（parse_template/summary/plan/stage2_loop/...） | `stage` | {name} |
| `name="done"` | `session_end` | {} |

## span 树关系的保留

信封是扁平的，OTel 是树（stage 嵌套 llm_call 嵌套 tool）。翻译时在 `data` 里保留
保留键 `"_span": {"id": "...", "parent": "..."}`（可选）——TraceDeriver 可用它重建
"哪个调用属于哪个阶段"，不用也可以忽略。02c_plan 等阶段产物如随 span 属性上报，
翻译为 `kind="artifact"` 事件（data.content 带结构化 items 及 reason 字段）；
历史 run 的 02c_plan.json 离线导入时复用同一翻译。

## 接入拓扑

```
docgen SDK → OTLP → docgen 的 Collector →（exporter 加一段）→ memory /otlp/v1/traces
```

memory 侧表现为一个标准 OTLP 后端；正文支持 protobuf（默认）与 OTLP/JSON 两种编码，
按 Content-Type 分流。protobuf 解析用官方 `opentelemetry-proto` 包（P4 加入依赖）。
