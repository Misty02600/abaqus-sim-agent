# 一次 CodingRun：问答、恢复与验收

## 这条流程保证什么

宿主拥有任务、预算与终态；Agent 提出代码、问题和验收请求。新普通 CLI 使用 `ask_user_v1`，
旧 run/Gold/未迁移 profile 按保存的 legacy 协议执行。以下状态是宿主语义，不是 LangGraph 节点。

## 首次执行与用户待答

1. CLI 解析 workspace/state、模型、runtime、预算与可选知识绑定，取得同 state 的 OS 独占锁。
2. 保存基础 request/launch 与 initial snapshot，组装 Coder、工具和对应 scope 的 verifier。
3. Agent 可先读工作区事实；物理输入不足时提出 `ask_user(question)`。问题与其他工具/final 混发，
   宿主在副作用前整轮拒绝，不能只靠顺序执行屏障。
4. 宿主保存问题、关联 history 和 `waiting_for_user`，退出活动执行并释放锁。Console 在锁外等待；
   空输入、EOF、Ctrl+C 保留待答，CLI 返回 2，等待不计 active wall。
5. 锁内按 question ID 持久化答复，再交给模型。同 ID 同答幂等，不同答案拒绝覆盖；写入后中断可从
   既存答案继续，不重复索求。取消问题不伪造用户答案。

`CodingRunService.run_step` 可返回 `WaitingForUser`；旧 `run` 保留 settled-result 契约。
legacy `needs_input` 继续使用原 `field_id`、answers 与预先澄清条件，不被新协议静默转换。
新建会话的具体组合选择以 [CLI](../../../src/abaqus_sim_agent/coding/cli.py)为准。

## 编码、运行与修复

1. Coder 编写普通 Abaqus Python，可按显式 profile 获取知识证据或静态诊断。
2. 三个 runtime 工具先检查路径、可选 allowlist 和策略，再预留预算；执行前记录输入指纹和 tool effect。
3. runner 运行固定 CAE/Solver/Abaqus Python launcher，记录 raw logs、execution result 和文件变化。
4. 宿主保存 execution journal；Coder 收到当前诊断后修复，所有调用继续累计预算。
5. ready decision 必须声明 deliverable manifest；宿主验收通过才写 completed outcome，否则反馈当前 scope
   的失败证据继续有界修复。模型最终输出的 schema 错误不是工作区 artifact 损坏的证据。

建模 → Solver → ODB 提取是阶段合同。当前路径 policy 不能静态阻断 CAE 脚本内直接 submit，
该缺口见 [PLAN-0011](../../plans/todo/0011-block-solver-submit-inside-cae-script.md)。
Solver 超时、进程 0 和 stage success 的区别见 [Job 生命周期](job-lifecycle.md)。

## 中断与继续

Provider/API 异常或 task cancellation 保存 `interrupted`。`--resume` 核对 base launch、知识绑定、
workspace 指纹、累计宿主状态和 Harness settled snapshot，在同一 conversation 开启新 turn。
已完成文件工具和执行证据可继续使用；首次响应尚未形成时使用原始 goal。

恢复必须先判断是否存在未决 Abaqus effect：只有 started、没有 terminal 证据时，进入 `needs_reconcile`，
不发新模型请求，也不自动重跑 runtime。损坏 ledger 失败关闭，不从其他日志猜造执行状态。
同 state 锁阻止第二个执行段，恢复不会重新捕获当前工作区来冒充最初 freshness 基线。

API lookup/discovery journal 与 snapshot 合并恢复计数、去重和实际已交付内容；候选不转成已读卡片，
旧 trace 不推断缺失的 delivered 状态。具体见 [知识接入](../knowledge-access.md)。

## Amendment 与 task revision

- 普通 resume 继续同一个 revision，累计 usage/active wall 保留；两次执行之间的用户等待不计活动预算。
- operational amendment 在 immutable base 上叠加允许的预算/provider 等配置，不清零用量，也不能任意改变
  run 内推理策略、工具列表或知识绑定。不兼容修订应在写 ledger 前拒绝。
- 新 goal 形成 task revision，保存旧 outcome，捕获新 freshness 基线和 execution offset。旧产物或旧阶段
  记录不能直接验收新任务。
- 已经发生 Abaqus 执行后的新工程答复同样建立 task revision；问题 ID 关联用于避免恢复时重复修订。
- terminal 任务的继续遵循显式 amendment/revision 合同，不把 `--resume` 当作无条件重新运行。

## 完成的含义

`execution_evidence` 要求 manifest 各项存在且 fresh、fresh Python 的 3.10 语法有效、每种已使用 invocation
最后一次 stage 通过。它不保证自然语言需求完整、未运行脚本的行为或物理结果。

`task_oracle` 由固定 evaluator 注入，另检查对应 source、INP/ODB、solver provenance、数值与回归。
Gold 从空工作区开始；verified-seed change 先核对 source 和 hash，再从缓存脚本建种子。具体 Case 的
文件修改范围和 gate 保持冻结，不能由产品默认演进替换。

失败、预算耗尽和 Provider 中断分别留证；显式 evaluator 的预算耗尽验收仍须完整通过其 verifier。
当前缺少 PID/Job 重新附着、人工 reconcile 解锁、异步 Job 管理和完整 durable workflow。

## 决定与实现

- [ADR-0019](../../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md)：验收 scope。
- [ADR-0020](../../adr/0020-resume-coding-runs-with-harness-step-persistence.md)、
  [ADR-0021](../../adr/0021-use-amendments-and-task-revisions-for-continuation.md)：恢复与修订。
- [ADR-0024](../../adr/0024-host-owned-user-question-waiting.md)：新问答协议。
- [Service](../../../src/abaqus_sim_agent/coding/service.py)、[问答](../../../src/abaqus_sim_agent/coding/user_questions.py)、
  [恢复](../../../src/abaqus_sim_agent/coding/resume.py)、[修订](../../../src/abaqus_sim_agent/coding/amendments.py)。
- [验证证据入口](../verification-evidence.md)：实现与真实任务验证分别有什么证据。
