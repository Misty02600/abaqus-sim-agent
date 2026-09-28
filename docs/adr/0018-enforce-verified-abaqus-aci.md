# ADR-0018：以分级 ACI 强制生产脚本语法和已验证 API 合同

## 状态

已采纳

后续替代：2026-09-22 [ADR-0038](0038-unify-runtime-syntax-and-static-feedback.md) 替代下文的双模式、
编辑语法门和 API blocker 策略；本文保留当时的决定、背景与实验。当前实现见 architecture。

权限、syntax/API gate、证据分级与 vendor runtime 前检查顺序继续有效；普通产品 static preflight
调用次数耗尽分支由 [ADR-0031](0031-remove-product-tool-call-count-limits.md) 部分替代。

## 日期

2026-08-29

## 当时遇到了什么

[ADR-0016](history/0016-advisory-abqpy-static-preflight.md) 接入了固定 abqpy/BasedPyright preflight，但刻意保留
“同一脚本 hash 原样重试即可运行”的模型逃生口。真实 B31 v1 轨迹随后证明这个接口不是有效控制：模型对
5 份高置信报告都先原样重试，Abaqus 2024 又逐项确认同类错误，复杂 pass 最终仍使用 9 次 Abaqus。
Prompt 已经说明应先修复，但模型可以通过接口忽略它，因此正确行为没有成为系统不变量。

[SWE-agent ACI](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md)提供了更合适的抽象：
文件查看、编辑、运行和错误反馈都是面向模型的计算机接口，确定性错误应在产生副作用前由接口拒绝。其
[NeurIPS 论文](https://papers.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf)
中的edit/lint版本明确保持原文件，并让模型重新提交完整候选；但Abaqus的SWIG对象、动态repository和
abqpy类型误报又意味着不能把所有type diagnostic都升级成同样强度的门。

本项目因此需要同时满足：

- 普通 Python 语法错误不能进入工作区中的 production script；
- 由 licensed runtime 或冻结合同确认的 API 错误不能消耗 vendor budget；
- abqpy/SWIG 的不确定类型诊断不能阻断真实 Abaqus；
- 权限检查必须先于静态分析和外部副作用；
- 模型不能自行声明“这是误报”来解除硬门。

## 最后决定

保留旧 `off`/`advisory` 作为兼容和实验模式，新增显式 `enforced_verified` ACI profile，并固定以下顺序：

```text
write_file / edit_file production candidate
→ Python 3.10 candidate syntax gate
→ valid candidate may change workspace

run_cae_script / run_abaqus_python request
→ RuntimeScriptPolicy authorization
→ current production file hash + Python 3.10 syntax recheck
→ abqpy/BasedPyright diagnostics
→ verified blocker / warning arbitration
→ Abaqus budget reservation
→ vendor runtime
→ host verifier
```

具体合同如下：

- `python-3.10-production-syntax-v1` 只拥有 task-owned CAE/Abaqus Python allowlist 中的生产文件。它通过
  Harness `ToolGuardrail` 在 `write_file`/`edit_file` 执行前构造完整候选，使用宿主 `ast.parse` 的 Python
  3.10 grammar 检查。无效候选返回行列诊断，`edit_applied=false`，旧文件不变；运行申请时再按当前 SHA-256
  复核，外部修改不能绕过编辑门。
- `abqpy-enforced-verified-v1` 只把带 `policy_rule_id` 和冻结 `evidence_refs` 的诊断提升为 blocker。当前
  blocker 覆盖已有 licensed API repair fixtures，以及 B31 实机确认的 BeamSection keyword、
  `assignBeamSectionOrientation` 必需参数、`Part.seedPartByNumber` 和 `Model.writeInput`。未知 attribute、
  尚未确认的参数问题和少量 SWIG consumer 类型差异只作为 warning；普通 Optional/overload 噪声不投递。
- 同一 blocker hash 会持续返回 `ApiPreflightFailed`，不进入 `acknowledged_static_preflights`。模型不能通过
  原样重试、解释理由或新增文件解除 blocker；只有修改生产文件，或宿主在后续版本中依据新的 licensed
  evidence 修改 policy/override。
- enforce profile 下 BasedPyright 不可用、输出损坏、静态预算耗尽或 syntax gate 不可用都 fail closed，
  返回 infrastructure/control error 且不消耗 Abaqus budget。旧 advisory profile 继续保持 fail open，便于
  对照和尚未晋升的诊断研究。
- enforce profile 要求 CAE 和 Abaqus Python 的显式 runtime allowlist；未授权路径先返回
  `RuntimeScriptPolicyDenied`，不做 syntax/API 检查。Solver INP 不经过 Python gate，继续由 allowlist、
  runner、INP contract 和 host oracle 负责。
- `syntax_checks.jsonl` 保存 candidate/runtime、hash、cache hit 和 denial；usage/evaluation 分别记录
  syntax edit checks、runtime checks、denials、static preflight 和 Abaqus/Solver 调用。

该 profile 已实现但默认关闭。只有新的 task-started B31 正式批次达到原 2/3 gate 且无正例误阻断后，才
能提出把它替换为推荐 profile；实现完成本身不改写当前已接受的 B31 v7。

## 为什么这样选

语法门使用框架原生 ToolGuardrail，检查的是工具执行前的完整候选，不需要 fork Harness 或修改 `.venv`；
运行前 hash 复核又把编辑时状态和实际副作用绑定起来。Python 3.10 grammar 是 Abaqus 2024 embedded Python
的明确合同，不涉及风格、import 可用性或动态对象推断，误报面远小于普通 linter 全量规则。

API 门继续复用 BasedPyright 的标准定位能力，但“发现”和“是否允许执行”分开。blocker 必须有项目自有的
licensed/冻结证据，warning 则明确把最终决定留给 vendor runtime。这样既实现 ACI 的不可绕过控制，也不把
abqpy 的错误容器类型写成产品真值。

## 没有采用的方案

- 继续只加强 Prompt：v1 已实际证明模型会把原样重试当成正常路径。
- 所有 BasedPyright error 都 hard block：21 份正例曾产生 134 条 raw 诊断，包含正常 Set、native Sequence、
  `Job.writeInput` 和 ODB Optional；会把类型源缺陷转成产品故障。
- 在编辑时运行完整 BasedPyright：约 1.2 秒开销和不确定诊断会干扰每次局部修改；API 检查保留在申请
  vendor runtime 时，语法门才属于编辑事务。
- 允许模型提交 override reason：模型仍拥有解除控制的权力，只是换了字段，不能恢复 ACI 不变量。
- 直接依赖 Catalog `not_found` 或自动修复候选：当前 `Model.HomogeneousBeamSection` 等查询没有候选，
  现有 conflict 也只比较部分参数名；`not_found` 不是完整否定证明。
- 自建 Abaqus type checker：会重复 abqpy/BasedPyright 的类型推导并扩大长期维护面。

## 带来的影响

- 有利：production Python 在任何已授权写入后保持 Python 3.10 语法有效；syntax/API/authorization 的错误
  都在 Abaqus budget 和 vendor side effect 前返回。
- 有利：每个 blocker 都能回答“哪条规则、哪份证据、哪个脚本 hash 阻断了执行”，warning 与 block 不再
  混成一句 Prompt 建议。
- 有利：API checker 故障在 enforce profile 中可见且 fail closed，不会静默退化成无检查运行。
- 代价：有效 production edit 和 runtime 会增加本地语法检查；每个新 runtime hash 仍增加约 1.2 秒
  BasedPyright 检查。
- 风险：verified registry 只覆盖当前证据家族；新的真实错误可能仅 warning 或漏报，仍需 Abaqus/verifier。
- 风险：模型可以通过更动态的 Python 写法避开静态类型推断；该 ACI 防止普通错误和无意绕过，不是对抗
  恶意代码的 OS sandbox。
- 风险：Product treatment 仍受 OpenCode Go transport 影响，不能用离线精度替代任务闭环证据。

## 落实与确认

- 语法门确定性测试覆盖：非法 create/edit 不落盘、Python 3.12-only 语法拒绝、hash cache、外部非法改写、
  相对路径 alias、非生产文件和显式 allowlist。
- API 门测试覆盖：同一 blocker hash 重试仍阻断、无 Abaqus budget、checker 不可用 fail closed、warning
  继续 runtime、RuntimeScriptPolicy 先于 syntax/API。
- 真实 BasedPyright 精度：当前 21 份正例 0 blocker、16 warning；5 份 licensed 负例 5/5 blocker；B31 v1
  四个额外失败家族 4/4 blocker。见
  [`abaqus_aci_enforced_policy_v1_20260829.json`](../../benchmarks/coding/abaqus_aci_enforced_policy_v1_20260829.json)。
- 首次正式 B31 treatment 三条都在生产文件前因 Go `Request timed out` 结束，syntax/API/Abaqus 调用均为
  0；该批对 Product adoption 无效，不补第四条，也不替代 v7。见
  [`beam_tension_b31_aci_enforced_evaluation_20260829.json`](https://github.com/Misty02600/abaqus-sim-agent/blob/aa7116f0377a6fd5f1173b057797ba54abe70478/benchmarks/coding/beam_tension_b31_aci_enforced_evaluation_20260829.json)。

主要实现：

- `src/abaqus_sim_agent/coding/aci.py`
- `src/abaqus_sim_agent/coding/static_preflight.py`
- `src/abaqus_sim_agent/coding/agent.py`
- `src/abaqus_sim_agent/coding/contracts.py`
- `tests/test_aci.py`
- `tests/test_static_preflight.py`
- `tests/test_coding_agent.py`

## 替代关系

- 替代：[ADR-0016：以固定 abqpy 与 BasedPyright 提供可回退的静态预检](history/0016-advisory-abqpy-static-preflight.md)

## 相关文档

- [当前运行流程](../architecture/flows/simulation-run.md)
- [当前系统框架](../architecture/system-framework.md)
- [技术栈与工程讲解点](../architecture/technology-stack.md)
- [ADR-0009：构建精确 Abaqus API Catalog](0009-exact-abaqus-api-catalog.md)
- [ADR-0015：产品 Coder 不暴露通用 Shell](0015-disable-general-shell-for-product-coder.md)
