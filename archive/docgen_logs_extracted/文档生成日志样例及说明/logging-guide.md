# 怎么看跑批日志

- **创建**：2026-07-31
- **读者**：拿到一个 session 目录但不知道从哪开始看的同事
- **目的**：3 分钟学会定位"这次跑批怎么了"

---

## 一句话定位

每个 session 目录下，**先打开 `summary.md`，再看 `logs/agent_trace.md`，按需翻 `logs/llm_calls/` 里的单次调用**。这三个文件配合，能回答 90% 的问题，不用写脚本聚合 JSON。

---

## 一个真实 session 长什么样

以 `outputs_eval/exp-016-tc03-react-evidence-fix/` 为例（一次完整跑批，react 模式 + evidence 全开）。打开目录你会看到：

```
exp-016-tc03-react-evidence-fix/
├── summary.md                          ← 先看这里（运行总览）
├── trace.json                          ← 程序读的事实源（人不用直接看）
├── evaluation.json                     ← 跨 run 对比用（fill_rate / token / 耗时）
├── match_review.docx                   ← stage2 后的字段匹配预览
├── template_*_输出_*.docx              ← 最终生成的文档
├── 01_parse.json ~ 05_generate.json    ← 各 stage 中间产物（按需翻）
├── 02a_evidence.json / 02b_summary.json / 02c_plan.json
│   ← bounded Summary + Plan 阶段的中间产物
├── uploads/                            ← 用户上传的模板 + reference（原文件副本）
├── working/                            ← 中间处理文件（reference 转 markdown 等）
└── logs/
    ├── agent_trace.md                  ← 模型每步决策轨迹（第二看）
    ├── run.log                         ← runner 自身的运行日志（INFO 级）
    ├── llm_log_plan_*.log              ← 旧版 LLM 全文日志（兼容期保留，新视图不读它）
    ├── llm_log_stage2_*.log
    ├── llm_log_summary_*.log
    └── llm_calls/                      ← 单次调用完整证据（按需深挖）
        ├── 004a5298....json
        ├── 19d02b31....json
        └── ...（每个 LLM 调用一个文件）
```

文件多但层次清楚：
- **顶层**：业务产物（docx / md）
- **trace.json**：程序读的"运行轨迹"事实源
- **logs/**：人类视图 + 完整调用证据

---

## 三步走查法

### 第 1 步：打开 `summary.md`（30 秒判断全局）

这是给你看的"先看这里"。例子：

```markdown
## 1. 基本信息与结论卡片
- workflow: `docgen`
- session_id: `exp-016-tc03-react-evidence-fix`
- trace 状态: `completed`
- end-to-end elapsed: 135.94s           ← 这次跑批总共多久
- active workflow time: 135.79s         ← workflow 真在工作的时间（HITL wait 不算）
- LLM calls: 7（live 7 / replay 0）     ← 调了几次模型
- total tokens: 78,629（in 64,179 / out 14,450 / reasoning 6,016）
- retry 总数: 0                          ← 有没有重试
- tool failures: 0                       ← 有没有工具调用失败
- observability warnings: 0              ← 有没有观测层 warning
```

30 秒能判断：
- **状态** = `completed` / `error` / `interrupted`（停在 HITL） → 成功/失败/暂停
- **end-to-end elapsed** → 这次跑批用了多久
- **LLM calls** → 调了几次模型（react 模式 5-10 次，bounded 模式 8-12 次）
- **retry / tool failures / observability warnings** → 都是 0 才正常，>0 就有问题

往下翻还有：

```markdown
## 2. 阶段耗时（逻辑阶段）
| 逻辑 stage | invocation 数 | active duration (s) | 状态 |
| parse_template | 1 | 0.03 | ok |       ← 代码部分，不烧 token
| wait_parse_review （HITL 折叠） | 1 | 0.00 | ok |
| summary | 1 | 39.50 | ok |             ← summary stage 花了 40s
| plan | 1 | 121.99 | ok |               ← plan stage 花了 2 分钟（最慢）
| stage2_loop | 1 | 44.63 | ok |         ← stage2 花了 45s
|   - partitioned_stage2 | 1 | 44.63 | ok | ← 缩进行是子 stage，跟父 stage 共享时间（不重复算）
| generate_document | 1 | 0.04 | ok |    ← 代码部分
```

注意几个细节：
- **HITL wait 折叠**：`wait_parse_review` / `wait_extraction_rules` / `wait_match_review` 这些是等用户操作的占位 stage，**不代表用户真等了多久**，只是 Noop stage 自己执行的耗时（基本是 0）
- **嵌套 stage 缩进**：`partitioned_stage2` 是 `stage2_loop` 的子 stage，两者 active duration 一样（44.63s），因为子 stage 的区间完全在父 stage 内——汇总到 `active workflow time` 时只算一次，不会翻倍

再往下：

```markdown
## 3. LLM 调用与成本
| stage | call_site | calls | in | out | reasoning | attempts | occupied wall (s) | sum duration (s) |
| plan | agent_loop.plan | 3 | 36,868 | 11,760 | 9,103 | 3 | 121.93 | 121.93 |
| stage2 | agent_loop.stage2 | 5 | 45,529 | 5,384 | 1,991 | 5 | 44.46 | 68.07 |
| summary | agent_loop.plan | 1 | 5,997 | 2,985 | 708 | 1 | 27.94 | 27.94 |
```

这里有个**关键差异**：
- `stage2` 的 `occupied wall = 44.46s` 但 `sum duration = 68.07s`
- 差值 23.61s 是 **4 个 worker 并发跑出来的节省**（如果串行会要 68 秒，并发只用了 44 秒）
- `plan` / `summary` 两列相等，因为它们是单线程串行

`attempts` 列：每次调用至少 1，>1 表示重试过。

### 第 2 步：打开 `logs/agent_trace.md`（5 分钟看模型每步为什么这么做）

summary.md 告诉你"总体怎么样"，agent_trace.md 告诉你"模型每一步具体干了什么"。例子：

```markdown
## 0. 顶部索引（按 started_at 排序）
| # | started_at | call_id | stage | scope | round | duration (s) | in | out | reasoning | attempts | tool_calls | status | evidence |
| 1 | 22:55:58 | 004a5298... | summary | main | 1 | 10.82 | 5997 | 880 | 747 | 1 | tool_query_reference | ok | [link] |
| 4 | 22:57:50 | 8cdf14e2... | stage2 | worker_0 | 1 | 5.34 | 9336 | 278 | 22 | 1 | tool_update_field, tool_update_field, ... | ok | [link] |
| 5 | 22:57:50 | c865636d... | stage2 | worker_2 | 1 | 5.90 | 9415 | 362 | 18 | 1 | tool_update_field, ..., tool_skip_field | ok | [link] |
| 6 | 22:57:50 | b83dfae2... | stage2 | worker_1 | 1 | 5.89 | ... | ok | [link] |
| 7 | 22:57:50 | b9bdbe3c... | stage2 | worker_3 | 1 | 14.45 | ... | ok | [link] |
```

这张表是核心。怎么看：

- **`started_at`** 几个一样的行 → 同时启动 → 是并发（看第 4-7 行都是 `22:57:50`，4 个 worker 同时跑）
- **`scope`** 列区分 worker（main = 单线程；worker_0/1/2/3 = partitioned 并发）
- **`tool_calls`** 列 → 这一次调用模型决定调了哪些工具（`tool_update_field x5` 表示一次更新了 5 个字段；`tool_skip_field` 表示模型决定跳过某字段）
- **`evidence`** 列：
  - `[link]` → 这次调用的完整 prompt / response / thinking 已落盘，点链接直达
  - `<disabled>` → 没开 `DOCGEN_LOG_LLM_CALL_EVIDENCE=on`，只有元数据没正文（不算 warning，正常情况）
  - `<missing>` → 写入失败了（这才是 warning，需要排查）

往下翻有"按 stage 分组详情"，每条调用单独一段，含时间、模型、prompt 版本、tool_calls、token 用量、evidence 链接。

### 第 3 步：按需打开 `logs/llm_calls/<call_id>.json`（深挖某次调用）

每个文件是一次完整的模型调用快照。结构：

```json
{
  "schema_version": "1",
  "sensitive": true,
  "call_id": "cea84cfc...",
  "started_at": "2026-07-30T22:56:41.617717+00:00",
  "ended_at": "2026-07-30T22:57:49.920728+00:00",
  "duration_s": 68.3,
  "model": "glm-5.1",
  "reasoning_effort": "medium",
  "prompt_version": "v2.1",
  "messages": [
    {"role": "SystemMessage", "content": "...完整 system prompt...", "chars": 8000},
    {"role": "HumanMessage", "content": "...用户消息...", "chars": 2000}
  ],
  "response": {
    "content": "...模型回复正文...",
    "tool_calls": [{"name": "tool_save_plan", "args": {...}}],
    "thinking": "...模型的思考过程（如 provider 暴露）...",
    "chars": 7411
  },
  "usage": {
    "input_tokens": 10720,
    "output_tokens": 7411,
    "reasoning_tokens": 4582,
    "input_token_details": {"cache_read": 1344}
  },
  "attempts": [{"outcome": "success", "duration_s": 68.3}],
  "request_digest": "sha256:..."
}
```

什么时候翻这个：
- "为什么模型这次决定 skip 这个字段？" → 看 `response.tool_calls` + `thinking`
- "这次 prompt 长什么样？" → 看 `messages`
- "为什么这次重试了？" → 看 `attempts`（多条表示重试，每条带 outcome + duration）
- "这次到底烧了多少 token？" → 看 `usage`（含 reasoning 子项 + cache_read 等 provider 细节）

**注意**：这个目录默认不开（`DOCGEN_LOG_LLM_CALL_EVIDENCE` 默认 off）。需要看完整 prompt 时让跑批的人加这个 env 重跑。默认 off 是因为含完整业务正文，敏感级别高，不希望每次生产跑批都落盘。

---

## 不同问题查不同文件

| 你想知道 | 看哪个文件 |
|---|---|
| 这次跑批成功了吗？ | `summary.md` §1 结论卡片 |
| 哪个 stage 最慢？ | `summary.md` §2 阶段耗时表 |
| 哪个 stage 烧 token 最多？ | `summary.md` §3 LLM 成本表 |
| 模型调了几次？ | `summary.md` §1 `LLM calls` |
| 模型第 N 轮做了什么？ | `logs/agent_trace.md` §0 顶部索引 + §1 详情 |
| 4 个 worker 是真并发吗？ | `logs/agent_trace.md` §0（`started_at` 一样 + `scope` 不同 = 并发） |
| 模型为什么跳过这个字段？ | `logs/llm_calls/<call_id>.json` 的 `response.tool_calls` + `thinking` |
| 完整 prompt 长什么样？ | `logs/llm_calls/<call_id>.json` 的 `messages` |
| 这次跑批的 fill_rate 多少？ | `evaluation.json`（顶层 `fill_rate` 字段） |
| 跟上次跑批对比？ | 跨 run 扫 `evaluation.json`（用 jq / python 脚本聚合） |
| 模型 retry 了几次？ | `logs/agent_trace.md` §0 `attempts` 列 |
| 哪里失败了？ | `summary.md` §4 异常与降级 + `logs/agent_trace.md` §2 异常调用 |
| 字段填充结果？ | `03_match.json`（字段值 + 状态 + 来源） |
| Plan 决策？ | `02c_plan.json`（每个 target 怎么处理） |
| bounded Summary 输出？ | `02b_summary.json`（reference_summary + mode + metrics） |

---

## 常见疑问

### Q1：为什么 `summary.md` 里 hitl wait 显示 0 秒？我明明等了 10 分钟

`wait_*` stage 是 Noop stage——它本身不执行任何代码，只是流水线上的占位等用户点"确认"。所以它的"耗时"是 0 秒（stage 自身的执行时间）。

你真实等待的 10 分钟是花在前端 + 你思考 + 你睡觉上，跟 workflow 没关系。这就是为什么 `summary.md` 把 HITL wait 折叠成一行，避免误导你"workflow 卡了 10 分钟"。

`end-to-end elapsed - active workflow time` 的差值才能粗略反映"非 workflow 时间"（含 HITL wait + 进程切换开销），但不能精确等同于"用户等待时长"。

### Q2：`logs/llm_log_*.log` 跟 `logs/llm_calls/` 是什么关系？

两个都是模型调用的日志，但格式不同：

- `llm_log_*.log`：旧版自由文本格式，按 round 顺序 append 写，partitioned worker 并发时会内容交错
- `llm_calls/<call_id>.json`：新版按调用隔离的结构化 JSON，每个调用一个文件，原子写不交错

新视图（`summary.md` / `agent_trace.md`）只读 trace + `llm_calls/`，**不解析旧 `llm_log_*.log`**。旧文件保留是兼容期双写，等新链路稳定后另行退役。

### Q3：`trace.json` 我需要看吗？

一般不需要。`trace.json` 是程序读的事实源（OpenTelemetry 风格的 span 树），人直接看会眼花。

但有几个场景需要打开它：
- 排查具体的 span 字段（比如 `evidence_ref` 是否正确指向文件）
- 用 jq / python 写脚本做自定义分析（比如"统计所有 retryable_error 的 attempt"）
- 给同事 / 上游反馈问题时贴具体 span 数据

### Q4：`agent_trace.md` 里 evidence 列显示 `<disabled>` 是不是出问题了？

不是。`<disabled>` 表示这次跑批没开 `DOCGEN_LOG_LLM_CALL_EVIDENCE=on`，所以没写完整 prompt/response 正文——这是默认行为（敏感数据保护，避免生产每次跑批都落盘正文）。

只有 `<missing>` 才是 warning（开了 evidence 但写入失败），需要排查。

### Q5：`occupied wall time` 跟 `sum duration` 有什么区别？

举个具体例子：stage2 有 4 个 worker 并发跑，每个 worker 花了 ~7 秒。
- `sum duration` = 7 + 7 + 7 + 7 = 28 秒（每个调用耗时之和）
- `occupied wall time` = ~7 秒（4 个调用并发同时跑，墙上的时间只过了 7 秒）

如果串行跑，两者相等；并发跑时 `occupied wall < sum duration`，差值是并发节省。

`summary.md` §3 LLM 成本表同时展示这两个数字，让你看清"模型被占用了多久"和"墙上时间过了多久"。

### Q6：跨 run 对比怎么看？

每个 run 的 `evaluation.json` 顶层有统一字段：`fill_rate` / `total_tokens` / `estimated_cost_usd` / `total_elapsed_s`。一行 bash 扫所有 run：

```bash
printf "%-40s %8s %10s %8s %10s\n" "run" "fill%" "tokens" "cost$" "time(s"
for d in outputs_eval/exp-*/; do
  [ -f "$d/evaluation.json" ] || continue
  python3 -c "
import json
e = json.load(open('$d/evaluation.json'))
print(f'$d'.ljust(40), f'{e[\"fill_rate\"]*100:.1f}%'.rjust(8),
      f'{e[\"total_tokens\"]}'.rjust(10),
      f'{e[\"estimated_cost_usd\"]:.2f}'.rjust(8),
      f'{e[\"total_elapsed_s\"]:.1f}'.rjust(10))
"
done
```

更详细的对比见 `docs/guides/eval-infra-usage-guide.md`（评测基础设施使用手册）。

---

## 排错速查

| 症状 | 排查路径 |
|---|---|
| `summary.md` 显示 `状态: error` | 看 §4.1 Error spans 的 traceback；翻 trace.json 找对应 span 的 error 字段 |
| `observability warnings > 0` | 看 §4.5 观测缺失；常见是 evidence 写失败或 evaluation 持久化失败 |
| `retry 总数 > 0` | 看 `agent_trace.md` §0 `attempts` 列；翻 `llm_calls/<call_id>.json` 的 attempts 看 outcome |
| LLM calls 数对不上预期 | 看 `summary.md` §0 一致性 warning（如果 trace 跟 evaluation 数字不一致会出现） |
| 字段没填上 | 看 `03_match.json` 的 status 字段；UNFILLED 字段查 plan_artifact 是否 skip |
| 文档生成出来内容错 | 看 `05_generate.json` + `match_review.docx`（match 阶段输出） + final docx |
| 跑批卡住不动 | 看 `logs/run.log` 最后几行（runner 的 INFO 日志）；如果是 LLM 调用中，trace 里 span 没结束 |

---

## 一图总结

```
拿到 session 目录
        ↓
打开 summary.md（30 秒看全局）
        ↓
有具体疑问？
        ├─ 哪个 stage 慢 → §2 阶段耗时
        ├─ 哪个 stage 烧 token → §3 LLM 成本
        ├─ 模型某轮做了什么 → logs/agent_trace.md
        ├─ 模型为什么这么决策 → logs/llm_calls/<call_id>.json
        ├─ fill_rate / 跨 run 对比 → evaluation.json
        └─ 排错 → summary.md §4 + agent_trace.md §2
```

---

## 还想深入

- **设计动机**：`docs/design/active/human-readable-logging/design.md`（架构 + 7 个 ADR）
- **怎么跑批**：`docs/guides/eval-infra-usage-guide.md`（场景 1/2/3 + 命令模板）
- **代码入口**：`workflow/human_views/` 目录（summary_builder / agent_trace_builder / consistency / logical_stages）

---

## 修改记录

- 2026-07-31 v1 初始版本，基于 exp-016-tc03-react-evidence-fix 真实数据
