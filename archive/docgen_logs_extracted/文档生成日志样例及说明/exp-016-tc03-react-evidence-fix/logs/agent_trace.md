# Agent 决策轨迹

session_id: `exp-016-tc03-react-evidence-fix` | 状态: `completed`

## 0. 顶部索引（按 started_at 排序）

| # | started_at | call_id | stage | scope | round | duration (s) | in | out | reasoning | attempts | tool_calls | status | evidence |
|---|------------|---------|-------|-------|-------|-------------|----|----|-----------|----------|------------|--------|----------|
| 1 | 22:55:58 | 004a5298... | summary | main | 1 | 10.82 | 5997 | 880 | 747 | 1 | tool_query_reference | ok | [link](logs/llm_calls/004a52980f6f4e689482de51d1a87dbc.json) |
| 2 | 22:56:09 | 19d02b31... | summary | main | 2 | 31.75 | 9153 | 4524 | 527 | 1 | tool_save_reference_summary | ok | [link](logs/llm_calls/19d02b319ca44b1798b7a0210628d136.json) |
| 3 | 22:56:41 | cea84cfc... | plan | main | 1 | 68.30 | 10720 | 7411 | 4582 | 1 | tool_save_plan | ok | [link](logs/llm_calls/cea84cfc95a84d7aabb4ca69310b687d.json) |
| 4 | 22:57:50 | 8cdf14e2... | stage2 | worker_0 | 1 | 5.34 | 9336 | 278 | 22 | 1 | tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_update_field | ok | [link](logs/llm_calls/8cdf14e299dc402cbe4d3d30d10eb77d.json) |
| 5 | 22:57:50 | c865636d... | stage2 | worker_2 | 1 | 5.90 | 9415 | 362 | 18 | 1 | tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_skip_field | ok | [link](logs/llm_calls/c865636d11754c528e62667dfc90d55f.json) |
| 6 | 22:57:50 | b83dfae2... | stage2 | worker_1 | 1 | 5.89 | 9406 | 328 | 43 | 1 | tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_update_field | ok | [link](logs/llm_calls/b83dfae29ac443b092925d74bc9cc882.json) |
| 7 | 22:57:50 | b9bdbe3c... | stage2 | worker_3 | 1 | 14.45 | 9558 | 1261 | 677 | 1 | tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_update_field | ok | [link](logs/llm_calls/b9bdbe3c32024362b0c4e38707c9b387.json) |

并发说明：worker 是否并发由 `[start_time, end_time]` 区间重叠判断，不能要求 `started_at` 字符串完全相同；按 scope 二级分组展示。

## 1. 按 stage 分组详情

### stage=plan (1 calls)
##### call_id=cea84cfc95a84d7aabb4ca69310b687d round=1
- 时间: 2026-07-30T22:56:41.617717+00:00 → 2026-07-30T22:57:49.920728+00:00 (68.30s)
- model: glm-5.1, effort: medium, prompt_version: v2.1
- attempts: 1 (outcomes: ['success'])
- tokens: in=10720 / out=7411 / reasoning=4582
- tool_calls: tool_save_plan
- 完整证据: [logs/llm_calls/cea84cfc95a84d7aabb4ca69310b687d.json](logs/llm_calls/cea84cfc95a84d7aabb4ca69310b687d.json)

### stage=stage2 (4 calls)
#### scope=worker_0
##### call_id=8cdf14e299dc402cbe4d3d30d10eb77d round=1
- 时间: 2026-07-30T22:57:50.051674+00:00 → 2026-07-30T22:57:55.391992+00:00 (5.34s)
- model: glm-5.1, effort: medium, prompt_version: v1.5
- attempts: 1 (outcomes: ['success'])
- tokens: in=9336 / out=278 / reasoning=22
- tool_calls: tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_update_field
- 完整证据: [logs/llm_calls/8cdf14e299dc402cbe4d3d30d10eb77d.json](logs/llm_calls/8cdf14e299dc402cbe4d3d30d10eb77d.json)

#### scope=worker_1
##### call_id=b83dfae29ac443b092925d74bc9cc882 round=1
- 时间: 2026-07-30T22:57:50.059689+00:00 → 2026-07-30T22:57:55.949688+00:00 (5.89s)
- model: glm-5.1, effort: medium, prompt_version: v1.5
- attempts: 1 (outcomes: ['success'])
- tokens: in=9406 / out=328 / reasoning=43
- tool_calls: tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_update_field
- 完整证据: [logs/llm_calls/b83dfae29ac443b092925d74bc9cc882.json](logs/llm_calls/b83dfae29ac443b092925d74bc9cc882.json)

#### scope=worker_2
##### call_id=c865636d11754c528e62667dfc90d55f round=1
- 时间: 2026-07-30T22:57:50.055085+00:00 → 2026-07-30T22:57:55.958834+00:00 (5.90s)
- model: glm-5.1, effort: medium, prompt_version: v1.5
- attempts: 1 (outcomes: ['success'])
- tokens: in=9415 / out=362 / reasoning=18
- tool_calls: tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_skip_field
- 完整证据: [logs/llm_calls/c865636d11754c528e62667dfc90d55f.json](logs/llm_calls/c865636d11754c528e62667dfc90d55f.json)

#### scope=worker_3
##### call_id=b9bdbe3c32024362b0c4e38707c9b387 round=1
- 时间: 2026-07-30T22:57:50.063027+00:00 → 2026-07-30T22:58:04.510978+00:00 (14.45s)
- model: glm-5.1, effort: medium, prompt_version: v1.5
- attempts: 1 (outcomes: ['success'])
- tokens: in=9558 / out=1261 / reasoning=677
- tool_calls: tool_update_field, tool_update_field, tool_update_field, tool_update_field, tool_update_field
- 完整证据: [logs/llm_calls/b9bdbe3c32024362b0c4e38707c9b387.json](logs/llm_calls/b9bdbe3c32024362b0c4e38707c9b387.json)

### stage=summary (2 calls)
##### call_id=004a52980f6f4e689482de51d1a87dbc round=1
- 时间: 2026-07-30T22:55:58.988067+00:00 → 2026-07-30T22:56:09.810963+00:00 (10.82s)
- model: glm-5.1, effort: medium, prompt_version: v1.1
- attempts: 1 (outcomes: ['success'])
- tokens: in=5997 / out=880 / reasoning=747
- tool_calls: tool_query_reference
- 完整证据: [logs/llm_calls/004a52980f6f4e689482de51d1a87dbc.json](logs/llm_calls/004a52980f6f4e689482de51d1a87dbc.json)

##### call_id=19d02b319ca44b1798b7a0210628d136 round=2
- 时间: 2026-07-30T22:56:09.829266+00:00 → 2026-07-30T22:56:41.583639+00:00 (31.75s)
- model: glm-5.1, effort: medium, prompt_version: v1.1
- attempts: 1 (outcomes: ['success'])
- tokens: in=9153 / out=4524 / reasoning=527
- tool_calls: tool_save_reference_summary
- 完整证据: [logs/llm_calls/19d02b319ca44b1798b7a0210628d136.json](logs/llm_calls/19d02b319ca44b1798b7a0210628d136.json)
