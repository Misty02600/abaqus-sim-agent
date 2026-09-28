# ADR-0016：以固定 abqpy 与 BasedPyright 提供可回退的静态预检

## 状态

已替代；由 [ADR-0018](../0018-enforce-verified-abaqus-aci.md) 引入不可由模型绕过的分级 ACI

## 日期

2026-08-29

## 遇到了什么问题

当前 Coder 直接编写普通 Abaqus Python。Exact Catalog 能在显式查询或真实 runtime error 后返回签名和
verified note，但初稿中的虚构方法、错误 owner、非法 keyword 仍可能先消耗一次 licensed Abaqus 调用。
项目已经固定保存 `abqpy-2024.10` wheel；它的源码带类型注解，因此可以作为第三方静态类型来源，而不必
为 Abaqus 动态对象模型再自建一套 AST/type checker。

但原始 abqpy 注解不能直接充当 truth。固定 wheel 内有 1,406 个 `.py`、没有 `.pyi` 和 `py.typed`；真实
BasedPyright 审计在已通过的 B31 脚本上仍误报 `assignBeamSectionOrientation(Set)`、
`setElementType((EdgeArray,))`、`Job.writeInput` 和多类 ODB Optional。对仓库 21 份 Abaqus 2024 正例脚本，
原始检查共给出 134 条不应阻断运行的诊断。把它们全部交给 Agent 或设为 hard gate 会把 vendor type-hint
缺陷变成新的产品故障。

## 决定

增加显式 `advisory` 静态预检，默认 `off`，并固定以下边界：

- Composition Root 只接受显式 abqpy wheel 和 workspace 外 cache。宿主校验 wheel 路径、包名、版本、
  SHA-256、压缩包逃逸和 symlink，再解包到 immutable hash 目录。abqpy 不安装进 Abaqus 2024，也不进入
  Agent workspace。
- 每个生产 `.py` hash 在进入 `run_cae_script` 或 `run_abaqus_python` 前最多检查一次。调用使用项目宿主
  Python 下固定版本 BasedPyright，原始 stdout/stderr bytes 和结构化报告写入 workspace 外 state。
- `abqpy-advisory-high-confidence-v2` 不重新解析 Agent AST 或推导类型，只筛选第三方诊断。当前放行的高
  置信形态是未知 attribute（已验证的 `Job.writeInput` 误报除外）、不存在 keyword、缺少必需参数，以及
  两个有 licensed failure fixture 的 container binding；其他 raw diagnostics 只留证，不反馈给模型。
- 第一次高置信报告暂停本次 vendor 调用，不消耗 Abaqus budget。Agent 编辑脚本后，新 hash 重新检查；
  clean 后正常运行。原样重试仍作为误报逃生口，但 Prompt 要求只有具体 Exact Catalog 或既有 licensed
  runtime 证据与诊断冲突时才使用，不能只为让 Abaqus 再确认同一错误。
- BasedPyright 不可用、输出损坏或超时时 fail open；静态预检不能替代真实 runtime 和 host verifier。
  `max_static_preflight_calls` 单独计数，`CodingRunUsage`、request binding、evaluation metadata 和
  `static_preflight.jsonl` 都保存实际版本与用量。
- 该能力不查询自然语言 Catalog 描述来“猜冲突”。当前 Catalog 的 `conflicts` 只比较官方与 abqpy 参数名
  集合，尚不能可靠仲裁类型、required/default 或 alias；因此不把现有 conflict 数量包装成静态真值。

## 为什么这样选

BasedPyright + abqpy 能在约 1.18 秒内发现一组真实 API binding 错误，并保留标准工具的定位、rule 和消息；
这比维护第二套 Abaqus parser/type system 更可控。固定 source hash、engine version 和 policy version，又让
每次建议可重放和审计。

同时，正例审计已经证明 raw type hints 存在大量假阳性，所以首轮不能 hard block。高置信筛选、licensed
override、原样逃生口和 fail-open 把“提前反馈”与“runtime 最终权威”分开。后续只有在新的正负例与
Product treatment 支持时才扩展 policy；不因单条模型轨迹自动写回全局规则。

## 没有采用的方案

- 自建 Abaqus AST/static checker：会重复类型推导、owner resolution 和动态容器语义，且用户明确不希望
  项目维护一套脆弱的自定义检查器。
- 原始 BasedPyright error 全部 hard block：21 份正例已有 134 条 raw 误报，无法接受。
- 把 abqpy 安装进 Abaqus vendor Python：宿主静态工具不应污染 2024 embedded Python 或成为运行依赖。
- 只在 Abaqus error 后查询 Catalog：保留为 baseline，但无法提前发现初稿中多个独立虚构调用。
- 把 Catalog `not_found` 直接当否定证明：当前 alias/coverage 与 conflict 模型不足以支持这种强结论。

## 证据与当前结论

- 离线 v2 policy：21 份正例 0 次误暂停、5 份已由 licensed Abaqus 2024 复现的坏脚本 5/5 命中；134 条
  raw 正例诊断被过滤。单脚本平均约 1.18 秒。缺少 `assignBeamSectionOrientation(method, n1)` 的诊断又由
  B31 v1 真实 runtime 在同一行确认。见
  [`abqpy_static_preflight_policy_v2_20260829.json`](../../../benchmarks/coding/abqpy_static_preflight_policy_v2_20260829.json)。
- Product v1：同一 B31 请求、Go/Flash、final15 automatic、allowlist、预算和 verifier 下为 2/3；一条为
  pre-file transport timeout，两个 task-started attempt 均通过 oracle。复杂通过样本在运行前发现
  `createStepName`、`orientation`、`seedPartByNumber` 和 `Model.writeInput`，但 Agent 对每个 hash 都先原样
  重试，Abaqus 随后确认相同错误，最终仍用了 9 次 Abaqus。它证明诊断命中和 clean-path 兼容，不证明调用
  节省或 gate 收益。
- Product v2：三条都在生产文件、静态预检和 Abaqus 前因 OpenCode Go `Request timed out` 结束；该批对
  v2 adoption 无效，不能把 0/3 归因静态策略。完整冻结结果见
  [`beam_tension_b31_abqpy_preflight_evaluation_20260829.json`](https://github.com/Misty02600/abaqus-sim-agent/blob/aa7116f0377a6fd5f1173b057797ba54abe70478/benchmarks/coding/beam_tension_b31_abqpy_preflight_evaluation_20260829.json)。

因此当前推荐 B31 profile 仍是未启用 preflight 的 v7。v2 只能描述为已实现、离线门槛通过、Product 行为
待重测的 opt-in candidate。

## 带来的影响

- 有利：在 vendor side effect 前得到版本固定、低延迟、带源码位置的 API 反馈，并单独量化静态调用。
- 有利：误报、override、原始输出、Agent 是否修改或绕过都成为可审计证据，而不是隐藏 Prompt 建议。
- 代价：每个新脚本 hash 增加约一秒本地开销；有诊断时还增加一次模型往返。
- 风险：高置信 policy 仍来自有限正负例；未见过的 abqpy 缺口可能误报或漏报，所以默认关闭且不取代 oracle。
- 风险：advisory 逃生口可能被模型当成普通路径；v1 已真实复现。v2 Prompt 的批量修复效果仍无有效
  Product treatment，不得提前声称收敛改善。

## 主要实现

- `src/abaqus_sim_agent/coding/static_preflight.py`
- `src/abaqus_sim_agent/coding/contracts.py`
- `src/abaqus_sim_agent/coding/agent.py`
- `src/abaqus_sim_agent/coding/cli.py`
- `src/abaqus_sim_agent/evaluation/beam_tension.py`
- `tests/test_static_preflight.py`

## 相关文档

- [ADR-0008：首轮采用 Pydantic AI Harness 单 Coder](../0008-pydantic-ai-harness-abaqus-coding-run.md)
- [ADR-0009：构建精确 Abaqus API Catalog](../0009-exact-abaqus-api-catalog.md)
- [当前运行流程](../../architecture/flows/simulation-run.md)
- [技术栈与工程讲解点](../../architecture/technology-stack.md)

## 替代关系

- 被替代：[ADR-0018：以分级 ACI 强制生产脚本语法和已验证 API 合同](../0018-enforce-verified-abaqus-aci.md)
