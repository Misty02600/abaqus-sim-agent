# ADR-0035：永久删除 Web 会话记录并保留工程文件

## 状态

已采纳，2026-09-22。用户明确选择 Pi 的永久删除方式，保留工程文件，并要求执行收尾由实现负责。

## 决定

- 用户确认后，`DELETE /api/v1/runs/{run_id}` 永久清理会话 history、问答、执行/验收及 Web 管理记录。
  自动创建或用户选定的工程目录及文件均保留，没有回收站。
- 删除期间禁止该会话再提交命令。宿主通过 Pydantic AI 原生 `AgentRun.cancel()` 结束工作段，并等待自己
  控制的工具和 Solver 实际清理完成；不能以桥接 Future 标记 cancelled 代替进程退出。
- 图外等待取消所属事件循环任务。现有“停止回复”按钮仍仅作用于当前模型请求；删除不扩大其语义。
- 执行收尾失败或单次HTTP等待超时返回错误，记录不提前清理。其他宿主持有的锁、未决 runtime effect、
  不可验证的记录或丢失控制句柄的外部执行均不猜测处理，不按进程名终止，不增加跨进程重新附着。
- 停止活动执行前先保存删除收尾记录，所有受控执行确认退出后才标为 settled；清理失败经服务重启仍不能
  当作已结束。runtime 边界先捕获收尾错误，避免 Agent 的取消异常覆盖真实的清理失败。
- 删除目标从固定管理根和合法 run ID 构造，递归清理前验证归属并拒绝 symlink/junction；state 内容在其
  OS 锁内清理。workspace 路径只告知文件保留位置，绝不用于删除目标构造。
- `control/deletions` 保留最小回执：ID、保留目录、时间、purging/deleted。它没有消息或可恢复的会话副本，
  用于阻止旧创建请求重放复活同一ID，以及在部分文件清理失败后重试。列表提供未完成删除的重试入口。
- 页面收到成功回执后移除会话，清理相应浏览器草稿和重试ID，当前会话回到新建入口。其他标签页通过通知
  重取，410使其退出已删除会话。使用已有本机会话和CSRF校验。

## 理由与代价

回收站可恢复误删，但用户明确选择直接删除。只移除侧栏项目仍会留下历史，因此清理宿主记录；工程目录
可能被多个会话使用并包含用户原始输入，因此始终保留。

取消、等待和删除跨Web调度、Agent生命周期与存储协调，但不增加第二套运行时、数据库或持久队列。
最小回执只保证应用请求重试与文件清理，不是外部作业事务或备份。

## 实现与验证

- [删除存储](../../src/abaqus_sim_agent/web/deletion.py)、[宿主接线](../../src/abaqus_sim_agent/web/controller.py)
- [取消控制](../../src/abaqus_sim_agent/coding/response_stream.py)、[Service](../../src/abaqus_sim_agent/coding/service.py)
- [删除测试](../../tests/test_web_deletion.py)、[Harness兼容测试](../../tests/test_coding_harness_compat.py)
- Windows Harness 0.31.0 前台 Shell 取消修复有精确版本门，MIT来源与修改记录见
  [harness_shell.py](../../src/abaqus_sim_agent/coding/harness_shell.py)。CLI/evaluator不注入删除控制器。

## 关系

扩展 [ADR-0025](0025-local-web-workbench-over-coding-run.md) 的会话操作，沿用
[ADR-0027](0027-decouple-web-thread-messages-from-task-revisions.md) 的状态所有权，以及
[ADR-0034](0034-bind-selected-workspaces-for-web-runs.md) 的工程目录与宿主记录分离。

其中对当时“停止回复”按钮仅作用于模型请求的描述，已由
[ADR-0036](0036-interrupt-web-work-segments-and-manage-background-jobs-separately.md) 局部替代；本 ADR 的删除
收尾、永久清理和工程文件保留决定不变。
