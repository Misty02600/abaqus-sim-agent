# PLAN-0011：禁止 CAE 脚本内部提交 Solver Job

| 状态 | 最后更新 |
|---|---|
| 已完成 | 2026-09-17 |

## 背景

产品把建模、求解和后处理分成 `run_cae_script`、`run_solver_job` 和 `run_abaqus_python` 三个宿主工具。
Solver 调用预算、执行 provenance 和诊断证据依赖该阶段边界；仅靠 Skill 要求无法阻止 CAE 脚本直接调用
Job API 启动求解。

## 最终实现

`coding/cae_script_policy.py` 使用宿主 Python 按 3.10 grammar 解析工作区 CAE 脚本，收集 AST 中直接调用的
`.submit(...)` 和 `.waitForCompletion(...)`。`coding/runtime_tools.py::_run_cae_tool` 在可选 static preflight、
Abaqus 预算预留和 runner 前返回 `CaeSolverLifecycleDenied`，并标记 `blocked=true`、
`runtime_executed=false`、`budget_consumed=false`。

Job 对象创建、`JobFromInputFile(...)` 和 `writeInput(...)` 保持允许。策略应用于 production 与 diagnostic
CAE 调用，不改变 `run_solver_job`、Abaqus Python、runtime allowlist 或 verifier 合同。

## 验证

- `tests/test_cae_script_policy.py` 覆盖直接 `job.submit()`、repository 访问、`waitForCompletion()`、多调用、
  注释/字符串、Job 创建、`writeInput()` 和动态 `getattr` 非目标。
- 工具集成测试确认拒绝不启动 runner，不消耗 Abaqus/Solver 预算，并返回调用位置。
- `tests/test_coding_agent.py` 全量离线回归通过，现有 Agent 工具路径保持兼容。

## 已知边界

- `getattr(job, "submit")()`、`exec`、运行时重绑定和等价动态绕行不在最小策略覆盖范围内。
- 这是受控本地任务的误操作边界，不是 OS 沙箱或不可信 Python 隔离。
- 本次只有离线合同测试，没有重新运行真实模型、持证 Abaqus 或物理验收。

## 相关文档

- [Agent、协议与权限范围](../../architecture/agent-implementation-scope.md)
- [一次 CodingRun](../../architecture/flows/simulation-run.md)
- [Solver 生命周期](../../architecture/flows/job-lifecycle.md)
