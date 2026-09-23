# ADR-0021：用 Amendment 和 Task Revision 延续 CodingRun

## 状态

已采纳

## 日期

2026-09-03

## 替代关系

本决定部分替代 [ADR-0020](0020-resume-coding-runs-with-harness-step-persistence.md) 中“resume 不接受新
goal 或运行配置”和“terminal run 一律不能继续”的限制；ADR-0020 的 StepPersistence、累计状态、工作区
一致性、文件锁和未决 Abaqus 副作用边界继续有效。

## 当时遇到了什么

首次恢复实现把 `request.json` 和 `launch.json` 当作整个 conversation 永远不变的合同。这能安全重放一次
中断执行，但不符合 Coding Agent 的正常交互：用户会在旧任务中增加预算、切换模型、要求继续，也会在
任务完成后追加或修改需求。完全拒绝这些输入，会把评测可复现约束错误地变成产品会话限制。

直接覆盖原 request、outcome 或 verification 同样不可接受。预算修改会丢失原配置，物理任务变化会让旧
产物、旧 Solver execution 和旧 verification 被错误地解释成新要求的证据。

## 最后决定

- `request.json` 和 `launch.json` 继续保存首次运行的 immutable base；用户修改写入 strict、无凭据的
  `amendments.json`。ledger 逻辑 append-only，提交时整文件原子替换并使用同一 CodingRun OS 锁。
- 预算、model provider、model name 和 HTTP retries 是 operational amendment。resume 时 replay ledger
  得到 effective request/launch；原 history、usage、active wall time、Abaqus/Solver计数和execution
  journal继续累计。预算可以调高或调低；低于已消耗值时由现有预算门自然结束，不伪造usage重置。
- `--resume <state> --goal <new goal>` 或 `--request-file` 表示新的 task revision。它仍使用同一run/conversation
  和历史，但在 amendment 提交锁内捕获新的workspace snapshot与execution offset。旧文件相对新baseline
  不再fresh，verifier只接收offset之后的execution，旧物理验证不能满足新任务。
- terminal state只有在存在尚未消费的amendment时才能进入新turn；没有新用户输入时仍不可重开。
  每个terminal outcome写入revision/sequence/turn路径，同时更新顶层latest projection；提交amendment前
  还归档已有outcome/verification，因此历史结果不被静默改写。
- `needs_reconcile`、workspace hash和文件锁优先级不变。未知的在途Abaqus副作用不能通过goal或预算修改
  绕过；真实运行持锁时amendment立即失败且不写ledger。
- `resume_state`升级为`coding-run-resume-v3`，记录amendment sequence、task revision、当前revision
  baseline和execution offset。不为旧v2 state自动猜测或迁移这些证据边界。
- 固定evaluator保持自己的冻结Prompt/profile/oracle，不通过产品CLI的task amendment改变正式Case。

## 为什么这样选

“允许修改”与“篡改历史”不是同一件事。base + typed overlay让用户可以像普通Coding Agent一样继续输入，
同时使每个结果仍能回答：它使用了哪一版任务、哪组运行配置、哪条freshness基线和哪些执行证据。

task revision仍使用同一conversation，模型可以利用先前讨论和文件；重新划定verification scope则避免旧
结果穿透到新的物理合同。该实现继续复用Pydantic AI原生history和Harness StepPersistence，不增加第二套
graph runtime或数据库控制面。

## 没有采用的方案

- 任意覆盖`request.json`：简单，但无法证明历史outcome对应哪套合同，也无法安全处理并发写。
- 所有修改都新建完全独立任务：证据最简单，但丢失Coding Agent最重要的连续对话与工作区体验。
- 用模型判断一句新消息是否改变物理合同：分类不可审计。CLI中显式的新goal统一视为task revision；纯
  “继续”不传goal，运行参数使用typed flags。
- task revision继续沿用最初snapshot和全部execution：会让旧文件或旧Solver结果错误通过新验收。

## 带来的影响

- 用户可在resume时修改全部预算字段、provider/model/retry，或提交新的任务目标。
- operational amendment保留累计消耗；task revision保留历史上下文但重新开始当前verification evidence
  scope。当前实现没有独立数据库级Conversation/TaskRevision/RunAttempt表，revision仍位于同一file-backed
  CodingRun state中。
- state writer与loader增加schema、overlay和archive逻辑；损坏ledger或并发revision必须在模型调用前失败。
- 这是本地受控CLI能力，不是多用户配置服务，也不提供外部Job事务或exactly-once。

## 落实与确认

- typed schema、merge与原子ledger：`src/abaqus_sim_agent/coding/amendments.py`
- effective request、resume v3：`src/abaqus_sim_agent/coding/resume.py`
- revision evidence boundary与历史outcome：`src/abaqus_sim_agent/coding/service.py`
- CLI flags与路由：`src/abaqus_sim_agent/coding/cli.py`
- 测试：`tests/test_coding_amendments.py`、`tests/test_coding_cli.py`、
  `tests/test_coding_service.py`、`tests/test_coding_resume.py`
- 真实Provider E2E：同一run完成operational amendment和task revision；同时复现Harness并行JSONL损坏并
  增加精确版本串行补丁。历史run曾由event log恢复，但后续审查确认effect-start早于event-start，当前已
  删除该兼容fallback并对损坏ledger fail-closed。state/evidence边界通过，但尾换行自然语言约束未满足，不能
  把产品`execution_evidence`描述成完整语义验证。证据见
  `benchmarks/coding/coding_run_amendment_revision_provider_e2e_20260903.json`。
- 全量质量门：Ruff、BasedPyright、`452 passed, 26 skipped`。
- Web 在既有决定上接入 runtime-policy operational amendment：命令使用独立 operation ID 幂等，乐观核对
  amendment sequence；待答问题可以保留，修改不自动运行模型。活动/待核查执行拒绝写入，累计 runtime
  usage 不清零，有限次数仍是会话总上限。

## 相关文档

- [一次 CodingRun](../architecture/flows/simulation-run.md)
- [当前系统框架](../architecture/system-framework.md)
- [架构总览](../architecture/overview.md)
