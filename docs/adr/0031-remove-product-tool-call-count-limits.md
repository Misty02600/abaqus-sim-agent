# ADR-0031：普通产品不按低成本工具调用次数终止 CodingRun

## 状态

已采纳。用户于 2026-09-17 确认普通工具不设置调用次数限制。

## 日期

2026-09-17

## 当时遇到了什么

`CodingRunBudget` 当前分别把 API lookup、Concept lookup、diagnostic retrieval 和 static preflight 限制为
4/2/2/12 次。这些字段最初用于冻结小样本实验和防止模型循环，后来进入普通产品 request、resume、
amendment 与前端展示，形成了与任务实际需要无关的硬终止条件。

普通产品已决定不限制 model requests、clarification 或 active wall，并由上下文压缩支持长会话。继续用
很小的逐工具次数上限会让正确任务因查证次数而失败，也把实验期参数误当成产品权限。Abaqus/Solver 不同：
它们会启动外部 vendor runtime，仍需要明确的用户权限和可选有限额度。

## 最后决定

- 普通产品不再按 API lookup、Concept lookup、diagnostic retrieval 或 static preflight 的累计调用次数拒绝
  工具；移除对应 `max_*_calls` 产品字段和 `reserve_*` exhausted 分支。
- 继续记录每类调用的累计 usage、query 去重、journal、来源绑定和返回证据；“不限制次数”不等于取消
  输入校验、输出大小、证据范围、去重、operation timeout 或失败关闭。
- Abaqus 与 Solver 使用独立 `deny` / `allow` / `allow_with_limit(n)` runtime permission，不归入本决定的
  低成本工具。
- 显式启用的 bounded document-policy/evaluation profile 可以保留其冻结调用合同；历史 benchmark、trial、
  seal 和报告不回填新默认。新评测若要限制工具，必须在自己的版本化合同中声明，不能泄漏为产品默认。
- 不向模型注入工具次数或剩余量提示；产品 UI 可以展示 usage，但不将其解释为终止预算。

## 为什么这样选

调用次数不能衡量一次查证是否必要，也不能可靠阻止循环；小上限首先惩罚复杂但正确的任务。已有 active
取消、单次 operation timeout、上下文治理、typed runtime permission 和宿主 verifier 分别控制卡死、容量、
外部副作用与完成条件，不需要再用低成本工具总次数承担这些职责。

## 没有采用的方案

- 把 4/2/2/12 提高到另一组固定大数：仍然没有任务语义依据，只会推迟同一种误杀。
- 接近上限时提示模型收尾：会把宿主内部计数写入模型行为，并污染固定评测。
- 取消所有工具边界：runtime permission、路径 allowlist、证据范围、单次 timeout 和输出限制仍有独立必要性。

## 带来的影响

- 有利：普通任务可以按需要重复查证或预检，不因实验期次数耗尽失败。
- 有利：usage 与 permission 分离，前端不再把所有计数都呈现成预算。
- 代价：异常循环不再由逐工具总数兜底，必须依赖用户停止、单次 timeout、模型请求取消和可观察 usage。
- 不变：检索默认是否启用、来源绑定、物理输入不得补造、runtime 权限和 verifier 合同均不改变。

## 替代关系

- 部分替代：[ADR-0009：精确 Abaqus API Catalog](0009-exact-abaqus-api-catalog.md) 的产品 API lookup 次数预算。
- 部分替代：[ADR-0010：显式受预算的官方概念检索](0010-budgeted-official-concept-retrieval.md) 的每 run 两次
  Concept 查询上限；其显式启用、证据范围和 usage/journal 决定继续有效。
- 部分替代：[ADR-0018：分级 ACI 与已验证 API 合同](0018-enforce-verified-abaqus-aci.md) 的 static preflight
  次数耗尽分支；权限、syntax/API gate 与 vendor runtime 前检查顺序继续有效。

## 相关文档

- [PLAN-0012：统一 Agent 运行预算、超时、上下文压缩与收尾策略](../plans/done/0012-agent-runtime-budget-and-context-policy.md)
- [知识接入](../architecture/knowledge-access.md)
