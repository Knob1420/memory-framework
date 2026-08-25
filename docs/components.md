# 可插拔组件（P2+ 草案）

> 定位：**推迟决策的记录，不是已冻结的契约**。接口签名是草案——等第一个真实消费者
> 出现时再冻结，提前冻容易冻错（参数、生命周期、错误语义要那时才显形）。

## 框架的普适性靠什么（已实现）

不是靠 Protocol，是靠数据契约 + 边界翻译：

- 统一基座：信封 `{seq, ts, kind, data}` + put 三动词 + pending 状态机 + workspace 隔离
- 场景差异在边界吸收：docgen = span_map（框架内置），codegen = TS 插件映射表（客户端）
- 新 agent 接入 = 写一个翻译器推信封，**不需要本文件的任何 Protocol**

## Protocol 注册表解决什么

只有一种情况：场景要往**演化管线内部**塞自己的 deriver/extractor
（代码在场景侧仓库，不能给框架提 PR）。在那之前每个 Protocol 的状态：

| Protocol | 立项信号 | 在那之前用什么顶着 |
|---|---|---|
| L1Deriver | 出现第 2 个派生器（P2 TraceDeriver） | scheduler 里 if/elif（现在只有 derive_doc ✅） |
| yaml 注册表 | 场景侧组件必须入驻且改不了框架 | 两人一仓库，直接提 PR |
| L2Extractor | 场景要自定义抽取逻辑 | 设计上单实现跨场景复用，一个类就够 |
| L2Merger | 同上 | 同上 |
| EventHandler | 出现第 3 个 agent 且事件格式需现场翻译 | 现有两条采集路都不需要 |
| L0Processor | 出现入库策略 ≠ hash→落盘→pending 的新类型 | put_repo 直接当 storage 方法写 |

## 接口草案（P2 写 TraceDeriver 时回来修）

```python
class L1Deriver(Protocol):
    """L0 → L1 派生。异步执行，由调度器按 derived_state='pending' 调度。"""
    def supports(self, l0_type: str) -> bool: ...
    def derive(self, l0: L0Record) -> None: ...  # 产物经 storage.put_* 落库

class L2Extractor(Protocol):
    """L1 trace → facts 抽取（LLM）。"""
    def extract(self, trace: Trace) -> list[Fact]: ...

class L2Merger(Protocol):
    """facts → scene.md 重写（LLM）。同场景 facts 累积 ≥3 触发。"""
    def merge(self, workspace: str, scene: Scene, facts: list[Fact]) -> str: ...
```

（EventHandler / L0Processor 无消费者，草案已删；真需要时从 git 历史找回。）

## yaml 注册（示意，立项前不建目录）

```yaml
# config.yaml —— 场景组件一行注册，框架本体不动
workspace: docgen
components:
  l1_derivers:
    - core.DocChunkDeriver          # 框架自带
    - docgen.DocgenSummaryCacher    # docgen 侧实现
```

## 铁律

- 组件内可调 LLM（经 `llm/` 封装）和 `storage`；组件之间互不 import
