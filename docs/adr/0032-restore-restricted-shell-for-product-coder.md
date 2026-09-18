# ADR-0032：为普通产品 Coder 恢复受限通用 Shell

## 状态

已采纳

## 日期

2026-09-17

## 当时遇到了什么

ADR-0015 在真实轨迹复现 `run_command → python → subprocess → abq2024.bat` 绕过后，从产品 Coder
删除了 Harness Shell。该决定保证当时所有 Abaqus 副作用都只能进入三个 typed runtime 工具，但也让产品
失去正常 Coding Agent 所需的测试、静态检查、Git、搜索和普通 Python 执行能力。

当前产品目标已经明确为一个可完成普通仓库工作的 Harness Coder，并额外提供 Abaqus 专用执行工具；不再
把“没有任何 Shell”作为产品定义。系统仍面向本地单用户和可信代码任务，不新增不可信租户隔离目标。

## 最后决定

- 新普通 `ask_user_v1` + response 会话保留 Harness Shell；冻结 evaluator、显式任务 profile 和既有实验
  继续使用各自声明的工具表。
- Shell 默认允许普通编码命令，不维护 `git`、`python`、`uv`、`pytest` 等固定 allowlist。
- Harness `denied_commands` 拒绝通用 `abaqus` / `abq` 名称；ToolGuardrail 进一步按配置的 launcher 绝对
  路径及版本化 `abaqus20xx` / `abq20xx[hfN]` 名称拒绝 `run_command` 和 `start_command`。
- Shell 固定在 workspace，剥离常见模型 Provider 凭据环境变量；不向 Shell 注册宿主 oracle 工具。
- Abaqus 建模、求解和 ODB 提取仍使用 typed runtime 工具。Shell 限制面向常见直接启动，不宣传为 OS
  沙箱，也不增加容器、独立账户或 Windows token sandbox。
- 普通产品默认不注册 Harness Planning；显式开启时使用 Harness 原生 Planning，冻结 profile 保留原值。

## 为什么这样选

项目需要的是“正常 Coding Agent + CAE 专用运行入口”，而不是只能编辑文件的领域机器人。Harness Shell
已经提供 cwd、命令 denylist、timeout、后台命令和输出限制；在本地可信边界内复用它，比为每个普通编码
动作新增 typed tool 更小。Abaqus launcher 的窄拒绝又保留了最常见误操作保护和 typed runtime 的默认路径。

## 没有采用的方案

- 继续完全删除 Shell：无法满足普通 Coding Agent 的基本执行能力。
- 只允许一小组编码 executable：Python、测试器和构建工具都能派生进程，固定 allowlist 既限制正常工作，
  也不能成为强安全边界。
- 为整个产品建设 OS sandbox：当前没有多租户或不可信代码需求，且 licensed Abaqus 的本地 Windows 依赖
  会显著增加实现和运维成本。

## 带来的影响

- 普通产品可以运行仓库检查、测试、Git、搜索和普通 Python，并复用 Harness 后台命令能力。
- 产品权限边界从“工具 schema 中不存在 Shell”变为“Shell 可用于普通编码，直接 Abaqus launcher 被拒绝”。
- 固定评测不能把新产品工具表回填到旧批次；新批次须显式记录 Shell policy 版本。

## 替代关系

- 替代：[ADR-0015：产品 Coder 不暴露通用 Shell](0015-disable-general-shell-for-product-coder.md) 对普通产品
  会话的无 Shell 决定。
- 沿用 ADR-0015 的事实：命令名规则不是 OS 沙箱，能力声明必须与真实工具表一致。

## 落实与确认

- `coding/agent.py` 只为普通开放会话保留 Shell，并注册 Abaqus launcher guardrail。
- 产品默认 Planning 关闭；CLI/Web 新会话显式保存该有效配置。
- 离线测试覆盖工具 schema、版本化 launcher 拒绝和冻结 profile 工具表不变。

## 相关文档

- [Agent、协议与权限范围](../architecture/agent-implementation-scope.md)
- [项目架构](../architecture/overview.md)

