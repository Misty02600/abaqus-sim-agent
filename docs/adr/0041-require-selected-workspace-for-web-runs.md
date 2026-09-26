# ADR-0041：Web 新会话必须选择已有工作区

## 状态

已采纳，2026-09-24。用户最终决定：Web 不自动创建托管临时工作区，新会话必须先选择工作区文件夹。

## 背景

ADR-0034 同时允许新会话选择已有目录，或由 Web 在管理根下创建托管空目录。后者让用户无需先确定实际项目目录，
并产生与 Pi Web 项目选择流程不同的空工作区。当前实现还允许前端在没有选定目录时直接发送首条消息，由宿主创建
`workspaces/<run_id>`。

## 决定

- 新 Web run 必须提交显式工作区路径。`CreateRun.workspace` 为必填字段；缺失时在 HTTP 请求校验阶段拒绝，
  不建立 run registration、CodingRun state 或 Agent 工作段。
- 前端在选定现有工作区之前禁用发送，并提供已有工作区快捷入口和目录浏览器。目录浏览只读，不创建目录或 run。
- Web 宿主重新验证目录，按 selected 来源登记规范路径；文件原地读写，会话创建后目录固定。现有文件仍进入初始
  snapshot，按原 freshness 合同处理。
- 新 run 的 state、registry、cache 和 control 仍由 `--root` 管理。项目不复制、搬迁或删除用户工作区。
- 历史 `managed` 登记和工作区继续按其保存的路径读取；不迁移旧 state，也不再产生新的 `managed` 登记。
- 保留 ADR-0034 的 ASCII、目录类型、可写、链接/junction、管理根重叠和并发冲突校验。

## 替代方向与代价

继续在管理根下创建每 run 的托管空目录，可以保留无前置选择的启动流程和目录隔离，但要求用户再把已有工程复制或
改选到该目录，也与明确的项目工作区选择交互不符。改为强制已有目录会让第一次创建多一步选择，并可能把用户
原有文件纳入 workspace 初始快照；这些文件不会因此被认作新交付物。

## 替代关系

局部替代 [ADR-0034](0034-bind-selected-workspaces-for-web-runs.md) 的“新会话可自动创建托管空目录”决定。
显式选择工作区的路径规则、固定绑定、state 管理和执行边界继续有效。

## 实现与验证入口

- [创建请求合同](../../src/abaqus_sim_agent/web/contracts.py)、[宿主创建流程](../../src/abaqus_sim_agent/web/controller.py)
- [目录选择器](../../webui/src/DirectoryPicker.tsx)、[新会话入口](../../webui/src/App.tsx)、[运行时发送门](../../webui/src/WorkbenchRuntime.tsx)
- [工作区目录校验测试](../../tests/test_web_directories.py)、[Web 创建测试](../../tests/test_web_workbench.py)
