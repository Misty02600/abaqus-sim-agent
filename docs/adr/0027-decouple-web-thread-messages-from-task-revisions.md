# ADR-0027：将 Web Thread 消息与 CodingRun task revision 解耦

## 状态

已采纳。

## 日期

2026-09-16

## 当时遇到了什么

Web 多轮会话已经能把追加消息先保存到 `registry/<run_id>/thread.json`，但实际执行前仍会把每条普通消息转换成
`task_revision` amendment。结果是“继续问一句”与“显式改变任务合同/验收基线”共享同一种状态变化：每条消息都会
推进 revision、归档旧 outcome 并借用 amendment ledger 表达聊天顺序。这个实现能够恢复和幂等，但产品 Thread 与
CodingRun 的执行/验证语义仍然耦合。

当前还必须保持已有文件后端恢复、累计预算、Pydantic AI/Harness history、未决 runtime reconcile、问答门禁、
artifact freshness 与现有 `/api/v1/runs/...` 前端合同。没有证据需要为此引入数据库、第二套 Agent runtime 或
每条消息一个独立 CodingRun。

## 最后决定

Web Thread 成为普通追加消息的产品层持久事实，CodingRun 继续拥有 Agent 执行、恢复、预算、工具副作用、
verification 和 outcome。

- 普通 `POST /api/v1/runs/{run_id}/messages` 先原子保存 Thread message。新消息不写 `amendments.json`，也不推进
  `task_revision` 或 amendment sequence。
- 调度时宿主用消息 ID 作为 `continuation_id`、原始文字作为 continuation prompt 恢复同一 CodingRun history。
  `resume_state.json` 保存当前 continuation ID，以便 Provider/进程中断后识别“同一执行段的恢复”。
- 每个新的 continuation 以消息提交前最后一个已持久 workspace snapshot 和当时已有 execution 数量作为本段
  verification baseline。旧文件和旧 Abaqus 调用可以作为上下文，但不能作为本段 fresh 交付或执行证据。
- Thread 的 execution link 同时保存 continuation ID、当时 task revision 和 amendment sequence。普通连续消息可以
  共享相同 revision/sequence，continuation ID 才是这类执行段的唯一关联。
- 如果 CodingRun 已经落到 terminal 状态，但 Thread execution link 在最后写入时失败，显式 continue 只补写该链接，
  不重新调用模型。Provider/运行中断仍保留 pending message，并在显式 continue 时恢复相同 continuation。
- 显式 task revision 仍由既有 amendment 协议表达，用于真正改变任务合同和 revision 验收基线。旧 v2 amendment 中
  已保存的 message metadata 继续可读，并可在缺少 `thread.json` 时迁移到 Thread；新 Web 消息不再产生这种记录。
- 为保持现有 UI/API 兼容，当前 Thread 仍以 Web `run_id` 作为存储键，并关联一个 CodingRun state/workspace。这个
  一对一物理布局不是“Thread 等于 task revision”的语义承诺；若以后确实需要一个 Thread 管理多个独立 CodingRun，
  再单独改变身份和生命周期合同。

## 为什么这样选

它把两种不同的边界分开：Thread 回答“用户在长期会话里说了什么”，CodingRun 回答“这一段实际执行了什么、
如何恢复、哪些证据属于当前验收”。同时继续复用已经验证的 Harness history、文件恢复和 verifier，不需要为了
聊天持久化再引入一套 workflow runtime。

每个 continuation 单独捕获 evidence baseline 也保留了原 task revision 的关键安全性质：旧产物不会因为普通消息
不再递增 revision 而自动变成新请求的 fresh 交付。

## 没有采用的方案

- 继续把每条聊天消息写成 task revision：实现最少，但长期把聊天轮次、任务合同和验收边界混为一谈。
- 每条消息新建一个 CodingRun：边界最彻底，但会立即拆散现有 history、累计预算、问答恢复、artifact 与 runtime
  reconcile，当前需求没有证明这项复杂度有收益。
- 迁移 SQLite、LangGraph checkpointer 或 durable workflow：本次状态量和并发模型由现有原子文件与单实例锁即可覆盖。

## 带来的影响

- 有利：普通对话不再污染 amendment/task-revision 语义；Thread message 与执行段仍可唯一追踪；旧文件/执行证据
  不会跨 continuation 冒充 fresh evidence。
- 代价：当前仍维持一个 Thread 对一个 Web run/CodingRun 的兼容布局；Thread 与执行状态分别写入两个文件域，必须
  保留“消息先保存、执行后链接”的恢复测试。
- 风险：Thread link 与 terminal CodingRun 之间存在一个很短的双写窗口，因此必须保留 terminal-link recovery；这不是
  数据库事务或外部 Job exactly-once。

## 落实与确认

实现位于 `web/storage.py`、`web/controller.py`、`coding/composition.py`、`coding/service.py`、`coding/resume.py` 与
`coding/read_model.py`。`tests/test_web_conversation.py`覆盖三轮上下文、跨进程重启、重复消息、dispatch/provider 失败、
terminal link 丢失恢复、pending question/reconcile 和 continuation evidence baseline；既有 CodingRun resume/amendment
测试继续约束显式 revision 路径。

## 替代关系

本决定替代 ADR-0025/0026 后续落实中“Web 普通追加消息通过 task revision 执行”的实现选择；不替代 ADR-0021 的
显式 amendment/task revision 合同，也不改变 ADR-0020 的文件后端恢复边界。

## 相关文档

- [项目架构](../architecture/overview.md)
- [ADR-0020：使用 Harness StepPersistence 续跑 CodingRun](0020-resume-coding-runs-with-harness-step-persistence.md)
- [ADR-0021：使用 Amendment 与 Task Revision 表达续跑中的任务修改](0021-use-amendments-and-task-revisions-for-continuation.md)
- [ADR-0025：在 CodingRun 宿主之上增加本地 Web 工作台](0025-local-web-workbench-over-coding-run.md)
- [ADR-0026：按当前请求范围支持文字回答与局部执行](0026-scope-requests-and-text-responses.md)
