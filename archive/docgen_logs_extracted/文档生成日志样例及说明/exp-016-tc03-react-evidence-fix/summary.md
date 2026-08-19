# Session 运行报告

## 1. 基本信息与结论卡片

- workflow: `docgen`
- session_id: `exp-016-tc03-react-evidence-fix`
- trace 状态: `completed`
- 当前 stage: `done`
- 产物: `outputs_eval/exp-016-tc03-react-evidence-fix/template_template_输出_20260731_065804.docx`
- end-to-end elapsed: 135.94s
- active workflow time: 135.78s
- LLM calls: 7（live `7` / replay `0`）
- total tokens: 78,629（in `63,585` / out `15,044` / reasoning `6,616`）
- retry 总数: 0
- tool failures: 0
- observability warnings: 0

### 字段/表格填充率
- 字段: 19/19 成功（0 失败）
- 表格: 0/0 成功（0 失败）

## 2. 阶段耗时（逻辑阶段）

| 逻辑 stage | invocation 数 | active duration (s) | 状态 |
|------------|---------------|---------------------|------|
| parse_template | 1 | 0.03 | ok |
| wait_parse_review （HITL 折叠） | 1 | 0.00 | ok |
| extract_reference | 1 | 0.05 | ok |
| wait_extraction_rules （HITL 折叠） | 1 | 0.00 | ok |
| summary | 1 | 52.71 | ok |
| plan | 1 | 68.33 | ok |
| stage2_loop | 1 | 14.62 | ok |
|   - partitioned_stage2 | 1 | 14.62 | ok |
| wait_match_review （HITL 折叠） | 1 | 0.00 | ok |
| generate_document | 1 | 0.04 | ok |
| done | 1 | 0.00 | ok |

说明：active duration 是 monotonic interval 并集长度，自动消去父子嵌套与并发重叠。`wait_*` 表示 Noop stage 自身执行，不等于用户 HITL 等待时长。

## 3. LLM 调用与成本

| stage | call_site | calls | in | out | reasoning | attempts | occupied wall (s) | sum duration (s) |
|-------|-----------|-------|-----|-----|-----------|----------|--------------------|------------------|
| plan | agent_loop.plan | 1 | 10,720 | 7,411 | 4,582 | 1 | 68.30 | 68.30 |
| stage2 | agent_loop.stage2 | 4 | 37,715 | 2,229 | 760 | 4 | 14.46 | 31.58 |
| summary | agent_loop.plan | 2 | 15,150 | 5,404 | 1,274 | 2 | 42.58 | 42.58 |

并发说明：`occupied wall time` 是同组 LLM span `[start, end]` 区间并集长度；`sum duration` 是各调用时长之和。仅当区间实际重叠时两者不同；`sum - union` 表示重叠量。

## 5. 证据导航

- trace.json: `outputs_eval/exp-016-tc03-react-evidence-fix/trace.json`
- evaluation.json: `outputs_eval/exp-016-tc03-react-evidence-fix/evaluation.json`
- agent_trace.md: `outputs_eval/exp-016-tc03-react-evidence-fix/logs/agent_trace.md`
- 单调用证据目录: `outputs_eval/exp-016-tc03-react-evidence-fix/logs/llm_calls`（7 个 `<call_id>.json`）
- 最终产物: `outputs_eval/exp-016-tc03-react-evidence-fix/template_template_输出_20260731_065804.docx`
