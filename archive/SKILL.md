---
name: can-protocol-log-analysis
description: "基于 ctrl_telem_api / ctrl_telem_api_test 代码模板的 CAN 协议测试日志分析方法论。提供日志锚点识别、5 维度验证、失败归因字典（[SELF_FIX]/[CODER_BUG]/环境）。使用场景：test agent Step 4.2 日志分析、Step 5 自愈循环重新分析。跨 weina/aotian/miaoyue/yinhe 等不同协议的项目通用——方法论针对代码模板，协议具体值通过查 protocol_checklist.md 获取。"
---

# CAN 协议测试日志分析方法论

## §1 适用范围

本 skill 适用于基于 **ctrl_telem_api / ctrl_telem_api_test 代码模板** 生成的 CAN 协议项目的测试日志分析。

**跨协议通用**：本 skill 提供方法论（日志锚点、验证维度、归因字典），内容针对代码模板的稳定日志结构，不依赖 weina/aotian/miaoyue/yinhe 等任何特定协议。
- **协议具体值**（指令码偏移、帧类型编码、CAN ID 字段、校验算法等）通过 §2 规则查证
- **方法论与协议事实的边界**：本 skill 只规定"如何分析"，不规定"具体协议是什么"

**消费者**：
- `can_protocol_test_agent`：Step 4.2 日志分析、Step 5 自愈循环重新分析（强制加载）
- 其他需要分析 ctrl_telem_api / ctrl_telem_api_test 测试日志的场景

## §2 协议事实查证规则（防歧义振荡 + 防错误传播）

测试日志分析中所需的协议具体值（如"0x10 是否为遥测查询指令"、"复帧首帧的长度字段位置"、"广播帧 CAN ID 构造"等），按以下规则查证：

### 默认路径：查 protocol_checklist.md

优先读取 `<项目目录路径>/ctrl_telem_api_test/protocol_checklist.md`（test agent Step 2 保存的检查清单）：
- §13 裁决表中状态为 `🔒已决` 的项、§14 取值表、§15 映射表 → **直接读值用，不得重新权衡**（决策冻结契约）
- 清单中未收录的**确定性事实**（某字节偏移、TITLE 值、DLC 等单解项）→ 允许回协议文档做**定向查找**（grep/Read 精确定位小节，禁止通篇重扫）

### 异常路径：发现 checklist 与日志现象矛盾

当归因方向与日志现象明显相反，怀疑 `protocol_checklist.md` 中某项裁决错误时：

1. 允许回协议文档做**定向查找**（仅限争议项的小节，禁止通篇重扫）
2. 落盘归因标签为 `[CHECKLIST_SUSPECT:理由]`，**不得静默改值**
3. 在测试报告"归因分析过程"中单独列出该怀疑项，提请用户介入
4. 测试结论仍按当前 checklist 推导，但报告中标注"存在 checklist 争议"

> **设计意图**：默认信任 checklist（防歧义振荡、防反复权衡），异常路径允许独立纠错（防错误传播）。`[CHECKLIST_SUSPECT]` 是显式的"提请用户裁决"通道，不阻塞测试流程。

## §3 可依赖的日志锚点

### 稳定日志（标记块外，不会因代码生成而改变）

| 锚点 | 含义 | 来源 |
|---|---|---|
| `can lib version:` | 初始化成功 | can_comu_interface.cpp [ADAPT_BEGIN] |
| `global_can_interface init error` | 初始化失败 | can_comu_interface.cpp [ADAPT_BEGIN] |
| `DEV A/B: ID: {:#x} DLC: {} Data:` + printf hex | 硬件层原始 CAN 帧收发 | can_driver.cpp（标记块外） |
| `send to id: {:#x}` + printf hex | 驱动层发送原始帧 | can_driver.cpp（标记块外） |
| `recv from id: {}` + printf hex | 驱动层接收原始帧 | can_driver.cpp（标记块外） |
| `timeout receive whole frame` | 接收超时 | can_interface.cpp（标记块外） |
| `timeout reset can dev:{}` | 超时触发总线复位 | can_interface.cpp（标记块外） |
| `CAN A/B status: telem_recv={}, notelem_recv={}, cmd_recv={}, err_frame={}, rcv_err={}, send_err={}, reset={}` | 总线统计（DEBUG 级别） | can_comu_interface.cpp（标记块外） |
| `ReceiveAndParseMsg error ret:{}` | 上层接收失败 | can_comu_interface.cpp test 函数 |
| `CAN telemetry info updated: reset_type={}` | 统计信息更新 | can_comu_interface.cpp（标记块外） |

### 约束保留日志（[PROTOCOL_BEGIN] 块内，由代码生成约束保证存在）

| 锚点 | 含义 | 约束要求 |
|---|---|---|
| 接收完整消息后的 INFO hex 日志 | 上层收到的完整消息 hex 数据 | 必须保留，禁止删除或降级 |
| 发送消息前的 INFO hex 日志 | 上层待发送数据 hex 数据 | 必须保留，禁止删除或降级 |

### 不得依赖

`can_msg_type` 数值（枚举值可能变化）、[PROTOCOL_BEGIN] 块内的日志文本内容。

## §4 验证方法论（5 维度）

### 维度 0：测试场景覆盖率验证（前置门禁，不通过则直接判失败）

在所有交互链路验证之前，先扫描主节点日志中实际发送的指令类型清单（从约束保留的发送 hex 日志中提取 `data_type`），对照 Step 2 保存的 `<项目目录路径>/ctrl_telem_api_test/protocol_checklist.md` 中 §12"测试用例覆盖矩阵"验证（覆盖率规则见《协议设计约束·测试用例覆盖说明》）：

- [ ] **遥测查询覆盖**：协议定义的所有遥测查询指令类型均已发送且收到应答（100%）
- [ ] **遥控采样数达标**：实际发送的遥控指令数 ≥ ⌈N/4⌉（N 为协议定义的遥控指令总数）
- [ ] **遥控单帧/复帧覆盖**：按约束 d 规则，若协议中遥控指令同时存在单帧和复帧，两者均已采样（至少各1条）
- [ ] **广播覆盖**：星时/时间广播（或最基础广播）已发送
- [ ] **CAN总线复位指令覆盖**：按约束 e 规则，若协议存在独立CAN复位指令，已作为独立测试用例覆盖（含"发 2 次、中间间隔其他指令、不作为末例"的细化要求）
- [ ] **CAN B 总线覆盖**：按约束 f 规则，至少 1 条用例从 CAN B 发送

**覆盖率不达标的处理**：
- 任一项不达标 → 直接判测试失败，归因 `[SELF_FIX]`，失败说明"测试用例覆盖不完整：缺少 xxx"
- 维度 0 不通过时，其他维度结果仅作记录，不影响最终失败判定

### 维度 1：初始化验证

- 两份日志均包含 `can lib version:` → 初始化成功
- 出现 `init error` / `set filter failed` / `init dev failed` → 初始化失败

### 维度 2：完整交互链路验证（核心）

对每个测试场景，验证 **"发送 → 接收 → 处理 → 响应 → 接收响应"** 完整链路。

**强制前置检查（适用于所有场景，在任何交互验证之前执行）**：

> **时间戳因果性验证**（必须首先执行，不通过则该场景直接判失败）：
> - 提取主节点发送日志的时间戳 `T_master_send`
> - 提取从节点接收日志的时间戳 `T_slave_recv`
> - 必须满足 `T_master_send <= T_slave_recv`，且 `T_slave_recv - T_master_send < 30s`
> - 若从节点接收时间戳早于主节点发送时间戳，说明从节点收到的帧来自**非当前主节点进程**（残留进程或外部干扰），该交互链路无效，直接判失败
> - 若日志中缺失时间戳无法比对，标记为"无法验证时间戳因果性"，该场景判失败

> **发送成功验证**（在验证"从节点收到"之前执行）：
> - 检查主节点日志中发送后是否出现 `Write error` / `send frame error` / `can send error`
> - 若出现发送错误，说明帧未成功发出，该交互链路在"发送"环节即断裂，直接判失败
> - **禁止**在发送失败的情况下，仅凭从节点日志中有数据接收就判定链路通过——那些数据可能来自其他来源

**遥测查询场景**：主节点发送遥测查询 → 从节点收到并自动回复遥测数据 → 主节点收到遥测响应
- **发送成功**：主节点日志中无 `Write error` / `send frame error`（检查驱动层 `send to id:` 日志之后无错误）
- **时间戳因果性**：从节点接收时间戳 ≥ 主节点发送时间戳
- 主节点约束保留的发送 hex 日志存在，确认 `data_type` 为遥测查询指令码
- 从节点约束保留的接收 hex 日志存在，指令码与主节点发送一致
- **从节点约束保留的发送 hex 日志存在**（确认触发了遥测回复行为）
- 从节点发送的遥测数据 `data_type` 与查询类型对应（快遥/慢遥/快慢遥）
- 主节点约束保留的接收 hex 日志存在，确认收到遥测响应

**遥控指令场景**：主节点发送遥控指令 → 从节点收到并上送给应用层 → 从节点发送应答帧（协议规定时）→ 主节点收到应答
- **发送成功**：主节点日志中无 `Write error` / `send frame error`
- **时间戳因果性**：从节点接收时间戳 ≥ 主节点发送时间戳
- 主节点约束保留的发送 hex 日志存在，确认 `data_type` 为遥控指令码，参数内容正确
- 从节点约束保留的接收 hex 日志存在，指令码和参数与发送一致
- 若协议要求应答：从节点约束保留的发送 hex 日志存在（应答帧）
- 主节点收到应答帧（约束保留的接收 hex 日志存在）

**广播场景**：主节点发送广播 → 从节点收到并上送给应用层
- **发送成功**：主节点日志中无 `Write error` / `send frame error`
- **时间戳因果性**：从节点接收时间戳 ≥ 主节点发送时间戳
- 主节点约束保留的发送 hex 日志存在
- 从节点约束保留的接收 hex 日志存在，数据与发送一致
- 无需回复

### 维度 3：协议处理正确性验证

通过稳定日志中的原始帧 hex（`DEV A/B` / `send to id:` / `recv from id:`），**对照 §2 查证路径**手动推演协议处理：
- **帧类型判断**：原始帧 CAN ID 中的帧类型字段是否符合协议定义的单帧/首帧/中间帧/尾帧编码
- **帧序号**：多帧传输中序号字段是否按协议规定的起始值和递增规则连续变化
- **校验位**：按协议文档规定的算法和范围手动计算校验值，与帧内校验值比对
- **消息类型分类**：按协议文档规定的分类条件（CAN ID 字段 + 数据域内容）推演，判断接收侧的分类是否正确
- **CAN ID 构造**：发送侧构造的 CAN ID 各字段（src_addr / dst_addr / multicast_type / func_code / frame_type / seq）是否符合协议文档的位域定义

> **说明**：CAN 硬件层保证帧传输的完整性，`send to id:` 和 `recv from id:` 的原始帧 hex 必然一致，无需比对传输一致性。重点在于协议层的帧构造、解析、校验、分类逻辑是否与协议文档一致。

### 维度 4：统计一致性验证

- **分类统计**：对照测试场景的发送类型和数量，验证接收侧各类别计数是否符合预期（而非仅验证总数守恒）
- **错误统计**：`err_frame = 0`，`rcv_err = 0`，`send_err = 0`
- **复位统计**：正常测试场景下 `can_reset_count = 0`；触发 CAN 复位测试用例或 CAN B 切换时复位计数应递增

> **注意**：统计总数守恒是必要条件而非充分条件。如果消息类型分类错误（如遥测查询被识别为遥控），`telem_recv + notelem_recv` 总数仍可能守恒但分类比例错误。需结合维度 2 的交互链路验证综合判断。

**从节点总线接收计数统计日志（从节点专属，主节点无需处理）**：

从节点在接收循环中会打印 A/B 双总线计数统计

字段含义（对应 `CanStatistics` 结构体）：
| 字段 | 含义 | 验证要求 |
|------|------|---------|
| `telem_recv` | 遥测请求指令计数（`TelemetryReceived`） | 等于主节点发送的遥测查询场景数（通过对应总线收到的）|
| `notelem_recv` | 非遥测请求帧计数（`NoTelemetryReceived`，含遥控指令/应答/广播）| 等于通过该总线收到的遥控+广播+复位场景总数 |
| `cmd_recv` | 遥控指令计数（`CommandReceived`，PRI=选择过程的指令）| 等于该总线收到的间接指令+数据块+CAN复位指令总数 |
| `err_frame` | 错误帧计数（`ErrFrameCount`）| 测试期间应保持 0 |
| `rcv_err` | CAN 控制器接收错误计数（硬件层面，`RecvErrorCount`）| 测试期间应保持 0 |
| `send_err` | CAN 控制器发送错误计数（硬件层面，`SendErrorCount`）| 测试期间应保持 0 |
| `reset` | CAN 总线复位计数（`CanResetCount`）| 若未触发复位场景应为 0；若触发 CAN 复位测试用例或总线切换，对应总线的 reset 计数应递增；总线一段时间无数据也将触发自动复位，reset计数增加 |

**验证方法**：
- 取**最后一条**完整 A/B 统计快照日志（即测试结束前的最终统计值，避免中途过程值干扰）
- 按 A/B 总线分别验证：
  - 若测试场景全部走 CAN A 总线（如本测试 can_channel 配置为 CAN A），则 CAN A 的 `telem_recv`/`notelem_recv`/`cmd_recv`/`reset` 应与场景数对应，CAN B 应全为 0
  - 若测试通过 CAN A、CAN B 双总线交错发送，则分别按各总线收到的场景数核对
- `rcv_err` 在测试启动初期可能因主从节点初始化时序差异出现非零值属正常现象，**不视为失败**；但若测试运行过程中持续增长则需排查

### 维度 5：错误扫描

扫描两份日志中所有 SPDLOG_ERROR 行。排除已知的正常场景（如测试结束前的超时）后，任何非预期错误均标记为测试失败。

**致命错误（无条件判失败，不得归入"正常场景"）**：
- `Write error` / `send frame error` / `can send error`：CAN 帧发送失败，说明交互链路在发送环节即断裂。即使从节点日志中有接收记录，也不能视为链路通过（从节点接收的帧可能来自残留进程或其他来源）。
- `bad_any_cast`：参数解析类型不匹配
- `Segmentation fault` / `Aborted`：进程崩溃

**可排除的正常错误（仅在能明确归因时排除）**：
- 测试进程被 pkill 终止时产生的最后一次 `timeout receive whole frame`（出现在测试持续时间结束附近）
- 从节点在主节点启动前的初始等待阶段的 `timeout receive whole frame`（出现在主节点第一条日志之前）

### 维度 6：cmd_code 校验（仅 slave 日志，弥补测试盲区）

> **适用范围**：仅 `ctrl_telem_slave.log`。主节点日志不做此校验（master 是发送方，不涉及业务指令码解析上送）。
> **设计意图**：维度 2/3 验证的是协议层交互（收发/应答/校验/CAN ID），无法发现"业务指令码取错字段"类错误（如把协议封装字段当业务指令码上送）。本维度专门弥补这一测试盲区，是发现该类错误的唯一自动化手段。

#### 校验依据

slave 日志中的 cmd_code 打印值（来自约束 5c 保留的 `parsed_data: cmd_code=...` / `telem_query: cmd_code=...` / `can_reset: cmd_code=...` 行）。

**注意**：本维度不依赖外部清单（test agent 受认知隔离约束，禁止访问 `ctrl_telem_api/protocol_checklist.md`）。所有校验仅基于 slave 日志自身内容 + 协议文档（按 §2 规则查证）。

#### 校验项

**1. 区分性校验（核心·检测"取错字段"）**

对 slave 日志中**同一消息类型**（按 `frame_type` 字段值归类，如所有 `REMOTE_CONTROL`、所有 `BROADCAST`）的所有 `cmd_code` 取值：
- 若该类型下**有多条** cmd_code 记录，但取值**完全相同** → `[CODER_BUG]`，归因"从节点 ReceiveAndParseMsg 把协议封装字段（同类业务取值恒定者）当成了业务指令码"。
- 典型症状：所有间接指令 cmd_code 都是同一封装字段值、所有数据块 cmd_code 都是同一封装字段值。
- 例外：协议中该消息类型本身只有一种业务指令（无同类区分需求）时不判失败，但需在报告中注明"该类仅一种业务指令，区分性无法验证"。

**2. 纯净性校验（辅助·检测 param_start 偏移不够）**

结合维度 3 已分析的原始帧 hex + slave 日志中的 `data_len` 值：
- 若 slave 日志中某指令的 `data_len` 比"按协议帧格式推导的纯参数长度"多出"指令码长度" → `[CODER_BUG]`，归因"param_start 未跳过指令码全长，pdata 首字节仍是指令码延续"。
- 推导方法：从原始帧 hex 计算业务数据总长，减去指令码长度，应等于日志中的 data_len。

**3. 不上送类日志前缀校验（检测违反约束 5c）**

扫描 slave 日志中是否存在以下前缀的行：
- `telem_query: cmd_code=`：每个遥测查询场景必须有一条
- `can_reset: cmd_code=`：每个独立复位场景必须有一条

- 遥测查询场景数（来自维度 0/2 已统计）≠ `telem_query:` 行数 → `[CODER_BUG]`，归因"违反约束 5c，不上送类未打印帧识别值日志"。
- 独立复位场景数 ≠ `can_reset:` 行数 → 同上。

#### 校验结果处理

- 任一项判 `[CODER_BUG]` → 维度 6 不通过，按 §6 归因字典第 13 条处理。
- 维度 6 不通过 → 测试整体判失败（见 §5 判定标准）。

## §5 判定标准

**测试通过条件（必须全部满足）**：
1. 维度 0 测试场景覆盖率验证通过（测试用例覆盖矩阵的所有要求均满足）
2. 两节点均初始化成功
3. 每个测试场景的完整交互链路验证通过（发送→接收→处理→响应→接收响应）
4. 从节点对各类消息的处理行为符合协议规定（遥测查询触发回复、遥控指令正确上送、广播正确接收）
5. 原始帧的协议处理（帧类型/序号/校验/消息分类/CAN ID构造）与协议文档一致
6. 双方分类统计符合测试场景预期，`err_frame = 0`，`rcv_err = 0`，`send_err = 0`
7. 双方无非预期的 SPDLOG_ERROR
8. slave 日志 cmd_code 校验通过（维度 6：区分性/纯净性/不上送类前缀）

**测试失败条件（触发任一项）**：
1. 维度 0 测试场景覆盖率验证不通过（测试用例覆盖不完整：缺少 xxx 类型/数量不达标）
2. 任一节点初始化失败
3. 交互链路中任一环节缺失（发送了但未收到、收到了但未正确处理、应回复但未回复）
4. 协议处理与文档不符（帧构造/解析/校验/分类错误）
5. 消息处理行为与协议规定不符
6. 统计不一致或存在错误帧
7. 非预期 SPDLOG_ERROR
8. slave 日志 cmd_code 校验未通过（同类下取值恒定/param_start 偏移不够/不上送类未打印日志）

满足全部通过条件 → **测试通过**
触发任一失败条件 → **测试失败**

## §6 失败归因字典

| # | 现象 | 归因 | 标签 | 处理 |
|---|---|---|---|---|
| 1 | 编译错误 | [PROTOCOL_BEGIN] 块内代码语法错误或引用了不存在的符号 | `[SELF_FIX]` 或 `[CODER_BUG]` | 按编译报错行号修复标记块内代码。主节点编译失败 → `[SELF_FIX]`；从节点编译失败 → `[CODER_BUG]` |
| 2 | 初始化失败（`init error` / `set filter failed`） | 过滤器构造逻辑或 CAN ID 宏与协议不符 | `[SELF_FIX]` 或 `[CODER_BUG]` | 主节点初始化失败 → `[SELF_FIX]`；从节点 → `[CODER_BUG]`。对照协议文档检查过滤器 ID/MASK |
| 3 | 帧校验失败（`check single/multi frame error`） | 发送侧校验算法与协议不符，或接收侧校验验证逻辑与协议不符 | `[SELF_FIX]` 或 `[CODER_BUG]` | 取接收侧原始帧 hex 按协议文档手动计算校验值：计算结果 ≠ 帧内校验值 → 发送侧构造错误；计算结果 = 帧内校验值 → 接收侧验证逻辑错误。主节点为发送侧时 → `[SELF_FIX]`；从节点为发送侧时 → `[CODER_BUG]` |
| 4 | 消息类型分类错误（如遥测查询被识别为遥控） | 接收侧 `can_interface.cpp` [PROTOCOL_BEGIN] 消息分类逻辑与协议不符 | `[SELF_FIX]` 或 `[CODER_BUG]` | 主节点接收侧分类错误 → `[SELF_FIX]`；从节点接收侧 → `[CODER_BUG]`。对照协议文档检查分类条件 |
| 5 | 消息处理行为错误（遥测查询未触发回复、遥控未上送、应答帧未发送、广播未上送等） | 节点 `can_comu_interface.cpp` [PROTOCOL_BEGIN] `ReceiveAndParseMsg()` 分发逻辑与协议不符 | `[SELF_FIX]` 或 `[CODER_BUG]` | 对照协议文档检查各消息类型的处理分支。归因方按错误方判定 |
| 6 | 统计不一致或 `ErrFrameCount > 0` | 计数触发条件、分类逻辑或错误路径与协议不符 | `[SELF_FIX]` 或 `[CODER_BUG]` | 检查统计错误方的 [PROTOCOL_BEGIN] 统计更新逻辑。主节点 → `[SELF_FIX]`；从节点 → `[CODER_BUG]` |
| 7 | 持续超时（`timeout receive whole frame`） | 发送侧未发送、或发送的 CAN ID 被接收侧过滤器拒绝、或总线物理不通 | `[SELF_FIX]` 或 `[CODER_BUG]` 或环境 | 确认发送侧有 `send to id:` 日志；若有，用原始帧 CAN ID 与接收侧过滤器做位运算验证是否匹配 |
| 8 | 遥测缓存为空（`fast/slow telem empty`） | 从节点 `CanComuTest()` 未调用 `UpdateTelem()` 或调用顺序错误 | `[CODER_BUG]` | 检查从节点 `CanComuTest()` [PROTOCOL_BEGIN] 中 `UpdateTelem()` 调用 |
| 9 | 主节点发送失败（`Write error` / `send frame error` / `can send error`） | CAN 设备未正确初始化、总线物理连接断开、或 CAN ID 构造非法被 SocketCAN 拒绝 | `[SELF_FIX]` 或环境 | 检查 CAN 设备是否 up；检查 CAN ID 是否合法（如标准帧不应设置 `CAN_EFF_FLAG`） |
| 10 | 时间戳因果性不满足（从节点接收早于主节点发送） | 从节点收到的帧来自残留进程或外部干扰，非当前主节点发送 | 环境或 `[SELF_FIX]` | 检查 test.sh 是否正确清理残留进程；确认测试期间无其他 CAN 节点在发送 |
| 11 | 维度 0 覆盖率不达标（遥测查询未全量覆盖/遥控采样数不足/缺单帧或复帧格式/缺广播/缺应覆盖的特殊指令/缺 CAN B 总线用例/复位用例未发 2 次） | 主节点 `CanComuTest()` [PROTOCOL_BEGIN] 未按协议检查清单 §12"测试用例覆盖矩阵"实现 | `[SELF_FIX]` | 对照协议检查清单 §12 补齐缺失的测试用例分支 |
| 12 | 归因方向与日志现象相反，怀疑 checklist 裁决错误 | checklist §13 中某项 `🔒已决` 与协议文档冲突 | `[CHECKLIST_SUSPECT:理由]` | 按本 skill §2 异常路径处理：定向回查协议文档 → 在报告中单独列出怀疑项 → 提请用户介入，不静默改值 |
| 13 | slave 日志 cmd_code 同类下取值恒定 / data_len 多出指令码长度 / 不上送类缺前缀日志 | 从节点 `ReceiveAndParseMsg` [PROTOCOL_BEGIN] 把协议封装字段（同类业务取值恒定者）当业务指令码，或 param_start 未跳过指令码全长，或违反约束 5c 不上送类未打印日志 | `[CODER_BUG]` | 对照协议文档的指令代号表 + §4.3 帧格式表，修复从节点 ReceiveAndParseMsg 中 data_type/pdata/data_len 提取逻辑；遥测/复位路径补打 `telem_query:` / `can_reset:` 前缀日志 |
