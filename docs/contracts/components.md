# 可插拔组件契约（components）

> 场景组件实现 Protocol + yaml 注册，**不继承任何基类、不 import 框架内部**。
> 场景代码只依赖本文件定义的协议和 storage 的公共 dataclass。

## 五个 Protocol

```python
class EventHandler(Protocol):
    """L0 事件流接收，输出统一为 OTel span。"""

    def handle(self, event: dict) -> None: ...
    def session_done(self) -> L0Record | None: ...  # 返回待入库的 session，无则 None


class L0Processor(Protocol):
    """L0 导入（doc/code 文件级）。"""

    def supports(self, l0_type: str) -> bool: ...
    def ingest(self, payload: dict, workspace: str) -> L0Record: ...


class L1Deriver(Protocol):
    """L0 → L1 派生。异步执行，由演化引擎按 derived_state='pending' 调度。"""

    def supports(self, l0_type: str) -> bool: ...
    def derive(self, l0: L0Record) -> None: ...  # 产物经 storage.put_* 落库


class L2Extractor(Protocol):
    """L1 trace → facts 抽取（LLM）。唯一实现跨场景复用。"""

    def extract(self, trace: Trace) -> list[Fact]: ...


class L2Merger(Protocol):
    """facts → scene.md 重写（LLM）。同场景 facts 累积 ≥3 触发。"""

    def merge(self, workspace: str, scene: Scene, facts: list[Fact]) -> str: ...  # 返回新 md
```

## 铁律

- 组件内可以调 LLM（经 `llm/` 封装）和 `storage`，但**组件之间互不 import**
- EventHandler 量级注意：opencode 一次对话几百条事件，需聚合后再吐 span（多事件一 span）

## yaml 注册

```yaml
# config.yaml
workspace: docgen
components:
  event_handler: otel.Receiver                # OTLP receiver（docgen 路径）
  l1_derivers:
    - core.DocChunkDeriver                    # 通用库（框架自带）
    - docgen.DocgenSummaryCacher              # 场景组件（docgen 侧实现）
components:
  workspace: codegen
  event_handler: codegen.OpencodeEventHandler
  l1_derivers:
    - core.CodeAstDeriver
    - core.CodeWikiDeriver
```

新 agent 接入 = 实现协议 + yaml 一行注册，框架本体不变。

## 现有组件清单

| 组件类型 | 通用库（core） | docgen 场景 | codegen 场景 |
|---|---|---|---|
| EventHandler | — | —（走 OTLP receiver） | OpencodeEventHandler |
| L0Processor | DocProcessor / CodeProcessor | — | — |
| L1Deriver | DocChunkDeriver / CodeAstDeriver / CodeWikiDeriver / TraceDeriver | DocgenSummaryCacher | — |
| L2Extractor | TraceFactsExtractor（复用） | ← 复用 core | ← 复用 core |
| L2Merger | SceneUpdater（复用） | ← 复用 core | ← 复用 core |
