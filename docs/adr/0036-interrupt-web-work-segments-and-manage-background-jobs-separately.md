# ADR-0036：中断 Web 当前工作段并单独管理后台作业

## 状态

已采纳，2026-09-23。用户明确选择 Pi 风格的当前 Agent 工作段中断，并确认后台 Solver 保留独立取消入口。

## 决定

- Web 主停止操作针对当前活动工作段，而不是只针对一次模型请求。宿主为每个工作段分配不可复用的
  operation ID；停止命令必须携带该 ID，过期命令不能取消后来的工作段。
- 宿主通过 Pydantic AI `AgentRun.cancel()` 中断当前模型或前台工具，并等待工具自身的取消收尾。CAE、
  Abaqus Python 和前台 Shell 继续使用各自现有的准确进程树终止路径。只有收尾成功后，CodingRun 才保存
  `interrupted` 与 `phase=stopped`；收尾失败保留为基础设施中断，不能显示成成功停止。
- 停止保留已完成的文件修改、累计 usage、已持久化 history 和模型已经产生的公开部分回复，不形成完成
  outcome。用户可在同一会话继续或发送新消息。
- 已经通过 `solver_job(action=submit)` 后台化的 Solver `ToolExecution` 不随主停止操作取消。它继续由
  execution ID 跟踪，并通过“取消求解”单独终止。Agent 正在 `wait` 时，停止只取消该前台等待，不取消
  Solver future。Solver 活动期间不启动新的 Agent 工作段；完成或受控取消后，宿主保存终态 workspace
  snapshot，只有当前目录与该记录精确匹配时才推进恢复边界并允许继续，未知文件变化仍显式拒绝。
- 删除会话仍采用 ADR-0035 的完整收尾语义：删除前取消并等待当前宿主控制的 Agent 工作段和 Solver，
  因为删除后不再保留管理入口。
- 宿主重启后丢失控制句柄的外部执行继续进入 `needs_reconcile`。停止命令不按进程名猜测终止，也不增加
  PID 重附着、跨进程 exactly-once 或暂停后原地恢复承诺。

## 为什么这样选

主停止操作需要在模型生成、工具执行和模型再次生成之间保持一致。只停止模型流会让前台 CAE 或 Shell
继续运行，用户看到的“停止”与真实副作用不一致。把所有后台作业一并终止又会破坏已显式后台化的长时
Solver；用户可能只想打断 Agent、修改后续方向，同时保留正在消耗许可证和算力的已知作业结果。

因此当前工作段与后台 Job 使用两个控制面：主停止沿 Agent 取消链传播，后台 Solver 按 execution ID 管理。
这与 Pi、Codex 和 Claude Code 将活动 turn/tool interruption 与后台任务管理分开的交互模型一致，同时保留
本项目对 Abaqus 进程树、执行证据和失控状态的更严格宿主边界。

## 没有采用的方案

- 继续只停止当前模型请求：前台工具可能继续运行，按钮语义取决于点击时机。
- 主停止同时取消所有后台 Solver：操作简单，但用户无法只打断 Agent 并等待已有求解完成。
- 只保留逐工具取消：无法统一处理中途模型生成、文件工具、CAE、Abaqus Python 和前台 Shell。
- 按进程名扫描并终止 Abaqus：无法证明进程归属，可能影响其他会话或用户作业。

## 带来的影响

- `/api/v1/runs/{run_id}/stop` 使用 `expected_operation_id`，页面在任意可取消的 Agent 阶段显示“停止任务”。
- 模型流阶段仍保存公开部分回复；工具阶段没有部分回复时只保存停止边界。
- 停止 Agent 后若 Solver 仍在后台运行，页面继续显示其状态和“取消求解”；Solver 到终态并核对 workspace
  snapshot 后，会话才按现有 continuation 合同继续。
- 主停止是中断当前工作段，不是暂停；继续会建立新的宿主工作段并沿用累计状态。

## 落实与确认

- [取消控制](../../src/abaqus_sim_agent/coding/response_stream.py)、
  [CodingRunService](../../src/abaqus_sim_agent/coding/service.py)、
  [Web controller](../../src/abaqus_sim_agent/web/controller.py)
- [前端入口](../../webui/src/Composer.tsx)、[状态栏](../../webui/src/RunStatusBar.tsx)
- [停止测试](../../tests/test_web_stop.py)、[流式测试](../../tests/test_web_streaming.py)

离线测试覆盖模型流、CAE、Abaqus Python、收尾失败和后台 Solver 保留。真实 Provider 与持证 Abaqus
进程的停止仍需单独运行验证，不能由离线夹具替代。

## 关系

- 扩展 [ADR-0025](0025-local-web-workbench-over-coding-run.md) 的 Web 命令和状态投影。
- 局部替代 [ADR-0035](0035-delete-web-conversation-records-and-keep-workspaces.md) 中对既有“停止回复”按钮
  仅作用于模型请求的描述；ADR-0035 的删除收尾与文件保留决定不变。
- 沿用 [ADR-0033](0033-split-solver-submit-and-wait-actions.md) 的后台 Solver 所有权。
