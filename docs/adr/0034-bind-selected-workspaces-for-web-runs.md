# ADR-0034：Web 会话绑定用户选择的已有工作区

## 状态

已采纳，2026-09-22。用户逐项讨论目录选择后确认：复用 Pi 的 React/TypeScript 选择器交互，
目录接口由现有 FastAPI 提供，随后明确授权实施。

## 背景

ADR-0025 首版规定每个 Web 会话在管理根下创建空工作区。这便于隔离会话，却使用户无法直接在已有工程
目录中工作。共享 `prepare_new_run` 与 Harness Coder 已接受工作目录；限制来自 Web 创建与登记校验。

## 决定

- 新会话可使用自动创建的空目录，或显式选择已有本机绝对目录。选择已有目录表示直接读写其中的文件，
  不复制工程，也不更改进程全局 cwd。一个会话建立后固定其目录；更换目录应创建新会话。
- `CodingRunRequest.workspace` 保存规范化路径；Web registration 明确记录 `managed` / `selected` 来源。
  每次加载复核来源、路径与 state 归属，保留当前基线、累计用量、恢复与验收语义。
- state、registry、cache、control 继续位于服务管理根；用户选择的工作区与整个管理根互不包含。
  保留 ASCII、存在、目录类型、可写和无路径祖先 symlink/junction 的检查，不接受 UNC 或设备路径。
- 目录浏览是认证后的只读宿主接口，只返回有界的盘符/子目录列表，不读取文件内容、不建立工作区或
  启动 Agent。浏览与最终创建各自验证，浏览结果不授予绕过创建校验的能力。
- 已有文件由共享 workspace snapshot 捕获为初始基线。原文件不会因新建 Web 会话被认作 fresh 产物。
  后续其他会话修改目录，仍受原恢复一致性检查约束，不通过重设基线掩盖变化。
- 同一 Web 实例禁止在重叠目录的另一会话仍活动，或有未完成/待核查执行时创建或启动相关会话。
  不增加跨实例、CLI 或外部编辑器的工作区锁，不声称事务隔离或 OS 沙箱。
- Web registration 使用 v3；旧版本明确拒绝，不回填来源或升级旧登记。CodingRun launch v7 不变；冻结评测
  与历史证据保持原状。

## 替代方向与代价

继续强制空目录最简单，但无法满足已有工程工作流。把工程复制到托管目录会增加大型 ODB 的复制成本并改变
用户修改原工程的预期。浏览器文件句柄也不能直接替代 Python/Abaqus 所需的宿主目录路径。

采用显式本机目录绑定可复用现有 Python 执行路径；代价是路径校验与同目录会话检查，以及已有大目录的
基线扫描成本。前端改编 Pi 组件，服务端保留 FastAPI，不增加 Node 后端或第二套会话运行时。

## 替代关系

局部替代 [ADR-0025](0025-local-web-workbench-over-coding-run.md) 的“网页只能使用管理根下新建空工作区”
限制。其本地单用户、宿主权威状态、REST 命令/SSE 通知和文件访问边界继续有效。
其中“新会话可自动创建托管空目录”由 [ADR-0041](0041-require-selected-workspace-for-web-runs.md)
局部替代；已有目录选择、路径校验、会话固定绑定和 state 归属决定继续有效。

## 实现与验证入口

- [目录浏览](../../src/abaqus_sim_agent/web/directories.py)、[路径与登记](../../src/abaqus_sim_agent/web/storage.py)
- [宿主接线](../../src/abaqus_sim_agent/web/controller.py)、[选择器](../../webui/src/DirectoryPicker.tsx)、
  [侧栏工作区切换](../../webui/src/ConversationList.tsx)
- [定向离线测试](../../tests/test_web_directories.py)、[来源记录](../../webui/THIRD_PARTY_REUSE.md)
- 当前部署和数据归属见[系统框架](../architecture/system-framework.md)。目录接线不构成真实模型或物理验收。
