# Grill Sessions: Memory 框架设计审查

## 场景背景

- **团队**: 5-8 人, 卫星搭载软件工程
- **2 个 agent 场景**: docgen (文档生成) + codegen (代码生成)
- **codegen**: 别的团队做的, 已有完整 predetermined skill (流程+模板+项目说明)
- **memory 框架定位**: 不改 skill 本身, 给两个 agent 提供沉淀的知识辅助
- **架构**: L0 (raw) → L1 (derived) → L2 (knowledge) → L3 (memory.md)

---

# 第一轮 Grill: 8 个模糊点 + 5 处冗余

## 8 个模糊点

### 模糊点 1: skill 是 md 还是可执行文件?
skill 注册成 tool 但用 md 形态 → 矛盾. 可能是 prompt template (类型 c), 不是真"可执行".

### 模糊点 2: "agent 已经写好的 skill" 是 predetermined 还是 emergent?
predetermined (人工/agent 预先写) vs emergent (从 trace 沉淀) 应分开存. 混在 L2 schema 会很别扭.

### 模糊点 3: facts 从 L1 抽, 但 L1 是异构的, fact 抽什么?
从 doc_chunk 抽"TM sync marker 是 1ACFFC1D"是 chunk 复制, 不是 fact. fact 应该是跨多个 L1 的归纳.

### 模糊点 4: preference 在团队场景下是什么?
"用户偏好简洁回答"是个人偏好 (团队场景不要); "团队代码风格用 4 空格"是团队约定 (应该并入 fact 或 memory.md).

### 模糊点 5: codegen memory.md 怎么组织?
3 个 scenario 共一份 memory.md 会很大. per-scenario 还是 per-workspace 没想清楚.

### 模糊点 6: trace 不检索, 但 SOP N=3 类似 trace 怎么找?
聚类需要相似度, 相似度最自然是 embedding. 跟"trace 不检索"矛盾.

### 模糊点 7: "agent 想看可以看" 怎么实现?
召回结果要不要带 source_id? read_raw 接口是什么? 没设计好, 渐进披露是口号.

### 模糊点 8: memory 系统对 codegen (已有 skill) 的核心价值?
fact/SOP/trace 都跟 skill 重叠. memory 提供什么独特价值?

## 5 处冗余/不必要

1. **doc_wiki 起步可不做**: doc 本身是文字, chunk 就能检索. wiki 价值不如 code wiki 大.
2. **L1 wiki 依赖 ast 的派生关系**: wiki 直接从 L0 code raw 生成就行, 省一层.
3. **JSONL + SQLite 双写对起步过度**: 起步只用 SQLite + Markdown 够.
4. **fact 的 dedup (store/update/merge/skip) 起步可简化**: 直接写 + 定期清理就够.
5. **manual_edits 保护机制可能用不上**: 5-8 人小团队, LLM 重生成频率低.

---

# 第二轮 Grill: 3 个核心矛盾 + 2 个新机会

## 矛盾 1: fact 没想清楚——整个 L2 最大的设计漏洞

**对 fact 内容没有清晰认识**: 不知道长度, 不知道对 agent 作用多大.

### 真正的 fact 应该是什么
**fact = 跨多个 L1 单元的归纳性事实, 不是单个 L1 单元的复制**

3 类真正有价值的 fact:
- **team_convention**: 团队约定 (跨多个 ast/code 归纳)
- **lesson_learned**: 错误教训 (从多个 trace_error 归纳)
- **design_rationale**: 设计理由 (doc 里隐含的设计原因)

### 起步建议
**v1 只做 lesson_learned 一类**——价值最高、抽取最容易、对 agent 帮助最大.

## 矛盾 2: memory 对 codegen 的价值——"渐渐转移文档/模板"的依据是什么?

凭频率? 凭变化频率? 凭人工判断? 3 种依据各对应不同实现.

更深问题: **转移之后 skill 怎么调用 memory?** placeholder/自动召回/互不干涉, 3 选 1 没决定.

## 矛盾 3: SOP 用 trace 聚类, 但聚类需要 embedding

"trace 不检索"指的是 agent 召回不参与, 但后台 SOP 形成时仍可用 embedding 聚类. 两个用途不冲突.

聚类的"类"按什么维度? goal_type / goal 文本 / error_type / linked_l0, 没说清楚.

## 新机会 1: memory 给 codegen 的真正价值——可能是"skill 反馈建议"

不替代 skill, 而是**反馈优化 skill**:
- 从多次 trace 发现"agent 偏离 skill 的地方"
- 生成"skill 优化建议"给人看

这意味着 L2 的核心类型可能不是 fact, 是 **skill_feedback**.

## 新机会 2: memory 给 docgen 的价值——"模板优化 + 术语一致性"

docgen 没有 codegen 那种成熟 skill, memory 的价值空间更大:
- 术语一致性 (从多次生成归纳)
- 模板优化 (从用户修改痕迹发现模板缺陷)
- 风格沉淀

## L2 类型重设计建议 (基于实际场景)

```
原设计: fact / preference / sop / skill (通用分类, 抽取模糊)
建议:
  - lesson_learned: 从 trace_error 抽, 避免重蹈覆辙
  - team_convention: 从 trace/doc/code 抽, 团队隐含规则
  - style_pattern: 从生成文档/代码归纳, 团队风格
  - skill_feedback: 从 trace 偏离 skill 处抽, 给 skill 优化建议
```

4 类对实际场景都有明确价值, 比通用分类更精准.

---

# 待回答的根本问题

1. fact 抽什么? 起步只做 lesson_learned? 还是按 4 类重设计?
2. memory 对 codegen 的核心价值? 知识辅助 vs skill 反馈, 优先级?
3. skill 和 memory 衔接机制? placeholder / 自动召回 / 互不干涉?
