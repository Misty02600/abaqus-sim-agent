# ADR-0015：产品 Coder 不暴露通用 Shell

## 状态

已采纳

被替代：[ADR-0032](0032-restore-restricted-shell-for-product-coder.md) 为普通产品恢复受限 Shell；
本文继续说明旧冻结 profile 的无 Shell 权限与命令 allowlist 不能充当沙箱的依据。

## 日期

2026-08-27

## 当时遇到了什么

首轮合同一直要求 Abaqus 只能经过 `run_cae_script`、`run_solver_job` 和
`run_abaqus_python`。Harness Coder 仍注册了只允许命令名 `python` 的 `run_command`，原意是保留有限的
宿主脚本检查能力；但命令名 allowlist 只检查最外层 executable，不能约束 Python 进程内部行为。

在冻结 load-mode 参数查询批次中，一条真实失败轨迹通过 `python -c` 调用 `subprocess.run`，间接启动
`abq2024.bat`。这次调用没有进入 Abaqus/solver budget、typed launcher 校验或 execution provenance，证明
“只允许 python”不是 vendor runtime 权限边界。继续增加命令字符串关键字过滤也不能可靠覆盖动态 import、
编码字符串、脚本文件或其他等价 Python 调用。

## 最后决定

- 当前产品 `abaqus_2024_coder` 不注册 Harness `Shell` capability，因此没有 `run_command` 或宿主 Python
  执行工具。代码检查使用 workspace 文件工具，vendor runtime 只使用三个 typed Abaqus 工具。
- Pydantic AI Harness `0.24.0` 的 `Coder` 构造器固定创建一个 `Shell`。composition root 要求恰好找到
  一个并在 Agent 注册前移除；数量不是一时直接失败，避免依赖升级后静默恢复通用命令。
- evaluator metadata 写入 `coder-general-shell-disabled-v1`，使不同权限配置的 Product batch 可区分。
- 不通过字符串扫描尝试“安全化”任意 Python。若以后确实需要宿主侧静态检查，新增参数固定、实现归宿主
  的 typed tool，并单独定义输入、输出、副作用和预算。
- 该决定只关闭已复现的 Agent 工具路径，不把普通动态 Abaqus Python 或本地同用户进程描述为恶意代码
  沙箱。项目仍面向受控本地用户和受控模型任务。

## 为什么这样选

首轮任务真正需要的是文件读写和三种 vendor runtime 语义，不需要任意宿主代码执行。移除通用 Shell 后，
每次 Abaqus 副作用都重新落回路径校验、调用预算、原始日志、artifact delta 和 verifier 可见的执行记录。
这比维护可绕过的命令内容规则更小，也更符合 ADR-0008 原本的工具权限边界。

## 没有采用的方案

- 保留 `python` allowlist：已由真实 Product 轨迹反证，Python 可以启动任意子进程。
- 禁止 `subprocess`、`os.system` 或 launcher 字样：只能拦截表面写法，容易产生错误安全感。
- 立即引入容器或独立 OS 账户：能提供更强隔离，但当前 Abaqus 桌面许可证、本地 Windows runtime 和受控
  使用场景尚未提出不可信租户隔离需求；这仍是明确的非目标。

## 带来的影响

- 有利：`run_command → python → Abaqus` 的已复现绕行路径从模型工具 schema 中消失。
- 有利：权限配置 fail-closed，并进入 evaluator metadata 与单元测试，便于升级 Harness 时重新验证。
- 有利：同配置 load-mode follow-up 三次均只使用 typed Abaqus 工具并完整通过。
- 代价：模型不能运行 `py_compile`、临时宿主 Python 探针或其他通用命令；需要的检查必须通过文件阅读、
  vendor runtime 或未来有明确合同的宿主工具完成。
- 限制：3/3 follow-up 是小样本正常路径证据；三次都没有尝试未知 `run_command`，不能据此宣称 OS 级隔离。

## 落实与确认

- 实现：`src/abaqus_sim_agent/coding/agent.py` 在 `_configure_coder` 中 fail-closed 移除 `Shell`，并在系统
  instructions 中明确没有通用 Shell/宿主 Python 工具。
- 测试：`tests/test_coding_agent.py` 从实际 Agent tool schema 断言 `run_command` 不存在，同时保留 workspace
  文件能力和三个 typed Abaqus 工具。
- Product 证据：相同 source、Skill、final9、OpenCode Go `deepseek-v4-flash`、typed-probe v2 与
  `48/18/3` 配置下为 3/3；三次分别使用 16/10/11 次模型请求，均为 3 次 Abaqus、1 次 Solver，history 中
  `run_command` tool call 和 subprocess 均为 0。
- 冻结证据：`benchmarks/coding/plate_model_change_treatments_20260825.json` 的
  `precise_feedback_no_shell_treatment`。2/3→3/3 不作因果成功率声明。
- B31后续暴露了typed工具内部的另一层权限过宽：固定Gold只需要两个生产脚本和一个INP，模型却用
  `probe_set.py`/`check_model.py`消耗vendor runtime。strict`RuntimeScriptPolicy`现可为三个工具分别声明
  相对路径allowlist；拒绝发生在probe状态、预算预留和runner前，且`budget_consumed=false`。默认null不改变
  原workspace边界。B31 v7显式允许`build_model.py`、`beam_tension.inp`、`extract_results.py`后正式2/3；
  三条均无拒绝或`run_command`，两个pass仅3/4次Abaqus，一条生产脚本仍12次耗尽。该证据支持权限边界与
  正常路径兼容，不把成功率改善单因果归于allowlist。

## 相关文档

- [ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun](0008-pydantic-ai-harness-abaqus-coding-run.md)
- [项目架构](../architecture/overview.md)
- [首轮 Agent 与实现范围](../architecture/agent-implementation-scope.md)
