# ADR-0014：用 typed probe 协议约束诊断结论和 production 收敛

## 状态

已采纳

## 日期

2026-08-26

## 当时遇到了什么

`load_mode` 的失败轨迹会创建多份 CAE/Abaqus Python 探针。脚本、输出、history 和 execution log 虽然都被
保存，但宿主只能知道诊断脚本是否正常执行，不能判断它证明的是候选成功、候选失败还是信息不足。模型曾在
probe 已经找出可行 Region 形式后继续确认，最终耗尽预算；扩大预算又同时容纳了 44-request 成功和
48-request、16 次 Abaqus、0 Solver 的失败，说明预算不是停止条件。

仅靠“probe 成功后应用”的提示词仍缺少两个机器边界：什么算成功，以及结论允许写回哪个 production
文件。若宿主把 exit code 0 当成候选成功，会误接纳内部捕获了全部异常的探针；若目标不受约束，模型又可
把新建 probe 文件声明为 production target，绕开真实任务脚本。

## 最后决定

- typed probe 协议是显式模式，默认 `off`。开启时，模型调用 `run_cae_script` 或
  `run_abaqus_python` 必须声明 `purpose=production|diagnostic`。
- diagnostic 执行后，宿主冻结该 Case 允许的 production targets 及其 SHA-256，并要求下一次 Abaqus
  runtime、Solver 或 verification 前提交 strict `ProbeResultInput`：`validated`、`rejected` 或
  `inconclusive`，以及 claim 和 evidence。
- `validated` 必须指定 Case allowlist 中的 production Python；model-change 的 `load_mode` 与
  `center_hole` 只允许 `build_model.py`，`dual_material` 允许两份既有脚本。新建 probe 文件不能作为目标。
- validated 后，目标 hash 必须改变，且下一次允许的 runtime 只能是该目标的 production 执行；在此之前
  阻断后续 diagnostic 和 Solver。production 结果无论成功或失败都追加 adoption trace；失败结果成为新的
  runtime evidence，并释放旧门禁。
- 为减少一次模型往返，结论可作为 `probe_result` 原子附在下一次 CAE/Abaqus Python/Solver 调用上；独立
  `record_probe_result` 只作为没有下一步 runtime 时的兜底。
- conclusion 与 adoption 以 strict JSONL 追加到 workspace 外的 `probe_results.jsonl`。直接请求
  verification 时仍有未结论或未采用的 probe，`CodingRunService` 不调用 oracle，而是把具体门禁反馈给
  Coder。
- probe 证据不会自动晋升为 Skill、Catalog runtime note 或跨 run Memory。长期知识仍需独立的验证和采纳
  流程。

## 为什么这样选

协议把三种原本混在文本 history 中的状态分开：诊断脚本执行、诊断 claim 成立、候选已进入 production。
宿主只约束状态转换和目标所有权，不解释 Abaqus API 语义，也不自动修改模型代码。这样既保留动态 Python
探索，又避免把探针本身冒充交付物或 verifier。

原子结论允许模型在读到 diagnostic evidence 后直接准备下一次有意义的 runtime，不必为单独的记录工具
再付一次模型往返。Case allowlist 来自已验证 seed 的实际文件职责，不包含题目答案。

## 没有采用的方案

- 看到 `stage_succeeded=True` 就自动标为 validated：只能证明脚本运行，不能证明内部候选成功。
- 通过 `probe*` 文件名自动识别并提炼结论：文件名不是稳定语义，也无法表达 claim 和 target。
- 把本题 `Assembly.Surface(side1Edges=...)` 直接写入 Skill：会污染通用 baseline，且不能解决停止条件。
- 只继续扩大请求/Abaqus 预算：正式消融已观察到相同 2/3 gate 和更长的无 Solver 循环。

## 带来的影响

- 有利：每次声明过的 diagnostic 都有 typed claim、evidence、runtime status 和稳定 probe id。
- 有利：validated candidate 不能采用到临时 probe；target hash 和 production execution 形成可审计因果链。
- 有利：协议关闭时工具 schema 和旧基线不变；开启后的正常 production 路径不必创建任何 trace。
- 代价：需要模型判断结论状态；当所有结果都 inconclusive 时，协议会留下更清楚的失败链，但不会替模型
  找到正确候选。
- 代价：独立 `record_probe_result` 会增加一次模型往返，因此 v2 优先使用原子 conclusion。
- 风险：普通读文件和通用 shell 仍可在结论前消耗模型请求；协议当前只阻断 Abaqus side effect、Solver
  和 verification，不是全工具状态机。

## 落实与确认

- strict contracts、状态协调和 JSONL persistence 位于 `coding/contracts.py` 与 `coding/probe.py`；Agent
  runtime gate 位于 `coding/agent.py`，verification gate 位于 `coding/service.py`。
- v1 Product-Agent 诊断批次为 0/3：它验证了 persistence/gating，但暴露 probe 文件可被声明为 target，且
  独立结论工具增加往返。v1 保留为被拒实现证据。
- `typed-probe-convergence-v2` 增加 Case target allowlist、diagnostic 时目标 hash snapshot 和原子 conclusion。
  `load_mode + full Catalog + 48/18/3` 正式结果为 2/3，与 no-protocol 高预算 control 相同；两条直接
  production 成功均无 probe trace，证明正常路径不受干扰。失败轨迹留下 7 条 `inconclusive`，没有
  validated/adoption，因此本批不能证明收敛收益。
- agent-tool deterministic test 覆盖 diagnostic → 修改 allowlisted target → 原子 validated conclusion →
  production execution → conclusion/adoption JSONL → gate clear；相关普通测试和静态检查通过。
- 重复的 SurfaceTraction Region gap 没有由协议自动学习；项目先用独立 licensed 微回归验证 Set 失败、
  side-aware Surface 成功，再人工加入 final9 scoped runtime note。同一 typed-v2 Product batch 中 note 投递和
  Assembly.Surface 采用为 3/3，结果仍为 2/3；这确认“显式验证后晋升”的边界，不构成自动 Memory。
- 冻结证据：`benchmarks/coding/plate_model_change_treatments_20260825.json`。

## 相关文档

- [项目架构](../architecture/overview.md)
- [一次 CodingRun](../architecture/flows/simulation-run.md)
- [ADR-0004：以证据和物理验证定义任务成功](0004-evidence-first-physics-verification.md)
- [ADR-0012：用逐 Case gate 的正交变更套件扩展 L1](0012-per-case-model-change-suite.md)
