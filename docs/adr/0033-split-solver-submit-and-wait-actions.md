# ADR-0033：用一个 Solver 工具分离提交与等待

## 状态

已采纳

## 日期

2026-09-17

## 当时遇到了什么

原 `run_solver_job` 在 CLI 中同步等待，在 Web 中一调用就通过 `CallDeferred` 结束 Agent run。两种路径都让
模型在提交后无法先做独立的文件修改、资料检查或报告整理；Web Deferred 解决了长等待占用模型工作段，
但没有解决“提交立即返回、等待由模型稍后选择”的产品语义。

进一步引入 submit/status/wait/cancel 四个工具、独立 Job Service 或数据库会超过当前本地单用户需求。
当前 Web `ToolExecution` 已能承载后台进程，CLI 也可以在当前事件循环保存后台 task。

## 最后决定

- 新普通会话只暴露一个 `solver_job`，请求使用 discriminator `action`：
  - `submit` 接收 `job_name` 与 `input_file`，预留一次 Solver 权限/预算，立即启动宿主后台任务并返回
    `execution_id`；
  - `wait` 接收 `execution_id`，普通 `await` 已提交任务，完成后把既有 `AbaqusExecutionResult`、诊断和
    artifact evidence 返回模型。
- `submit` 后模型可以调用其他文件、知识或 Shell 工具；在请求验收、失败或文字终态前必须消费所有当前
  run 内 pending Solver execution。
- CLI 用 runner 内的 asyncio task 承载后台执行；Web 复用 `ToolExecution` 和专用事件循环。两端共享同一
  模型工具语义，`wait` 不使用 Pydantic Deferred。
- Solver timeout、用户通过 Web 取消、准确进程树终止和 `needs_reconcile` 边界继续归宿主；不向模型暴露
  cancel、PID 或通用进程工具。
- 不增加 `get_solver_job`、独立 SolverJob 数据库、持久 CAE session、PID 跨进程重新附着或 exactly-once
  声明。
- 冻结 evaluator 继续使用其版本化 `run_solver_job` 工具合同；新普通产品不把新工具表回填旧批次。

## 为什么这样选

提交返回 handle、等待显式 join 是满足模型并行安排工作的最小抽象。一个 discriminated tool 避免多个相近
工具名；普通 await 对当前本地单用户足够，不需要为释放 Agent run 引入 deferred frontier 和自动恢复状态。
Web 已有的后台进程记录仍提供 UI 状态、宿主取消和结果持久化，因而无需第二套 Job 控制面。

## 没有采用的方案

- 一调用 Solver 就同步等待：模型不能利用求解时间完成独立工作。
- 一调用 Solver 就 Deferred：当前 Agent run 立即结束，仍不能先做其他工作。
- submit/status/wait/cancel 四工具：增加模型选择和状态面，当前只有 submit 与 join 是必要动作。
- 独立数据库 Job Service或 durable workflow：当前没有多 worker、跨主机调度或 PID 重附着需求。

## 带来的影响

- 模型必须保存并使用 `execution_id`，且不能在 pending Solver 存在时结束当前 run。
- Solver budget 在 submit 时消耗，wait 不重复计数。
- Web `ToolExecution` 不再绑定原 Solver tool call 或触发自动 Agent resume；wait 本身读取同一执行结果。
- 若 CLI Agent run 在 wait 前被进程级中断，当前不承诺后台 task 跨进程存活或重附着。

## 替代关系

- 局部替代：[ADR-0008：Pydantic AI Harness 单 Coder](0008-pydantic-ai-harness-abaqus-coding-run.md) 中一步式
  `run_solver_job` 的普通产品工具合同。
- 局部替代：[ADR-0025：CodingRun 上的本地 Web 工作台](0025-local-web-workbench-over-coding-run.md) 后续落实
  中“Solver tool call 立即 Deferred 并自动恢复”的普通产品路径。

## 落实与确认

- `coding/runtime_tools.py` 注册 `solver_job` 的 submit/wait strict request，并在 wait 后复用既有 result
  finalization。
- `execution/abaqus2024.py` 为 CLI runner 保存后台 Solver task；`web/controller.py` 复用 ToolExecution future。
- `CodingRunService` 在当前 run 尚有 pending Solver 时拒绝终态并要求先 wait。
- 离线测试覆盖 submit 后继续文件读取、wait 记录 execution、Web 完成与取消路径。

## 相关文档

- [一次 CodingRun](../architecture/flows/simulation-run.md)
- [Solver 生命周期](../architecture/flows/job-lifecycle.md)
