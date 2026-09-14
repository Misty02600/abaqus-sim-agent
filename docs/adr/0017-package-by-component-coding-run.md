# ADR-0017：以扁平组件图组织 CodingRun 产品包

## 状态

已采纳

## 日期

2026-08-29

## 当时遇到了什么

当前首轮产品已经形成 `coding`、`execution`、`verification`、`retrieval`、`api_catalog`、`evaluation`
和 `skills` 等一级子包，但仓库还保留被 ADR-0008 替代的 `agents`、`application`、`domain`、`inference`
研究资产。只看目录名称容易得到两种相反误解：要么继续补齐旧的全局分层控制面，要么把所有支撑能力
重新塞回 `coding`，认为单 Agent 就应对应单一模块。

当前代码还出现了两个真实边界压力：`CodingRunService` 从 `verification.base` 导入 verifier Protocol，而
`verification.base` 又依赖 `coding.contracts` 与 `coding.workspace`；多个包的 `__init__.py` 也同时重导出
当前运行类型、历史类型或组件局部 evaluator。需要先确定长期组件和依赖方向，再判断具体文件是否值得拆分。

外部实践没有给出 Agent 应用的统一目录标准。[Martin Fowler](https://martinfowler.com/bliki/PresentationDomainDataLayering.html)
建议系统增长后使用顶层 domain-oriented modules，并在模块内部继续分层；
[Simon Brown](https://simonbrown.je/modular-monolith/) 的 package-by-component 把相关功能封装在粗粒度
组件的窄接口后；[Robert C. Martin](https://blog.cleancoder.com/uncle-bob/2011/09/30/Screaming-Architecture.html)
的 Screaming Architecture 要求顶层优先表达系统用途而不是框架。
[Pydantic AI Harness](https://github.com/pydantic/pydantic-ai-harness/blob/main/AGENTS.md) 自身也以一个
released capability 一个顶层子包组织可选能力。这些证据支持组件优先，但不能替代本项目已有的工具权限、
状态所有权和物理验收决定。

## 最后决定

当前产品继续使用一个 Python 发布物中的扁平组件图：

```text
src/abaqus_sim_agent/
├── coding/          # 单 Coder 产品能力和宿主 run 编排
├── execution/       # Abaqus vendor process 副作用边界
├── verification/    # 模型不可见的宿主验收
├── retrieval/       # Concept 文档检索
├── api_catalog/     # Exact Abaqus Python API knowledge
├── evaluation/      # Gold、变更 Case 与重复 gate
└── skills/          # 随包领域资源
```

并固定以下组件语义和依赖不变量：

- `coding` 拥有 strict request/decision/outcome、Agent/Harness composition、run-scoped dependencies、
  clarification、history、budget、workspace snapshot 和最终 outcome 编排。
- 模型可见的 runtime、Catalog 和 Concept tool adapter 属于 `coding`；它们只能把经过合同校验的请求委托给
  下游组件，不能自己形成外部系统真值。
- `execution` 独占 Abaqus launcher、Windows process lifecycle、raw stdout/stderr、exit/timeout 和
  artifact delta。它不依赖 Agent、retrieval、Catalog、verifier 或 evaluator，也不判断物理成功。
- `verification` 独占模型不可见的 oracle、Case-specific INP/ODB contract 和最终 verification report。
  它不能被注册为模型工具，也不修改 Agent workspace。
- `retrieval` 拥有文档加载、Dense/BM25索引、RRF、exact metadata优先拼接 query 与 `var/rag`；`api_catalog` 拥有 immutable cards、source
  conflict、exact method/parameter query 与 verified runtime notes。两者保持独立语料、查询、预算、trace
  和组件局部评测，不合并为通用 `knowledge` 包。
- `evaluation` 可以组合所有产品组件并拥有 task、trial、repeat、gate、verified seed provenance 和报告；
  产品运行模块不得反向依赖 `evaluation` 或 `benchmarks`。
- `skills` 只承载随包静态领域指导，不成为 API 真值库、hidden reference 或自动累积的跨 run memory。

`CodingRunService` 所需 verifier Protocol 和通用 attempt contract 由消费方 `coding` 拥有；concrete
`verification` 单向实现该 port，CLI 或 evaluator composition 注入具体 verifier。确切文件名可以随实现
演进，但不得继续要求可复用的 `coding.service` 依赖 concrete verifier package 才能取得抽象。

组件内部按真实变化原因拆 `agent`、`service`、`contracts`、model-facing tooling、diagnostic/convergence
等职责；不创建全局 `agents`、`tools`、`models`、`schemas`、`services`、`common` 或 `utils` 分桶。是否
使用 `tooling/`、`convergence/` 等内部子包属于实现细节，不由本 ADR 固定。

包根 facade 保持窄且有意：产品新代码优先依赖明确的 contract 或精确模块，不从根包重导出全部 Case、
evaluator 和历史类型。删除已有 export 属于兼容变化，实施前必须核对文档、测试和外部消费者。

旧 `agents`、`application`、`domain`、`inference` 以及 legacy executor 继续明确为历史研究资产，不进入
当前组件图。是否迁入 `research/legacy` 或删除必须作为独立兼容性工作处理；本决定不通过搬目录恢复
LangGraph、多 Agent、StructuredModelPort 或持久控制平面。

只有真实调用证据出现后才增加新的顶层能力或基础设施。第二入口、第二种 runner、多 Agent、数据库、
durable checkpoint、remote worker 或通用 model-change plugin 都需要各自的问题证据、验收和必要 ADR，
不能由目录设计提前推出。

## 为什么这样选

当前组件图已经对应真实调用链和最重要的可信边界：模型做决策，typed tools 请求副作用，runner 执行，
host verifier 裁决，evaluator 测量。保留这些同级组件比重新套一层通用 Clean Architecture 目录更能让代码
结构直接说明项目做什么，也避免把三个 runtime tools、Concept retrieval 和 host oracle 混成一个全局
`tools` 包。

组件内部仍可以分层，因此该选择不排斥 typed contract、依赖反转或 adapter；它只把这些技术角色放回拥有
语义的组件内。这样既能解决 `coding/agent.py`、verifier port 和宽 facade 的实际维护压力，又不需要大规模
迁移现有 Case、测试和 import。

## 没有采用的方案

- 全局 package-by-layer：`agents/tools/models/services/repositories` 会让一次 CodingRun 变更横跨所有目录，
  并模糊 tool 权限、process 副作用和 host verification 的不同所有者。
- 增加 `capabilities/knowledge/runtime/quality` 总括父层：当前每类只有少量组件，额外层级不能简化依赖；
  `capability` 在 Pydantic AI/Harness 中还有特定模型能力含义。
- 把 retrieval、Catalog、execution 和 verification 全部并入 `coding`：会失去独立索引生命周期、精确知识
  语义、唯一 launcher 和模型不可见 oracle 边界。
- 将每个组件拆成独立服务或包：当前是本地同步 CLI，没有部署、并发、恢复或团队边界证明这种成本合理。
- 继续保持目录但不规定依赖方向：Python 目录本身不提供 package-private，边界会继续被宽 re-export 和跨包
  私有 import 侵蚀。

## 带来的影响

- 有利：一级目录直接表达当前产品能力、知识、外部副作用、可信验收和质量系统。
- 有利：Concept retrieval、Exact Catalog、typed runtime tools 与 host verifier 的权限和状态所有权保持清楚。
- 有利：内部重构可以沿组件渐进进行，不要求恢复旧控制面或一次搬动所有测试和 Case。
- 代价：一级包故意混合“产品能力、支撑组件和强边界”，不是一棵分类完全对称的目录树；维护者必须阅读
  依赖规则而不能只凭目录名推断层级。
- 代价：Python 不能靠目录强制私有性，需要窄 facade、精确 import、评审和必要的静态依赖测试。
- 风险：以后新增组件或再次扩大 facade 时，仍需靠 import 测试和评审维持方向；Python 目录不会自动阻断
  私有模块依赖。
- 风险：本轮按维护者明确选择移除了旧内部 import，没有 deprecation 期；仓库外若存在未声明调用者，需要
  直接迁移到新 owner。

## 落实与确认

- 已有实现：七个活动一级组件已经存在；`coding.cli` 是唯一安装入口；`retrieval` 与 `api_catalog` 不依赖
  `coding`，产品运行路径不依赖 `evaluation`；`execution.abaqus2024` 不依赖 Agent 或 verifier。
- `CodingAgentDependencies` 与 `RuntimeBudgetExceeded` 已迁入 `coding.dependencies`，使
  `coding.service` 不再依赖 Agent composition；`CodingRunVerifier` 与 `VerificationAttempt` 已迁入
  `coding.verification_port`，concrete verifier 单向实现该 port；`verification.base` 已删除。
- `coding.agent` 已收敛为 Agent/Harness composition；初始指令迁入 `coding.instructions`，三个 typed runtime
  tools 与 runtime feedback 迁入 `coding.runtime_tools`，Exact API lookup/automatic hint 迁入
  `coding.api_lookup`，Concept tool 注册与 payload/trace 由 `coding.concept_lookup` 独占。原 Agent 模块从
  超过 2,000 行缩减到约 250 行。
- `execution`、`retrieval`、`api_catalog`、`verification`、`evaluation` facade 已收窄；组件局部 evaluator、
  具体 Case verifier、oracle 和 legacy executor 不再从组件根导出。旧 `coding.agent` mode/policy/dependency
  symbols 也不再转发，全部仓库调用者已迁到实际 owner。
- 接受证据：维护者于 2026-08-29 明确认可该组件与包设计。
- 验证：全仓 Ruff check/format、全仓 BasedPyright、393 个非持证 pytest 均通过；26 个 `abaqus` marker
  测试未运行。新增 facade/owner 测试明确断言旧 symbols 和旧 verifier module 不再提供。
- 本 ADR 没有创建实施计划；本轮只完成已确认的包职责重构，没有改变 vendor execution、物理合同或默认
  retrieval/preflight 模式。

## 与现有决定的关系

- 扩展 [ADR-0008](0008-pydantic-ai-harness-abaqus-coding-run.md) 的单 Coder、CLI composition 和宿主所有权。
- 保持 [ADR-0009](0009-exact-abaqus-api-catalog.md) 与
  [ADR-0010](0010-budgeted-official-concept-retrieval.md) 的两种知识组件边界。
- 保持 [ADR-0011](0011-verified-seed-model-change.md)、[ADR-0012](0012-per-case-model-change-suite.md) 和
  [ADR-0013](0013-host-owned-model-change-acceptance.md) 的共享 evidence / Case-specific verifier 原则。
- 保持 [ADR-0015](0015-disable-general-shell-for-product-coder.md) 的 no-Shell 与 typed runtime 权限边界。
- 保持 [ADR-0018](0018-enforce-verified-abaqus-aci.md) 的显式分级 ACI；包重构不改变其默认模式或证据门槛。
- 不恢复已由 ADR-0008 替代的 ADR-0002、ADR-0003、ADR-0006 或 ADR-0007 首轮路线。

## 相关文档

- [项目架构](../architecture/overview.md)
- [当前系统框架](../architecture/system-framework.md)
- [首轮 Agent 与实现范围](../architecture/agent-implementation-scope.md)
