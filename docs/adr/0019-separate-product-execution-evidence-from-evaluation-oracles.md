# ADR-0019：分离产品执行证据与测评物理 oracle

## 状态

已采纳

## 日期

2026-08-31

## 当时遇到了什么

普通 `abaqus-sim-agent` CLI 对外接收任意 goal，但 composition 同时硬编码了 plate tension 的默认提示词和
`PlateTensionRunVerifier`。因此梁、接触、后处理或任意其他用户任务最终仍会被要求生成
`plate_tension.*`，并按 CPS4R、80 个单元和固定 U/S/RF 判分。固定 Gold 的隐藏真值由此泄漏成产品约束，
使“开放 Coding Agent”与实际完成条件矛盾。

完全移除宿主校验也不可接受：Abaqus/CAE 可能在脚本错误时返回进程 exit code 0，模型还可能留下语法错误、
失败的最后一次 runtime 或没有任何本次产物。产品仍需要确定性执行证据，但它不能冒充任务专属物理正确性。

## 最后决定

- 普通 CLI 使用开放任务提示词，不预设板厚、几何、单元、网格、文件名或期望力学结果。
- 普通 CLI 注入 `ExecutionEvidenceVerifier`。它检查本次工作区变化、fresh Python 的 3.10 语法，以及每种已
  使用 Abaqus 阶段的最后一次执行是否通过确定性 stage check；它明确不运行隐藏物理 oracle。
- 固定 plate、bar、beam、repair 和 model-change evaluator 继续显式拥有各自 task instructions 与
  task-specific verifier。只有这些测评入口可以形成 `task_oracle` 结论。
- `CodingRunOutcome.verification_scope` 区分 `execution_evidence` 与 `task_oracle`。Agent 仍只能请求
  verification，最终 outcome 仍由宿主形成。
- 产品默认 `RuntimeScriptPolicy` 继续不设置文件 allowlist；固定测评可以显式选择任务级 allowlist，但不得
  反向成为开放 CLI 的文件名限制。

## 为什么这样选

执行证据属于所有本地 CodingRun 的共同安全与可审计边界，物理真值则依赖具体问题、输入和验收定义，不
可能由一个 plate oracle 泛化。分离后，同一个 Agent、runner 和 service 可以服务开放任务；测评仍能通过
隐藏 oracle 给出严格、可重复的 Gold 分数，同时不把测评实现细节伪装成产品能力。

## 没有采用的方案

- 普通 CLI 继续固定 plate oracle：会错误拒绝所有非 plate 任务。
- 普通 CLI 完全不校验：无法阻止语法错误、CAE exit-0 traceback 或失败终态被宣告完成。
- 让 Agent 自选 verifier：模型可以选择容易通过的验收器，破坏宿主独立性。
- 为任意自然语言任务动态生成隐藏物理 oracle：当前没有可信的规格提取与独立真值来源，不能作为确定性
  产品边界。

## 带来的影响

- 有利：开放 CLI 不再被固定 plate 合同限制；测评和产品的成功语义可以从 state/outcome 直接区分。
- 代价：`execution_evidence` 只能证明脚本、产物和已运行阶段的工程证据，不能证明开放问题的物理正确性。
- 风险：通用 verifier 无法确定自然语言 goal 的全部交付物；需要依赖 fresh artifact、runtime evidence 和
  Agent 报告，不能宣传为 specification-fidelity verifier。

## 落实与确认

- 产品 verifier：`src/abaqus_sim_agent/coding/execution_verifier.py`
- CLI composition：`src/abaqus_sim_agent/coding/cli.py`
- 开放与固定任务提示词边界：`src/abaqus_sim_agent/coding/instructions.py`、
  `src/abaqus_sim_agent/evaluation/plate_tension.py`
- 测试：`tests/test_execution_evidence_verifier.py`、`tests/test_coding_cli.py`、
  `tests/test_coding_agent.py`

## 相关文档

- [项目架构](../architecture/overview.md)
- [CodingRun 流程](../architecture/flows/simulation-run.md)
- [ADR-0008：首轮采用 Pydantic AI Harness 单 Coder](0008-pydantic-ai-harness-abaqus-coding-run.md)
