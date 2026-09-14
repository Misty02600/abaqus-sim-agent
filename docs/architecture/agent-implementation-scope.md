# 首轮 Agent 与实现范围

## 结论

首轮只有一个真正进入产品调用链的 LLM Agent：`abaqus_2024_coder`。它由 Pydantic AI 运行，通过
Pydantic AI Harness 的 `Coder` 和 `Skills` capabilities 在单一工作区编码，并通过三个 typed Abaqus
工具运行真实 vendor runtime。

旧设计中的 Interpreter、Architect、Input Writer、Reviewer、Verifier 和 Supervisor 对应的 Protocol、领域
模型骨架已清理，历史决定不计为当前多 Agent。Runner、clarification provider、workspace resolver、host verifier
和 evaluator 都是确定性宿主组件，也不称为 Agent。

产品 CLI 接收开放的 Abaqus coding goal，并只以通用执行证据形成 outcome；固定 evaluator 复用同一 Coder
并注入 Case task instructions 与独立物理 oracle。产品与测评的 verifier 边界由
[ADR-0019](../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md) 确定；固定 Gold 的纵向
闭环仍遵循 [ADR-0008](../adr/0008-pydantic-ai-harness-abaqus-coding-run.md)。

## 什么才算当前 Agent

`abaqus_2024_coder` 同时具备以下真实调用边界：

- Pydantic AI `Agent[CodingAgentDependencies, CodingAgentOutput]`，不是给普通函数改名；
- strict最终决策为ready_for_verification/failed；新默认ask_user使用DeferredToolRequests交回宿主，旧profile仍含needs_input；
- 每个 run 注入 immutable request、Abaqus runner 和调用计数，模型看不到 API key 或 trusted oracle；
- Harness `Coder` 只绑定当前 workspace，禁用 subagent，并在注册前 fail-closed 移除自带 `Shell`；
- `Skills` 只从包内 `src/abaqus_sim_agent/skills/` 加载 `abaqus-2024-coding`；
- 三个显式工具分别拥有 vendor runtime 副作用和独立计数；
- model request、tool、Abaqus、solver、clarification 和 wall-clock usage 进入 outcome/evaluation；
- 合同、clarification、workspace、模型 factory、runner、verifier 和 evaluator 都有独立测试。

宿主 `CodingRunService` 可以多次调用同一个 Agent 并携带 message history，但这仍是一个 Coder 的修复
循环，不是多个角色协作。

## 模型可见能力

| 能力 | 模型能做什么 | 宿主限制 | 明确不能做什么 |
|---|---|---|---|
| Harness `Coder` | 读取、搜索、创建和修改 workspace 内普通文件，检查自身脚本 | 根路径固定；无 subagent；composition 移除唯一 Shell，数量变化时失败 | 访问 state/oracle、跨 workspace 写文件、调用 `run_command` 或宿主 Python；vendor runtime 只能走三个 typed tools |
| 包内 `abaqus-2024-coding` Skill | 获得 Abaqus 2024 运行模式、vendor Python、诊断和停止条件 | `skill_library_path()` 默认解析已安装 package 内的 `src/abaqus_sim_agent/skills/`，只加载这一项 | 把 Skill 当成已验证 API 百科，或绕过真实 runtime 猜测成功 |
| `run_cae_script` | 用 CAE kernel 运行工作区 `.py`，生成 `.cae/.inp` | 非 solver 调用预算；路径和 `.py` 后缀校验；可选task-owned exact allowlist；180 秒工具超时 | 在 authoring 脚本里提交 Job |
| `run_solver_job` | 从工作区现有 `.inp` 启动一个 Abaqus/Standard Job | Job 名 ASCII/长度校验；solver 与总调用预算；可选INP allowlist；300 秒工具超时 | 运行任意 shell、从工作区外读取 INP、伪造 completed |
| `run_abaqus_python` | 用 Abaqus standalone Python 运行 `odbAccess` 数值提取脚本 | 路径和 `.py` 后缀校验；可选task-owned exact allowlist；120 秒工具超时 | 使用宿主 Python 代替 vendor runtime，调用模型不可见 oracle |

显式`enforced_verified`不是额外Agent：Harness ToolGuardrail在task-owned production `write_file/edit_file`
执行前检查完整Python 3.10候选，非法候选不落盘；运行申请还会复核当前hash。RuntimeScriptPolicy先授权，
随后BasedPyright只把有licensed/冻结evidence id的诊断hard block，未知SWIG/type为warning；同一blocker
hash不可由模型override，checker不可用fail closed。Solver INP不经过Python门，最终物理正确性仍归真实
Abaqus与host verifier。旧`advisory`保留兼容但不再代表推荐ACI设计。

`run_trusted_abaqus_python` 是 runner 的宿主私有入口，只允许应用自有的绝对 ASCII `.py`，并要求声明它要
读取的 workspace artifact。它专供 verifier 使用，不注册成 Pydantic AI tool。

这个 no-Shell 边界关闭的是已复现的 `run_command → python → subprocess → Abaqus` 路径。Agent 编写并交给
typed tool 的 Abaqus Python 仍是普通动态代码；首轮面向受控本地工作流，不宣称 OS 沙箱或不可信租户隔离。

## Typed run contract

### 请求和预算

`CodingRunRequest` 固定：

- 受限 ASCII `run_id`、自然语言 goal；
- 已解析、绝对、可写的 ASCII workspace；
- workspace 外的已解析 state directory；
- 显式 `RuntimeProfile`，首轮为 `D:\SIMULIA\Commands\abq2024.bat`；
- 可选`RuntimeScriptPolicy`分别声明CAE脚本、Abaqus Python脚本和Solver INP；null保持原行为，显式
  allowlist拒绝发生在预算和runner之前；
- 默认最多 2 次澄清、30 个模型请求、12次可选静态预检、9 次 Abaqus 调用、其中 3 次 solver 调用，以及
  2,700 秒总 wall time。静态模式关闭时预检计数为0。

solver 次数不能大于 Abaqus 调用次数。预算耗尽、无 scripted/console answer、Agent 在澄清前调用 Abaqus、
超时或 verifier 持续拒绝都会形成带 `failure_stage` 的失败 outcome。

### Agent decision 与宿主终态

| Agent decision | 含义 | 宿主动作 |
|---|---|---|
| `needs_input` | 缺少必需物理值、存在会改变物理问题的意图歧义，或显式用户约束冲突 | 在任何文件、Planning、lookup 或运行工具前返回带 `kind/field_id/question/impact` 的请求；provider 取得同一 `field_id` 的回答，保存 event 后继续同一 history |
| `ready_for_verification` | Agent 认为所需 artifact 和运行证据已齐全 | 普通CLI调用execution-evidence verifier；固定eval调用task oracle；通过才形成带scope的`COMPLETED`，失败则把证据送回同一Coder最小修复 |
| `failed` | 在预算或已知条件下无法继续 | 宿主形成 `FAILED` outcome，不再伪造验证 |

模型不能直接返回 `CodingRunOutcome`。完成状态由 `CodingRunService` 和 verifier 聚合，确保模型没有
“自己生成、自己判分”的权限；`execution_evidence` 只证明工程执行边界，不冒充物理验收。
ready decision还必须声明非空typed deliverable manifest；普通CLI逐项核对存在性与freshness，再检查所有
fresh Python的3.10语法和最后一次已用runtime stage。真实cube smoke已以任意文件名、1次CAE、0 Solver
通过该路径，证据见[产品smoke](../../benchmarks/coding/product_open_cube_execution_evidence_20260831.json)。

当前 `physics-materiality-clarification-v1` 要求 Coder 保留用户显式约束，只把会改变物理理想化、控制
方程、材料、载荷/约束语义或验收含义且无法从请求确定的事实交给用户；API、Set、`findAt`、文件名等编码
问题不得转嫁给用户。剩余实现或当前已验证任务范围内的数值选择由 Coder 基于证据完成。四个版本化 plate
请求已有分层 smoke：歧义与显式冲突的 decision-only gate 分别为3/3和2/3；缺板厚与显式兼容 CPS4R 各有
1次持证闭环 pass，但对应三重复都只有1/3；由 Coder 自主选择单元的三重复为0/3且全部停在 provider
timeout。因此这些证据支持当前决策边界的观察结果，不支持“完整闭环稳定”或“已证明自主单元选择”。详见
[冻结证据](../../benchmarks/coding/plate_clarification_policy_evaluation_20260830.json)。
临时 `retry10/timeout60` follow-up 中，自主选型和显式兼容单元仍分别只有1/3；自主选型的pass没有询问
用户，但首次选择CPS4，经host指出不属于当前唯一受支持的CPS4R profile后才修复并通过；另两条仍超时。
显式兼容组则是一条pass、一条runtime-budget、一条timeout。它证明Coder可在反馈后收敛，不证明首次自主
选型正确，不满足稳定gate，也不能把变化单因果归于retry。详见
[follow-up证据](../../benchmarks/coding/plate_clarification_retry10_evaluation_20260830.json)。
随后自主选型Case移除隐藏精确CPS4R条件，允许单一CPS4或CPS4R并拒绝混用；首个新formal批次三条都在
文件/Abaqus前发生provider timeout，0/3只表示基础设施阻断，不构成模型选型结论。详见
[修正后证据](../../benchmarks/coding/plate_element_choice_allowed_evaluation_20260831.json)。

`ClarificationEvent.kind/impact` 没有旧格式默认值。缺少这两个字段的历史 `outcome.json` 会被当前 strict
合同拒绝，不能继续作为 verified-seed source；需要重新生成符合当前合同并通过 oracle 的 source run。

## 首轮 Gold contract

Gold request 从空工作区开始，固定要求：

| 维度 | 当前唯一受评测目标 |
|---|---|
| Solver / procedure | Abaqus/Standard；一个 `Static, General` step；`NLGEOM=OFF` |
| 几何与单位 | 100 mm × 20 mm 二维平面应力矩形板；mm-N-MPa |
| 材料 | 各向同性线弹性；`E=210000 MPa`、`ν=0.3` |
| 缺失输入 | 初始 request 故意不提供厚度；必须先请求 `plate_thickness`，scripted answer 为 `1 mm` |
| 网格 | 只允许 `CPS4R`；长度 20 个、高度 4 个，共 80 个单元 |
| 边界 | 左边 `U1=0`、左下角 `U2=0`、右边 `U1=0.1 mm` |
| 输出 | 请求 U、S、RF |
| authoring artifact | `build_model.py`、`plate_tension.cae`、`plate_tension.inp`；建模脚本不 submit |
| solver artifact | 独立 solver tool 生成 `plate_tension.odb` 和完成状态证据 |
| extraction artifact | `extract_results.py` 通过 `odbAccess` 生成包含 `right_mean_u1`、`mean_s11`、`left_sum_rf1` 的 `results.json` |

该题只验证首个闭环。即便 Coding Agent 的动态代码理论上能生成其他模型，项目也不能把未配 hidden oracle、
fixture 和 licensed repeat 的任务宣传为 supported capability。

## Host verifier 的完成条件

`PlateTensionRunVerifier` 在模型请求验收后执行：

1. 对当前 workspace 重新 snapshot，要求六个文件存在且相对初始 snapshot 是 fresh；
2. 读取 Agent `results.json`，要求三个字段都是可转 float 的数值；
3. 通过模型不可见的 trusted oracle 重新读取 `plate_tension.odb`；
4. 同时要求本次 execution 记录中存在成功的 solver invocation，且 `.sta` 包含成功完成标记；
5. 要求 ODB 只有 80 个 `CPS4R`；
6. 以 2% 相对容差检查右端平均 U1≈0.1、平均 S11≈210、左端总 RF1 绝对值≈4200；
7. 要求 Agent `results.json` 与独立 oracle 的三项测量在同一容差内一致。

任一条件失败都会返回结构化 feedback 和 failure stage。Agent 可以在剩余预算内继续修复，但 verifier 不
修改 workspace，也不降低标准。

## 评测范围

`pydantic-evals` 默认数据集是 `plate_tension_missing_thickness`，也可通过显式 case 选择运行两个共享同一
plate oracle 的 proceed 请求；每题顺序重复三次，`max_concurrency=1`，至少两次 `closed_loop_pass` 才通过
gate。独立 clarification evaluator 在零 Abaqus 预算下只评估 `needs_input` 决策。每次还记录
clarification、pre-decision tool、fresh artifact、physics、failure stage、token/tool/runtime/cost usage 和是否
有人工代码修改。

这是一条 licensed smoke，不是统计稳定的成功率。单元测试使用 fake process、synthetic measurements 和
固定 evaluator output；它们证明合同、命令、超时、编码、freshness 和 scorer 逻辑，不能替代持证运行。
项目参考脚本已经在本机 Abaqus 2024 通过一次 opt-in 集成测试并命中全部 oracle 目标；这证明真实
runner/solver/verifier 边界。OpenCode Go 的无重试与两次 SDK retry 三重复实验均为 0/3；retry 组有一项
进入 9 次 Abaqus authoring/probe 后耗尽模型预算，另外两项仍在模型网关失败。

## 首轮明确不做

- 不运行 LangGraph 父图、专业 Agent 子图或 Supervisor；
- 不使用 `DeckDraft/ChangeSet`、确定性 INP renderer、通用 Abaqus AST 或 CAS publisher；
- 默认 Gold 不接入 Retriever、104 条上游案例或本地 Chroma，保留 B0 no-RAG；ADR-0009 的 exact API
  Catalog 仅通过显式 `--api-catalog-dir` 形成 B1 变体，Concept RAG 仍不接入；
- 不提供 FastAPI、REST/SSE、SQL、DBOS、database checkpoint 或 durable Job ledger；
- 只提供基于 Harness StepPersistence 与宿主文件状态的中断 CodingRun 续跑；不提供外部 Abaqus Job
  重新附着、crash reconcile、异步 poll/cancel、并发 worker 或多用户隔离；
- 不承诺 3D、接触、塑性、NLGEOM、dynamic/Explicit、复合材料、用户子程序、CAD 或通用现有模型修改；
- 不把工作区限制描述成恶意代码沙箱。

这些方向不是永久拒绝。只有当前 Gold 和新增 Gold 的失败分布表明单 Coder、同步工具或宿主 verifier 无法
承担某类稳定职责时，才增加角色、中间表示、检索或持久控制面，并用同一 evaluator 做消融。

## 相关文档

- [项目架构](overview.md)
- [当前系统框架](system-framework.md)
- [技术栈与工程讲解点](technology-stack.md)
- [Coding Gold 说明](../../benchmarks/coding/README.md)
- [ADR-0004：以证据和物理验证定义任务成功](../adr/0004-evidence-first-physics-verification.md)
- [ADR-0005：有来源地吸收与重构 AbaqusAgent](../adr/0005-traceable-upstream-reuse.md)
- [ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun](../adr/0008-pydantic-ai-harness-abaqus-coding-run.md)
- [ADR-0009：使用官方文档、abqpy AST 与运行证据构建精确 Abaqus API Catalog](../adr/0009-exact-abaqus-api-catalog.md)
- [ADR-0018：以分级 ACI 强制生产脚本语法和已验证 API 合同](../adr/0018-enforce-verified-abaqus-aci.md)
- [ADR-0020：使用 Harness StepPersistence 续跑中断的 CodingRun](../adr/0020-resume-coding-runs-with-harness-step-persistence.md)
