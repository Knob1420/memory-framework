# StorageEngine 接口契约（#6）

> 全项目唯一被允许碰 SQL 的模块。签名冻结：改接口必须走 PR 并同步更新本文件。
> 表结构见 [schema.md](../schema/schema.md)。
>
> **实现状态**：已实现 = put_doc / put_session / read_session / pending / put_chunks /
> mark_derived / mark_failed（L0 全部 + doc_chunks 三表）。其余为 P2/P3 规划签名。

## 三条铁律

1. **storage 永不调 LLM**——所有带 embedding 的方法，向量由调用方算好传入（mock 边界在 `src/memory/llm/`）
2. **影子表（`*_fts` / `*_vec`）对调用方不存在**——put 内部同步维护，失败整批回滚，主表与影子表永远一致
3. **所有读写隐含 workspace 过滤**——没有"查全部 workspace"的方法；跨 workspace 数据走 `workspace='shared'`

## 负设计：没有 delete

L0 append-only，L2 用 status 软删（merged/promoted/retired）。演化引擎找不到物理删除的 API——
"记忆只能沉淀和淘汰，不能篡改"压进类型系统。

---

## L0 入库

```python
def put_doc(self, content: bytes, meta: dict, workspace: str) -> L0Record
```
算 content_hash 查 l0_records：未命中 → 写文件 + 插记录（derived_state='pending'），返回
`hash_hit=False`；命中 → 不写任何东西，返回已有记录，`hash_hit=True`。
去重逻辑的全部复杂度封在此方法，调用方只看 hash_hit。

```python
def put_session(self, workspace: str, session_id: str, events: list[Event]) -> None
```
事件 append 到 data/<ws>/l0/session/<session_id>.jsonl（append-only，utf-8 显式）。

```python
def read_session(self, workspace: str, session_id: str) -> list[Event]
```
读回事件并按 seq 排序（到达序 ≠ 逻辑序，消费端排序）。幂等去重由 ingestion 层的 seq 集合完成。

```python
def pending(self, workspace: str | None = None) -> list[L0Record]
```
演化引擎轮询口：取 derived_state='pending' 的记录。异步管线的拉入口。

```python
def put_repo(self, repo_path: str, workspace: str) -> L0Record   # P3
```
拷贝仓库到 data/l0/code/<id>/，逐文件算 hash 写 code_files。不做解析（那是 CodeAstDeriver 的事）。

## L1 写入（整体替换语义：先删该 l0_id 旧数据再插入，重跑不留残渣）

```python
def put_chunks(self, chunks: list[Chunk]) -> None    # ✅ 已实现；Chunk 自带 l0_id 与 embedding
def put_code_graph(self, l0_id: str, nodes: list[CodeNode], edges: list[CodeEdge]) -> None  # P3
def put_trace(self, l0_id: str, trace: Trace) -> None                  # P2；scenario 在此落库
def put_artifact(self, l0_id: str, kind: str, cache_key: str,          # P2
                 md: str, summary: str) -> ArtifactHit
```

put_artifact 是缓存语义（与 put_doc 对称）：按 (l0_id, kind, cache_key) 查，命中返回
`hit=True`，未命中写 .md + 表行返回 `hit=False`。
docgen summary 缓存 = `put_artifact(l0_id, 'docgen_summary', f"{doc_hash}:{prompt_version}", ...)`。

四个方法成功后内部把 l0_records 置 derived（调用方不管状态位；当前实现由调度器
mark_derived / mark_failed 显式回写，P2 起收进 put_* 内部）。

## L2 读写（P2）

```python
def put_fact(self, fact: Fact) -> str          # 不做去重！去重需 LLM，是演化引擎的职责
def merge_facts(self, keep_id: str, drop_id: str) -> None   # keep.sample_count += drop 的，drop 置 merged
def promote_fact(self, fact_id: str) -> None   # 置 promoted（月级跨场景升级）
def retire_fact(self, fact_id: str) -> None    # 置 retired（confidence 掉底）
def bump_fact(self, fact_id: str, field: str) -> None
```

bump_fact 是反馈通道：field ∈ {sample_count, hitl_count, use_hits}——confidence 三路原始
计数从这里 +1（docgen fill_rate 回流、标注 HITL 确认最终都落到这）。

```python
def list_facts(self, workspace: str, scenario: str) -> list[Fact]   # SceneUpdater 的原料
def get_scene(self, workspace: str, name: str) -> Scene | None
def put_scene(self, workspace: str, name: str, md: str, summary: str) -> None
```

put_scene 写 .md + 更新表行（heat / fact_count / dirty 在此维护）。

## 检索（待开发——P1 检索口 / P3 code）

```python
@dataclass
class Hit:
    id: str
    table: str            # facts | doc_chunks | scenes | l1_artifacts | traces
    title: str
    content: str | None   # ≤200 字给全文，>200 给 None（agent 再调 get）
    score: float          # FTS rank + vec distance 融合分

def search(self, table: str, query: str, workspace: str, k: int = 5) -> list[Hit]
def get(self, table: str, id: str) -> dict | None
def graph_query(self, symbol: str, relation: str, depth: int, workspace: str) -> GraphResult
```

- search：vec 暴力扫 top-k×5（over-fetch）→ workspace 过滤 → 与 FTS 融合 → 200 字截断。
  table='code' 走 code_nodes_fts 特殊分支（只搜符号名）。
- get：按 id 拿全文，单条，无分页无批量。
- graph_query：callers / callees / impact 的递归 CTE + 返回节点（含 source）。
  code_explore 不单独给方法——它 = search('code') 后按 file_path 分组取 source，retrieval 层组装。

## 方法总账

| 类别 | 数量 |
|---|---|
| 写 | 12（各数据 put + 生命周期小方法） |
| 读 | 4（search / get / list_facts / graph_query） |
| 删 | 0（见"负设计"） |

## 数据类型（公共 dataclass，与 schema 列一一对应）

```python
@dataclass
class L0Record:    id, type, workspace, path, content_hash, meta, derived_state, hash_hit
@dataclass
class Chunk:       id, l0_id, workspace, parent_id, seq, title, summary, content, embedding
@dataclass
class CodeNode:    id, l0_id, workspace, kind, name, full_name, file_path, line_start, line_end, source
@dataclass
class CodeEdge:    id, l0_id, src_id, dst_id, kind
@dataclass
class Trace:       id, l0_id, workspace, scenario, summary, steps, errors, keywords
@dataclass
class Fact:        id, workspace, scenario, topic, type, content, llm_score, embedding
@dataclass
class Scene:       workspace, name, md_path, summary, heat, dirty, fact_count, updated_at
@dataclass
class ArtifactHit: hit: bool, path: str, summary: str
```
