# decisions

一句话一条，带日期。记结论 + 原因，防止重新提议已否掉的方案。

- 2026-08-18 向量方案 sqlite-vec：单库单文件，万级向量暴力扫足够；无 ANN 索引，过百万级再换。换 embedding 模型 = 换维度 = 重建向量表。
- 2026-08-18 workspace 隔离：单 SQLite + workspace 逻辑列，不用多库。FTS 用 UNINDEXED 列 + WHERE 过滤，vec 用 over-fetch + Python 过滤。
- 2026-08-18 OTLP：接入层加 receiver，挂各 agent 已有 OTel Collector 分流，agent 侧零改动（推翻此前 OTLP YAGNI 的结论，因 docgen 统一 OTel 格式 + SDK 传输）。
- 2026-08-18 docgen 采集只用 OTLP（取消 FileSessionReader 文件级路径）：docgen 组已明确统一
  OTel+SDK 传输，文件路径不再需要；OTLP receiver 从 P4 提前到 P0，随采集端到端一起交付。
  （取代此前"文件级先行、OTLP 第二步"的两路并存方案。）
- 2026-08-18 跨平台：Linux 共同开发 + Windows 本地开发。规则：显式 encoding="utf-8"、pathlib、eol=lf（.gitattributes）、CI 只跑 Linux。
- 2026-08-18 LLM 边界：所有 LLM 调用收敛在 src/memory/llm，测试全 mock 此边界，CI 不含 secret。
- 2026-08-18 codegen 事件传输选 JSON 信封推 /events（不上 OTel JS SDK）：插件简单、试点快；
  P4 OTLP receiver 建好后可把发送层换成 OTel exporter，抓取/聚合逻辑不动（两种格式都翻译到统一信封）。
- 2026-08-18 kind 为开放枚举：未知 kind 收下落盘 + 日志警告，不拒收；场景可自定义私有 kind。
- 2026-08-18 透传约定（回应"全量捞数据"诉求）：核心 7 kind 之外，插件以 `opencode.<原名>` 透传
  原事件（data 原样），L0 零丢失；筛选取舍推迟到 TraceDeriver 消费端。理由：kind 是代码触发词
  （session_end/hitl）+ 两场景公共语言 + 源知识的唯一编码处，透传保证不丢，二者不冲突。
- 2026-08-18 OTel→信封翻译规则冻结于 docs/contracts/otel-mapping.md，OTLP receiver 消费；历史 trace.json 离线导入（如有）复用同一规则，不允许各自实现。
- 2026-08-18 内部格式用自定义信封而非 OTel：OTel 标准化的是传输与结构，语义（kind）仍需自定；
  源异构（TS 插件走 JSON）+ 消费者是 LLM（信封比 OTel 嵌套结构省 4 倍 token），故信封为枢纽、
  OTel 为边界协议（防腐层：外部标准改版只改映射表一处，不冲击演化引擎）。若所有源永远 OTel
  且消费者是链路追踪系统，则应反过来。
- 2026-08-18 data/ 一级按 workspace 物理分目录（data/<ws>/l0/...），SQLite 仍单库+workspace 列：
  物理树便于按 workspace 导出/清理/看磁盘占用，逻辑列保证查询隔离；归属真源仍是表里的 path 列。
  （推翻此前"文件目录不按 workspace 分"的表述。）
- 2026-08-20 docgen 采集改拉型：Phoenix 同步器（定时只读拉库→信封→同一 store_events），
  替代"等对方配 collector"。原因：管线天级异步本不需要实时；对方零改动（只读账号即可）；
  Phoenix 是 sink 不转发，且现无 collector。receiver（push 型）保留待命，两路汇于同一管道。
  演进链：文件级读取(FileSessionReader)→OTLP实时→Phoenix拉取，两次转向均因外部前提变化。
  关键规则：只导含 done 的完整 trace 且按 trace_id 全量一次导入（seq 一次成型，防分轮导入
  seq 撞车导致幂等误杀）；水位=自增id优先，JSON 文件存储（人可改，重置即重拉）。
