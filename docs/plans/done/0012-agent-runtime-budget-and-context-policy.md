# PLAN-0012：统一 Agent 运行预算、超时、上下文压缩与收尾策略

| 状态 | 完成时间 |
|---|---|
| 已完成 | 2026-09-17 |

## 问题与最终结果

原 `CodingRunBudget` 把普通产品、冻结评测、runtime 权限、低成本工具次数和 active wall 混成一组有限整数，
同时 HTTP/runtime timeout 分散，OpenCode gateway 缺少 1M context 绑定，长 history 只有默认清理、没有摘要
续跑。

现在普通产品的 model requests、clarification、lookup/preflight 和 active wall 均为 unlimited；固定 benchmark
通过显式 `CodingRunLimits` 保留有限合同。Abaqus/Solver 使用独立 runtime permission，默认
`Abaqus=allow`、`Solver=deny`，CLI 与 Web 新会话均可选择 deny/allow/allow_with_limit。三个 Abaqus 进程
timeout 默认 unlimited，外部进程等待会暂停 active-wall 时钟，取消仍终止受控进程树。

DeepSeek 官方、OpenCode Zen 和 OpenCode Go 的 V4 Flash profile 显式绑定 1M context。launch v7 启用
960K `TieredCompaction`：先清旧普通 tool result，再按需生成 incremental summary，并保留 typed capability
activation exchange；launch 同时冻结 filesystem Skill digest。开发期旧产品 state 不迁移。

## 关键改动

| 模块或路径 | 最终改动 |
|---|---|
| `coding/contracts.py`、`amendments.py` | 新增显式 limits、runtime permission/timeout policy；低成本产品工具无次数上限；amendment 只修改未来 runtime policy |
| `coding/active_wall.py`、`service.py`、`runtime_tools.py` | active-wall timeout 可在外部 Abaqus/静态预检等待时暂停；普通产品显式 `request_limit=None` |
| `execution/abaqus2024.py`、Web ToolExecution | runtime timeout 支持 `None`，有限 timeout 与取消保留进程树终止 |
| `coding/model.py` | HTTP retry=4、timeout=300 秒；三网关 context window=1,000,000 |
| `coding/context_policy.py`、`agent.py`、`launch.py` | launch v7 tiered summary、capability activation capsule、Skill digest 绑定；冻结 evaluator 不启用新产品 compaction |
| `coding/application.py`、`cli.py`、Web API/UI | 产品 defaults、CLI runtime policy 参数、Web 新会话权限选择、limits/runtime usage 展示 |
| `coding/contracts.py`、`read_model.py` | usage 增加 cache read/write token 与 provider details 投影 |
| ADR-0030/0031 | 明确开发期 state 不兼容，以及普通产品不按低成本工具调用次数终止 |

Shell 开放和 Solver submit/wait 由同工作树的 ADR-0032/0033 实施；本计划复用其最终工具合同，没有创建第二
套 Shell permission 或 Solver lifecycle。

## 验证结果

| 成功标准 | 证据或结果 |
|---|---|
| limits/runtime policy 边界 | `tests/test_coding_contracts.py`、`test_coding_amendments.py`、Agent/Service/Web 合同测试通过 |
| active wall 与无限 timeout | `tests/test_active_wall.py`、`test_abaqus2024_runner.py` 通过 |
| compaction 与 capability state | `tests/test_context_policy.py` 通过；覆盖 960K/32K 参数、typed load pair/delta 恢复和 Skill digest 漂移拒绝 |
| Web 权限与生成契约 | CreateRun 三态权限测试通过；OpenAPI/TypeScript 重新生成，`scripts/build_web.py` 构建成功 |
| Python 离线回归 | 完整 pytest 运行得到 1292 passed、27 skipped，唯一失败来自并行 public-mechanics 任务在运行中把 Case 数由 12 改为 13；当前 13-Case 定向测试随后通过 |
| 静态检查 | `ruff check src tests` 通过；`basedpyright` 0 errors；`git diff --check` 通过 |

## 已知缺口与后续事项

- 未调用付费模型或持证 Abaqus；不声称真实 1M 长上下文、摘要质量或长时 Solver 已完成在线验证。
- Provider context-too-large 首版只持久化 infrastructure interruption，不做网关错误字符串匹配和自动压缩重试。
- `FileStepStore` 继续保留默认全部 snapshots；只有观察到真实磁盘增长问题后再设计 retention。
- 全量 pytest 运行期间 public-mechanics 并行任务仍在修改共享工作树；其最新 13-Case 基线已定向通过，但该
  任务自己的最终收尾不属于 PLAN-0012。

## 相关文档

- [ADR-0030：开发期不保留 CodingRun state 向后兼容](../../adr/0030-drop-pre-release-coding-run-state-compatibility.md)
- [ADR-0031：普通产品不按低成本工具调用次数终止 CodingRun](../../adr/0031-remove-product-tool-call-count-limits.md)
- [ADR-0032：为普通产品 Coder 恢复受限通用 Shell](../../adr/0032-restore-restricted-shell-for-product-coder.md)
- [ADR-0033：用一个 Solver 工具分离提交与等待](../../adr/0033-split-solver-submit-and-wait-actions.md)
- [架构总览](../../architecture/overview.md)
- [一次 CodingRun](../../architecture/flows/simulation-run.md)
