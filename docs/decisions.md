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
- 2026-08-23 清洗器原则：结构性信号优先于领域关键词（空格率≥60% 判空表、跨页重复≥3 判页眉页脚、
  字符集形态判页码、强/弱特征成串判目录），清洗器领域无关；URL/邮箱保留（是下游 LLM 的线索）；
  图片语法→[图片] 占位、图例保留（图例是图片的语义描述，占位同时是空表格判定的输入）。
- 2026-08-23 chunker 重写（891→~440 行）：表格在入口归一为 md 管道表（早渲染，删 _chunk_semantic 等
  170 行死代码）；单元序列唯一解析点 _parse_units（表格原子不与正文混切）；child 上下文注入——
  [路径: H1/H2/H3] 前缀进每个 child，表格再拼前一句作锚（真实 IDS 文档验证：标题结构缺失时
  前文锚承担分节定位）。教训：单元拼接必须补 \n，MinerU 说明行紧贴表格，粘连会毁掉表格识别。
- 2026-08-23 embedding 与 LLM 分端点部署：EmbeddingClient 独立 base_url（本地部署的 OpenAI 兼容
  /v1/embeddings，vLLM/TEI 等），LLM 走 OpenAI API；维度校验仍在写库前拦截。deriver 只依赖
  embedder.embed（鸭子类型），FakeEmbedding 替身测试。
- 2026-08-24 OTLP receiver 删除：真实 tc03 数据核验发现 docgen 的 span 经 SDK 直落 Phoenix 服务端、
  永不走 OTLP 推送——receiver 无真实消费者，protobuf 路也无法验证，预留代码是纯负担。
  将来出现直推 OTLP 的 agent 时按 git 历史恢复。两扇门变一扇：/events（TS 插件）+ Phoenix 拉。
- 2026-08-24 Phoenix 采集改 REST（替代 psycopg 读库）：POST /v1/spans 返回 Arrow IPC
  （application/x-pandas-arrow，Phoenix 官方设计），HTTP 即取、无需数据库只读账号（原方案卡点消除）。
  水位从 last_id+incomplete 改为已导 trace_id 名单（REST 无增量游标，每轮全量拉 limit=1000
  再按名单跳过；量级上来后换服务端时间过滤）。span_map 词表对真实数据核对修正：
  tool:* 前缀（非裸 tool）、tokens 取 llm.token_count.*（非 gen_ai.*）、llm_call 保留 docgen 负载。
  真实样例回归：14/16 trace 导入，2 条无 done 的半截 trace（含 46 span 中途失败运行）正确拒绝。
