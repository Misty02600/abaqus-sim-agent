# 科研与工业 Agent 项目参考价值

整理日期：2026-09-13。上游基线：2026-08-21 保存的固定源码快照。返回[调研索引](README.md)。

本文集中保存领域 Agent 项目调研中可用于本项目的实现经验，回答“具体值得读什么、解决什么问题、
与现有能力有什么重叠、参考到什么程度”。AbaqusAgent 已有独立的[论文与源码笔记](AbaqusAgent论文与源码详读.md)，
这里重点补齐其余五个项目。书目与论文阅读入口见[相关论文与阅读记录](相关论文与阅读记录.md)。

证据来自已读取的固定版本源码、参考快照清单及本项目当前文档。本次整理未运行这些上游项目的完整测试、
真实 CAD/CAE 工作流或对照实验；源码存在某种机制，不等于已经证明它能提高本项目任务成功率。
下文的“可借鉴”是研究判断，不表示已迁入实现或已采纳新的架构决定。

## 先看哪些内容

| 项目 | 值得保留的具体机制 | 对本项目的增量价值 | 阅读定位 |
|---|---|---|---|
| pymechanical-mcp | 按操作主题提供指导，补充对象准备、调用顺序、单位与结果读取条件 | 帮助改进领域知识的组织和返回粒度；单个 API 签名不足以表达完整操作 | 优先阅读主题指导与触发约定 |
| CadAgent | 根据首次运行、质量失败、重复错误和临近上限注入不同提示，限制 finish 重试 | 对照已有自动报错证据，检查重复失败是否产生了新信息、何时应停止 | 有局部实现价值的补充案例 |
| sim-plugin-abaqus | 文件持久化与常驻 CAE 两种增量建模会话，配合模型和作业检查 | 研究已有模型的局部修改、重启成本和状态一致性 | 出现增量建模需求时重点参考 |
| OpenHands SDK | 事件持久化、会话生命周期与恢复 | 对照已经实现的 Harness 续跑，检查外部作业与会话记录的恢复边界 | 按恢复场景查阅，不必另换框架 |
| mini-swe-agent | 简洁的模型—执行—反馈循环、预算检查和轨迹保存 | 理解基础职责，并为领域增强设计公平的消融对照 | 原理与实验方法参考 |

是否有论文和是否值得参考是两个问题。一个工程仓库可以提供有用的局部机制，但不足以充当工业可靠性
对标；论文中的实验结论也不能自动转移到另一个项目、版本或本项目任务上。

## 1. pymechanical-mcp：把完整操作知识按主题交付

### 已观察到的实现

`get_guidelines_for(content)` 通过明确的主题映射返回预先编写的指导，并非向量检索。
主题包括 workflow、geometry、materials、meshing、analysis_setup、boundary_conditions、solution、
postprocessing、named_selections 和 general。

网格主题不仅给出一个方法名，还包含获取 `Model.Mesh`、设置全局 `ElementSize`、针对 Named Selection
设置局部 sizing，以及生成网格的组合示例。提示词还提醒使用 `Quantity`、命名选择和正确的结果读取顺序，
例如先求值再读取结果。

其提示词要求生成脚本前查询相关指导，工具说明约定每个主题查询一次。这是提示词和工具描述中的约定，
不能据此认定宿主强制执行，也不能认定它已解决主动检索调用率问题。

实现还区分 Mechanical 内的脚本执行与独立、持久 Python 环境中的数据处理，并管理软件连接及工具可见性。
本项目当前的直接收益主要来自指导内容，而非照搬完整会话管理。

### 对本项目有什么用

Exact API Catalog 适合回答符号、参数、对象归属；主题指导适合回答“一组操作怎样组合起来”。例如，
围绕网格控制返回区域选择、对象准备、适用条件、调用顺序和检查方式，比只给出 `setElementType` 签名更完整。
这可作为 `search_abaqus_docs` 证据组织方式的参考。

需要区分两种知识来源：主题指导是经过整理的操作材料，文档检索是从已有语料找证据。不能仅更换工具名称，
就声称已经拥有脚本指南覆盖；也不能直接把 Mechanical 的对象模型、示例和规则用于 Abaqus。

当前项目已有按需文档工具和错误触发证据，部分查证策略还有显式实验 profile。是否整理主题指南、何时提示
模型查询，应结合已有检索缺陷和固定任务评测判断，不必新增一个分类 Agent。

### 来源

- [主题映射与查询入口](https://github.com/ansys/pymechanical-mcp/blob/3c3ce7009872051a51af48fbff49b3f62e0f28e0/src/ansys/mechanical/mcp/contexts.py#L1225)：`_CONTENT_MAP`、`get_guidelines_for`。
- [网格指导](https://github.com/ansys/pymechanical-mcp/blob/3c3ce7009872051a51af48fbff49b3f62e0f28e0/src/ansys/mechanical/mcp/contexts.py#L313)与[提示词约定](https://github.com/ansys/pymechanical-mcp/blob/3c3ce7009872051a51af48fbff49b3f62e0f28e0/src/ansys/mechanical/mcp/prompts.py#L55)。
- [主题工具注册](https://github.com/ansys/pymechanical-mcp/blob/3c3ce7009872051a51af48fbff49b3f62e0f28e0/src/ansys/mechanical/mcp/toolsets.py#L154)与[执行及连接工具](https://github.com/ansys/pymechanical-mcp/blob/3c3ce7009872051a51af48fbff49b3f62e0f28e0/src/ansys/mechanical/mcp/tools.py)。

## 2. CadAgent：让重复失败获得不同反馈，并限制结束重试

这里指 `SSSSSia/CadAgent`，不是名称相近的 CADCodeVerify。

### 已观察到的实现

`_build_context` 持续附带当前参数和文档状态，并根据运行状态追加不同指导：首次迭代、质量失败、近期多次错误、
通过但有警告，以及接近迭代上限。错误结果仍保留原始文本，并用截断文本判断重复；实现保留有限条近期错误，
重复时再加入定向修复提示或要求改变方法。

质量状态部分依赖 `execute_code` 返回文本中的 `OK:`、`FAIL:`、`ERROR:`。`finish` 会经过质量门禁；
达到三次门禁拦截后以失败结束。这提供了可以检查的反馈与停止实现，但不是严格结构化的执行协议。

### 对本项目有什么用

本项目已经有 API 错误触发证据、Solver diagnostic advisory 和宿主验证反馈。新增价值是检查：同一失败再次出现时，
模型究竟收到新的定位信息，还是只重复收到相同文档。

可用于对照评估的候选行为是：第一次错误提供相应证据；再次出现同类错误时，关联上次证据与脚本改动，说明
仍未满足的条件；持续无进展时以明确原因结束，同时保留原始日志。错误类别、API 符号和脚本版本可以作为
比文本前缀更明确的比较依据，但这一改进设想还需要在本项目中验证。

不要机械照抄其阈值和几何规则。该项目包含固定重叠尺寸等假设，不能推广到任意尺度的几何或有限元模型；
其质量检查主要检查实体、拓扑、体积与尺寸异常，不能自动验收用户所要求的孔数、孔距、壁厚或物理响应。
部分集成测试使用 FreeCAD/Part mock，因此不能用这些测试推断真实 CAD 任务通过率。

结论是保留“状态触发的证据、重复失败处理、结束门禁”三个局部机制；该快照不足以作为通用工业可靠性证明。

### 来源

- [上下文构造](https://github.com/SSSSSia/CadAgent/blob/a73168b1f2f9ea47b78eef82506069a8e0894485/agent/loop.py#L123)：`_build_context`。
- [工具反馈与重复错误处理](https://github.com/SSSSSia/CadAgent/blob/a73168b1f2f9ea47b78eef82506069a8e0894485/agent/loop.py#L297)及[finish 门禁](https://github.com/SSSSSia/CadAgent/blob/a73168b1f2f9ea47b78eef82506069a8e0894485/agent/loop.py#L364)。
- [修复指导](https://github.com/SSSSSia/CadAgent/blob/a73168b1f2f9ea47b78eef82506069a8e0894485/agent/references.py)、[质量检查](https://github.com/SSSSSia/CadAgent/blob/a73168b1f2f9ea47b78eef82506069a8e0894485/core/quality.py)与[mock 测试边界](https://github.com/SSSSSia/CadAgent/blob/a73168b1f2f9ea47b78eef82506069a8e0894485/tests/test_tool_quality_integration.py#L25)。

## 3. sim-plugin-abaqus：比较两种增量建模会话

### 已观察到的实现

除了完整 INP 批执行和完整 CAE Python/noGUI 执行，该项目还提供 iterative CAE authoring session：

| 方式 | 状态如何保留 | 值得检查的取舍 |
|---|---|---|
| file-backed | 每个片段启动 CAE，加载会话 `.cae`，执行修改后保存 | 重启成本、保存失败后的状态、磁盘模型与片段执行结果是否一致 |
| bridge | 使用常驻本地 noGUI CAE 进程，连续执行片段 | 内存状态、进程失效、连接生命周期和会话恢复 |

实现提供模型摘要、会话健康检查、作业信息、日志及产物检查，也有需要真实 Abaqus 环境的端到端测试入口。
这些入口在本次调研中未执行，不能视作已经在本机 Abaqus 2024 上验证。

### 对本项目有什么用

典型问题是“保留已有几何和材料，只修改网格或边界条件，再查看当前模型”。该项目提供了比较文件持久化
和常驻进程两条路线的具体实现，而不是只提供一个调用求解器的包装。

本项目已经有三阶段执行和 verified-seed 脚本变更。已有脚本的修改与重跑，不等于常驻 CAE 中的增量建模。
若完整脚本方式已满足当前任务，launcher、日志收集等重叠部分的新增收益有限；会话式方案应由真实的局部
编辑或启动成本问题触发，再验证状态一致性与故障恢复。

任何未来适配都需保留本项目的执行权限、预算和独立验收边界，不能因为上游支持多种路径，就把建模阶段
直接提交 Job 当成本项目的新默认行为。

### 来源

- [执行路径及两种会话方式](https://github.com/svd-ai-lab/sim-plugin-abaqus/blob/4118f73841245dc7890f9d526da8d7415e455d98/README.md#L33)。
- [执行驱动](https://github.com/svd-ai-lab/sim-plugin-abaqus/blob/4118f73841245dc7890f9d526da8d7415e455d98/src/sim_plugin_abaqus/driver.py)：持久化、health、lint、artifacts、bridge、模型摘要等实现。
- [端到端测试入口](https://github.com/svd-ai-lab/sim-plugin-abaqus/blob/4118f73841245dc7890f9d526da8d7415e455d98/tests/test_abaqus_e2e.py)。

## 4. OpenHands SDK：对照会话恢复与外部副作用

### 已观察到的实现

`EventLog` 提供事件追加、序列化及持久化，并处理线程/进程访问；本地会话包含 create-or-resume 逻辑，
恢复会话状态和工具规格，持续保存事件。锁与文件系统有适用边界，例如源码明确提醒 NFS 上文件锁的限制。

### 对本项目有什么用

最值得对照的情形是：模型请求已经产生提交意图，但客户端退出时，Abaqus 作业究竟是否已经开始执行。
会话中“记录了什么”和外部软件“实际完成了什么”需要分别判断，否则恢复时可能重复执行或错误地跳过工作。

当前项目已经有 Harness step persistence、累计预算续跑、amendment/task revision，以及未决 Abaqus effect
进入 `needs_reconcile` 的边界。因此，不应再把“增加基本续跑”列为尚未实现的收益。OpenHands 的价值在于
对照事件完整性、恢复状态和外部副作用处理，而非引入第二套 Agent SDK。

恢复会话不自动提供 Abaqus Job 重新附着、人工对账后解锁或 exactly-once 执行；这些能力必须独立实现和验证。
当前事实以本项目[续跑决定](../adr/0020-resume-coding-runs-with-harness-step-persistence.md)和
[任务修订决定](../adr/0021-use-amendments-and-task-revisions-for-continuation.md)为准。

### 来源

- [事件存储](https://github.com/OpenHands/software-agent-sdk/blob/013fba46279888f6f0040cd3a6affb2831482cbd/openhands-sdk/openhands/sdk/conversation/event_store.py#L30)：`EventLog`。
- [本地会话恢复](https://github.com/OpenHands/software-agent-sdk/blob/013fba46279888f6f0040cd3a6affb2831482cbd/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py#L320)。

## 5. mini-swe-agent：提供基础循环与消融参照

### 已观察到的实现

核心 `step` 将模型请求与动作执行分开；执行后追加 observation，并包含调用/成本、walltime 检查和
配置、用量、消息、状态的轨迹保存。这使基础运行职责容易理解和核对。

### 对本项目有什么用

主要价值是帮助判断领域增强是否值得保留。可以在本项目现有 Harness 中，固定任务、模型、工具权限、预算和
宿主验收，比较关闭增强、启用检索、启用自动错误证据等条件。基础循环不需要另接一个运行框架。

若直接把 mini-swe-agent 的通用 shell 与本项目三个受控运行工具对比，差异会混入工具权限和执行环境，
难以归因于知识增强。因此应保留同一运行边界，再观察增强是否减少失败和无效调用。

该项目引用 SWE-agent 论文，不等于 mini-swe-agent 的当前版本或本项目自动继承了论文中的实验结论。

### 来源

- [基础循环、预算与轨迹](https://github.com/SWE-agent/mini-swe-agent/blob/25941c89cfbc91eb40b3f8756348c91d9977d57e/src/minisweagent/agents/default.py#L126)。
- [项目说明](https://github.com/SWE-agent/mini-swe-agent/blob/25941c89cfbc91eb40b3f8756348c91d9977d57e/README.md)。

## 与当前项目的衔接和证据要求

本项目的当前结构仍以[架构概览](../architecture/overview.md)、[实现范围](../architecture/agent-implementation-scope.md)
和[ADR 索引](../adr/README.md)为准：一个 Pydantic AI Harness Coder、三个受控 Abaqus 运行工具、宿主独立验收，
知识入口和错误证据由各自的显式配置控制。本文没有改变这些决定。

评估参考机制时，应分别回答以下问题，避免只用“工具有调用”判断有效：

| 参考机制 | 先确认的行为 | 对任务收益的验证 |
|---|---|---|
| 主题操作指导 | 材料覆盖真实问题，内容适用于目标 Abaqus 版本，模型在需要时取得并正确使用 | 相同预算下，完整任务通过情况及知识相关失败是否改善 |
| 重复失败反馈 | 能关联原错误、已给证据和脚本改动，并保留原始状态 | 重复错误和无效运行是否减少，是否误终止本可修复的任务 |
| 增量建模会话 | 修改确实保存到预期模型；失败、重启后状态可辨认 | 相同交付物和验收要求下的总耗时、失败率及恢复代价 |
| 会话恢复 | 已完成 effect 不重放，未决 effect 明确阻断或进入对账 | 故障注入后的产物与作业状态是否一致 |
| 基础循环对照 | 模型、任务、工具权限、预算、verifier 和版本绑定一致 | 完整任务结果、调用量、耗时与失败类型的配对比较 |

这些是研究判断的核验标准，不是新建的实施计划或已运行的实验。现有评测事实分别见
[Coding 评测](../../benchmarks/coding/README.md)和[RAG 评测](../../benchmarks/rag/README.md)。
小样本正例、提示词遵循或 mock 测试都不足以证明通用能力；失败和无收益结果也应保留。

## 固定来源与复用记录

以下提交来自本地参考快照清单；本文上游链接全部固定到这些提交，不代表上游最新状态。
本机快照位于 `D:/Projects/abaqus-agent/references/`，各自独立、默认只读；阅读本文无需依赖这些本地路径。

| 项目 | 基线提交 |
|---|---|
| mini-swe-agent | `25941c89cfbc91eb40b3f8756348c91d9977d57e` |
| OpenHands software-agent-sdk | `013fba46279888f6f0040cd3a6affb2831482cbd` |
| pymechanical-mcp | `3c3ce7009872051a51af48fbff49b3f62e0f28e0` |
| sim-plugin-abaqus | `4118f73841245dc7890f9d526da8d7415e455d98` |
| SSSSSia/CadAgent | `a73168b1f2f9ea47b78eef82506069a8e0894485` |
| LIRAM-LIN/AbaqusAgent | `f5462b3eaf34d21a958f495b80e40df45b3f39db` |

本次迁入的是研究结论、适用边界与来源定位，没有复制上游源码、完整提示词、案例数据或索引，
也没有把参考仓库设为运行依赖。未来直接复制或实质性改编时，依照本项目的
[可追溯复用规则](../adr/0005-traceable-upstream-reuse.md)，记录原仓库、基线、原文件、改动及许可信息；
第三方案例和文档的再分发来源另行核验。
