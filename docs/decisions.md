# decisions

一句话一条，带日期。记结论 + 原因，防止重新提议已否掉的方案。
被推翻的旧结论合并进最新一条（演进过程看 git log）；细节展开在 contracts/ 对应文件。

## 存储

- 08-18 向量 sqlite-vec：单库单文件，万级暴力扫够用；换 embedding 模型 = 换维度 = 重建向量表。
- 08-18 workspace 隔离：单 SQLite + 逻辑列，不多库；FTS 用 UNINDEXED+WHERE，vec 用 over-fetch+Python 过滤。
- 08-18 data/ 按 workspace 物理分目录 + 逻辑列双轨：物理树便于导出清点，查询隔离靠列，真源是 path 列。

## 采集

- 08-24 docgen 采集 = Phoenix REST 拉（/v1/spans，Arrow），零账号零改动。三转向（OTLP receiver→psycopg→REST）均因外部前提变化；receiver 已删——docgen span 直落 Phoenix 永不推送，无消费者。
- 08-24 只导含 done 的完整 trace、按 trace 一次成型：分轮导入 seq 撞车 → 幂等误杀 = 静默丢数据。水位 = 已导 trace_id 名单（JSON，删掉即重拉）。
- 08-24 v1 埋点（REST，仅元数据）/ v2 埋点（GraphQL 导出，含消息全文 + thinking）双格式并行；持续通道走哪个待与 docgen 确认。
- 08-18 codegen = TS 插件推 JSON 信封到 /events（不上 OTel SDK）：插件简单、curl 可测。
- 08-18 内部格式 = 自定义信封而非 OTel：kind 语义须自定 + 信封比 OTel 嵌套省 4 倍 token；OTel 退为边界协议（防腐层，改动只碰映射表）。
- 08-18 kind 开放枚举 + 透传约定：核心 kind 之外 `opencode.<原名>` 原样落盘，L0 零丢失，筛选推迟到消费端。

## 演化（doc 链）

- 08-23 清洗：结构性信号优先于领域关键词（空格率/跨页重复/字符集/成串）；URL 保留（下游线索）；图片→[图片] 占位、图例保留。
- 08-23 chunker：表格入口归一 md 管道表（早渲染，尺寸度量在真实内容上）；`_parse_units` 唯一解析点；child 注入 [路径]+表格前文锚——真实 IDS 验证：标题结构缺失时锚承担分节定位。教训：单元拼接必须补 \n，粘连毁表格识别。
- 08-23 embedding 与 LLM 分端点：EmbeddingClient 独立 base_url（本地 bge-m3，实测 1.6G 显存），deriver 只依赖 `.embed` 鸭子接口。

## 工程

- 08-18 LLM 边界：外部模型调用收敛 `llm/` 包，CI mock 此边界，无 secret。
- 08-18 跨平台：显式 `encoding="utf-8"`、pathlib、eol=lf、CI 只跑 Linux。
- 08-18 映射唯一实现：span→信封只在 span_map.py，禁止第二份。
