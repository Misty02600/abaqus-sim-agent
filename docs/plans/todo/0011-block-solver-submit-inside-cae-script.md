# PLAN-0011：禁止 CAE 脚本内部提交 Solver Job

| 状态 | 最后更新 |
|---|---|
| 讨论中 | 2026-09-14 |

## 背景

当前运行合同把建模、求解和后处理分成三个宿主工具：`run_cae_script` 负责生成 `.cae` / `.inp`，`run_solver_job` 负责从现有 `.inp` 启动 Abaqus/Standard，`run_abaqus_python` 负责 ODB 数值提取。Solver 调用预算、执行 provenance 和诊断证据都依赖这一阶段边界。

Skill 和工具说明已经要求 CAE 建模脚本不得提交 Job，但该要求目前主要是 Agent 协议，不是代码层硬约束。

## 当前设计与缺陷

### 相关实现与当前行为

- `src/abaqus_sim_agent/coding/runtime_tools.py::_run_cae_tool` 在检查路径 allowlist、文档策略和可选静态 preflight 后，直接调用 `dependencies.runner.run_cae_script(...)`。
- `src/abaqus_sim_agent/coding/dependencies.py::runtime_script_policy_denial` 只校验允许运行的脚本路径，不检查脚本内容。
- `src/abaqus_sim_agent/execution/abaqus2024.py::Abaqus2024Runner.run_cae_script` 最终执行 `abq2024.bat cae noGUI=<script>`。
- `src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md` 和 `run_cae_script` 工具说明规定 Solver 应由独立工具运行。

### 缺陷机制、证据与影响

- 触发条件：一个被允许执行的 CAE 脚本包含 `job.submit()`、`mdb.jobs[...].submit()` 等内部求解调用。
- 机制：宿主只验证“哪个脚本可以运行”，随后整个脚本进入 Abaqus/CAE；脚本内部调用不会再次经过 `run_solver_job`。
- 证据：源码核对确认当前没有针对 `submit()` / `waitForCompletion()` 的 AST 或文本级拒绝逻辑。
- 影响：Agent 误写脚本或外部知识示例带入直接提交代码时，可以绕过 Solver tool 的调用预算、阶段 provenance 和统一诊断入口，使“建模 → 求解”边界只剩协议约束。

该计划面向 Agent 误操作和工程边界完整性，不把静态检查描述成恶意代码沙箱。

## 技术路线

1. 在任何 CAE 外部进程启动、Abaqus 调用预算消耗之前，对待执行 Python 脚本执行确定性静态检查。
2. 至少拒绝 Abaqus Job 生命周期中的 `.submit(...)` 和 `.waitForCompletion(...)` 调用；`mdb.Job(...)` / `JobFromInputFile(...)` 的 Job 对象创建和 `writeInput(...)` 仍允许，因为建模阶段需要生成输入文件。
3. 拒绝结果使用独立、可识别的 policy error，明确 `blocked=true`、`budget_consumed=false`，并告诉 Agent 应通过 `run_solver_job` 启动求解。
4. 该检查应用于所有 `run_cae_script` 调用，包括 production 和 diagnostic，保证 Solver 启动始终只有宿主工具一个入口。
5. 保留现有 runtime script allowlist、静态 preflight、预算与 provenance 顺序；新检查只补充“允许脚本内部做什么”的边界。
6. 明确静态策略的安全声明：普通 AST 可确定调用必须被阻断；`getattr`、`exec`、运行时重绑定等动态绕行不作为恶意代码隔离保证。若未来需要不可信代码隔离，再单独设计 OS / 进程级沙箱。

建议把脚本内容策略放在独立 helper 中，由 `_run_cae_tool` 在 runtime budget reservation 前调用，避免把 Abaqus Job 语义塞进通用路径 allowlist。

## 完成标准与验证

- `job.submit()` 被确定性拒绝，且没有启动 Abaqus 进程、没有消耗 Abaqus / Solver 预算。
- `mdb.jobs['Job-1'].submit()` 等常见 repository 访问形式同样被拒绝。
- `.waitForCompletion()` 被拒绝，避免完整 Solver 生命周期重新进入 CAE 脚本。
- 注释、字符串中的 `submit()` 不产生误报。
- `mdb.Job(...)`、`JobFromInputFile(...)`、`job.writeInput(...)` 和普通建模脚本仍可进入现有 CAE 执行路径。
- 正常 `run_cae_script → run_solver_job → run_abaqus_python` 链路行为保持不变。
- 增加针对 policy denial 和正常路径的确定性测试，并运行相关 `runtime_tools` / runner 测试。

## 非目标

- 不把该检查宣传为 OS 沙箱或针对恶意 Python 的安全隔离。
- 不改变 Solver 工具的现有输入文件、预算、日志和 verifier 合同。
- 不禁止创建 Abaqus Job 对象或写出 `.inp`；禁止的是在 CAE 脚本内部启动和等待求解。
