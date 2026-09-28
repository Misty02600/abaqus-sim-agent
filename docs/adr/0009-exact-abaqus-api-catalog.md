# ADR-0009：使用官方文档、abqpy AST 与运行证据构建精确 Abaqus API Catalog

## 状态

已采纳

API Catalog 的工具范围、来源与 trace 合同继续有效；普通产品的独立调用次数预算由
[ADR-0031](0031-remove-product-tool-call-count-limits.md) 部分替代，普通产品默认启用策略由
[ADR-0040](0040-require-api-catalog-for-product-runs.md) 更新为必须绑定 Catalog、默认 `automatic_verified_only`。
下文保留原决定的背景与当时配置。

2026-09-27 普通产品改为自动读取包内 Catalog，目录配置和外置检查的当前语义见
[Catalog 安装与升级](../architecture/knowledge-access.md#catalog-安装与升级)；下文保留当时决定。

## 日期

2026-08-23

## 当时遇到了什么

首轮 no-RAG CodingRun 已经得到真实基线。启用 SDK retry 后，一项 OpenCode Go 运行进入 Abaqus/CAE，
但 Coder 在 30 次模型请求和 9 次 Abaqus 调用中反复猜测 `findAt`、`seedEdgeByNumber` 和 `Set` 的动态
对象语义，最终没有生成 INP。Jedi 只有在显式得到 `Part` 类型时才能稳定补全；BasedPyright 与 abqpy
对这次真实 GeomSequence 外层多包 tuple 的错误返回 0 diagnostics。因此静态分析不能替代 API 知识和
真实 runtime。

当前 Chroma 索引适合建模概念以及 Keyword、Element、Output 的精确符号解析，不适合直接摄取
Scripting Reference。该参考共有 1,253 个有效页面，方法和 constructor 是带 required/optional、return、
exception 的原子 article；通用标题/字符切分会拆散它们，并把大量 API 页面无意义地向量化。

## 最后决定

新增项目自有、非向量的 Exact API Catalog：

- `ApiMethodCard` 是内部事实单位，而不是 abqpy 对象或原始 HTML chunk。
- 官方 Scripting Reference 提供摘要、Access/Path、参数说明、return、exception 和可引用 anchor。
- 固定 abqpy wheel 只通过 AST 读取 class graph、继承 receiver、overload、参数、默认值和 annotation；绝不
  import/execute，也不安装进 Coder 或 Abaqus Python 环境。
- 经 Abaqus 2024 实机验证的动态行为作为独立 runtime note；优先级高于文档和 annotation。
- source-specific record 和冲突都保留，不把多来源静默压成一个不可追溯字符串。
- Catalog 写成不可变 `methods.jsonl + manifest.json`，manifest 固化 cards、官方文档树、abqpy wheel 和
  runtime notes 的 SHA-256。
- 查询按 Python 大小写敏感的 qualified/public alias 精确解析；bare method 多义时返回 `ambiguous`，近似
  名只作为 suggestion。

当前本地 2026 Scripting Reference 可直接服务于 2024 基础 API。release 仍进入 provenance 和
`release_alignment`，但不因基础版本差异阻塞 lookup；高级能力另行验证。

Catalog 以可选 capability 接入现有单 Coder：

- 默认不传 `--api-catalog-dir`，继续保留 ADR-0008 的 no-RAG 基线。
- `full` 模式才注册 `lookup_abaqus_api`；`automatic_verified_only` 不改变初始工具或提示词，只在精确
  运行时错误命中 verified note 时返回短提示。Catalog 位于 workspace 外且模型不能修改。
- API lookup 不占 Abaqus/solver budget，但有独立调用预算并进入 usage/eval metadata。
- CAE 失败时，宿主区分 process success 与 stage success；若 traceback 能提取 method 名，自动附加精确
  lookup 结果；同名方法仍可能返回多张候选卡。Agent 可在方法名未知或证据不足时主动查询 qualified name。
- Catalog 只提供调用知识，不能补造或覆盖用户物理输入，也不能宣告脚本或仿真成功。

## 为什么这样选

纯 2-Step RAG 无法提前知道代码修复中才出现的方法；完全依赖模型主动查询又可能漏查或重复查询精确
identifier。当前采用事件驱动的 hybrid exact retrieval：宿主处理 traceback 中可提取的方法名，模型可
主动查询 qualified API 和处理剩余歧义。作出本决定时 Concept Retriever 尚未接入，因此当时的 B1 不是
完整 Hybrid RAG；后续显式 Concept 工具见 [ADR-0010](0010-budgeted-official-concept-retrieval.md)。

Exact Catalog 不依赖当前局部代码能否推断 receiver type，因此比 Jedi/BasedPyright 更适合 Abaqus 的
Repository、SWIG Sequence 和 runtime-created object；但它也不是证明器，最终行为仍由真实 Abaqus 2024
和宿主 verifier 决定。

## 没有采用的方案

- 把 `SIMACAEKERRefMap` 直接加入现有 Chroma：会拆散方法 article、扩大索引并浪费 embedding。
- 只使用官方 HTML：难以稳定恢复 mixin 继承、public receiver 和 overload。
- 只使用 abqpy：缺少完整语义和官方引用，mock/annotation 也不等于 runtime。
- import/inspect abqpy：其模块含可执行 bootstrap，可能启动 Abaqus，且最终 `*args/**kwargs` 实现会丢失
  overload。
- 把 Jedi、python-lsp-server 或 BasedPyright 设为执行 gate：真实失败已证明动态链存在漏报；LSP 只增加
  editor 协议和生命周期，不改善分析引擎。
- 新增 Agent、LangGraph、reranker、GraphRAG 或向量服务：当前 exact lookup 不需要这些能力。

## 带来的影响

- 有利：把数千 API 的维护交给可重复构建流程，不再无限扩张 Skill。
- 有利：官方语义、abqpy 类型和 runtime note 可以分别评测、追溯和比较冲突。
- 有利：同一 no-RAG/B1 消融能测量 authoring 首次成功率、probe 次数、Abaqus 调用、token 和最终 gate。
- 代价：增加一个离线管理产物和专用 parser；Catalog 构建需要本机官方文档与固定 abqpy wheel。
- 限制：静态 signature 和卡片不能覆盖所有动态对象行为，0 diagnostics 或 found card 都不代表脚本正确。

## 落实与确认

- 实现：`src/abaqus_sim_agent/api_catalog/`。
- 项目 runtime note：`benchmarks/api_catalog/abaqus_2024_runtime_notes.jsonl`。
- 固定 lookup benchmark：`benchmarks/api_catalog/abaqus_api_queries.jsonl`。
- 本机全量构建：5,481 cards、48 source conflicts；官方文档、abqpy source、runtime notes 均有 SHA-256。
- 官方 `Owner(...)` 直连构造器已与 abqpy `Owner.__init__` 合并；`ElemType.__init__` 现在直接携带官方
  `import mesh` 和 `elemCode` 说明。`FieldOutputRequest import step`、SectionAssignment 的 Set 参数和
  ElemType import 不再重复维护为经验，benchmark 改为检查官方 Access/参数描述；当前六条 runtime note
  只补充官方未解释清楚的 `findAt` call shape、native geometry Sequence 与具体 consumer 绑定差异。
- 固定 resolver benchmark：当前 final9 的 16 条 method/parameter、exact/ambiguous/case-sensitive case 通过，
  包括继承解析后的 `Part.seedEdgeByNumber`、`Assembly.Set`、`Model.DisplacementBC`，带 verified note 的
  `Part.Set`、`Assembly.Set`、`Part.setMeshControls`，以及使用官方 Access/参数断言的
  `Part.SectionAssignment`、`Model.FieldOutputRequest` 和 `ElemType.__init__`。
- 2026-08-23 使用同一 OpenCode Go 模型、Prompt v2、SDK retry 和预算分别顺序运行 B0/B1 三次；两组均为
  0/3，六次都以 `model_budget` 终止。B0 三次都进入 Solver，B1 三次都停在 CAE authoring，因此本次
  smoke 未观察到任务收益。
- B1 的策略已从每次用满 12 次 lookup 收紧为按需、去重、最多 4 次，并即时记录 query/status/card/payload；
  新实验每次计费 3–4 次，input token 总量与 B0 接近，但仍未改善闭环。该小样本不能证明 Catalog 普遍
  无效或稳定负收益，足以支持它继续保持显式可选、默认关闭。
- 该 smoke 之后又补上跨 query 的同 card-id 去重，避免 `Model.FieldOutputRequest` 与裸
  `FieldOutputRequest` 重复返回同一张大卡；同时把 runtime note 改为自包含最小调用，并移除重复的可选
  参数长描述。代表性 payload 从约 12.3/7.5/3.4 KB 降为 3.5/3.2/1.9 KB；这些修正随后进入 Prompt v3
  消融，不单独产生收益声明。
- 随后的 Prompt v3 同代码消融先把 B0 稳定为 3/3，再运行 B1；B1 为 1/3。receiver-qualified 自动查询
  已能返回 `Part.setElementType`、`Assembly.Set`、`Part.setMeshControls` 和
  `Part.seedEdgeByNumber`，但唯一通过的 B1 run 没有调用 Catalog。最新结果仍不支持 Catalog 任务收益，
  同时正式确认 B0 单 Coder 闭环已经通过 smoke gate。
- 对 B1 history 的后验审计发现，六次精确查询虽然全部 `found`，实际投递的 runtime note 均为零：
  `Part.seedEdgeByNumber` 的英文 error fingerprint 没有匹配本机 CP936 中文错误，`Assembly.Set` 又没有
  自己的 consumer note。因此该实验有效否定了“当时的端到端 Catalog 变体已有收益”，但没有真正测到
  verified runtime note 的效果。错误指纹随后改为软排序，lookup trace 现在固化 `delivered_note_ids`。
- 新增 `automatic_verified_only` 后，自动路径仅在 receiver-qualified API 存在 verified note 时注入紧凑
  guidance；无 note 不改变模型上下文。`Assembly.Set`、`Part.seedEdgeByNumber`、
  `Part.setMeshControls` 的容器规则已由仓库内 `pytest -m abaqus` 微回归验证；final9 全量 Catalog 为 5,481
  cards、48 conflicts；增加参数级 resolver case 后 fixed benchmark 为 16/16。
- 为避免只验证 `findAt` 记忆题，新增 6 类冻结 API repair benchmark，DEV/HOLDOUT 各 3 类。HOLDOUT 不
  增加 runtime note，覆盖 `Model.FieldOutputRequest` access/import、`Part.SectionAssignment` region 和
  `FaceArray.findAt` 多点调用形状。首轮 Skill-only、full、automatic-verified-only 分别为 5/6、6/6、
  6/6。三个 holdout 交错三轮后，Skill-only 为 8/9，full 为 9/9；full 每次仅查询一张精确 Card，投递
  runtime note 数为零，模型请求、输入 token、工具调用总量分别少 11.4%、9.6%、13.6%。
- 这组结果首次观察到官方/abqpy Catalog 相对当前 Skill 的任务增量，但只覆盖三个 API 家族和每臂三次；
  正常成功轨迹中 Card 仍会增加约 1.7–3.0 KB 上下文。产品含义是优先把 full Catalog 用于已有脚本修复/
  调试，把 automatic 模式用于稀疏已知兼容问题，而不是宣称所有从零建模任务都应默认启用。固定结果见
  `benchmarks/coding/api_repair_evaluation_20260823.json`。
- 随后将相同修复带回从零建板 Prompt v4，三组共同关闭 Planning 工具并保持其余合同/预算一致。
  Skill-only、full、automatic-verified-only 分别为 2/3、2/3、3/3。automatic 实际投递 3 条 note，相对
  Skill 少 23.9% 模型请求、35.0% 输入 token、25.0% 工具调用；full 前两次通过为 17/7、18/6，但第三次
  在 3 次 Solver 失败后耗尽模型预算。该结果推翻“从零建模不需要 Catalog”的过强说法，同时说明 full
  Card 不是万能默认：当前低干扰 automatic 是更合理的从零候选，仍需不同建模 Gold 验证。固定结果见
  `benchmarks/coding/plate_tension_catalog_evaluation_20260823_v4.json`。
- shared model-change runtime 后续接受同一显式 Catalog mode/binding。冻结 `load_mode` request、verified
  source、Skill、模型、Abaqus/Solver 预算和 host verifier 后，corrected off control 为 0/3，full Catalog
  treatment 为 2/3。两条通过轨迹各主动命中 `Model.SurfaceTraction` 与 `Assembly.Surface`，分别只用
  16 次模型请求、3/4 次 Abaqus 调用和 1 次 Solver；第三条仍以 36/10/0 耗尽，说明精确卡片能减少该题的
  API-binding 长尾，但不能替代对象归属和载荷语义推理。固定证据见
  `benchmarks/coding/plate_model_change_treatments_20260825.json`。
- `Model.SurfaceTraction.region` 的重复 gap 通过独立 licensed 微回归人工晋升：普通
  `Assembly.Set(edges=...)` 被 2024 consumer 拒绝，side-aware `Assembly.Surface(side1Edges=...)` 创建
  load 成功。final9 cards SHA-256 为
  `3e3294808dc180df2d13ce5183095ebb6ecc47998f6313c96398af25c564255b`，runtime notes SHA-256 为
  `935a92973203fb1cfee4f828727b1d75126ebee3237cd8030d6a9f57487ac881`。同一 typed-v2 `48/18/3`
  Product batch 中 note 投递与 Assembly.Surface 采用均为 3/3，最终 2/3 gate 与 final8 control 相同；唯一
  非通过样本在越过 region gap 后遇到 directionVector 错误和 tool infrastructure，因此只确认该 scoped
  binding 的行为采用，不确认稳定成功率提升。
- resolver 后续在不重建 final9 的前提下增加 `Method.parameter` 精确查询。full/short method prefix 后可跟
  一个大小写敏感参数名；返回只包含该参数的签名变体、官方描述、import/release，以及空 scope 或与参数
  同 scope 的 runtime note。方法卡和 `(card_id, parameter_name)` 分别去重，避免已返回完整方法卡后阻断
  optional 参数详情。确定性测试覆盖 alias 去重和 directionVector/region note 隔离。
- 相同 source、Skill、OpenCode Go `deepseek-v4-flash`、typed-v2、final9、`48/18/3` 的 Product batch 仍为
  2/3。`Model.SurfaceTraction.directionVector` 参数查询、两点向量正式采用均为 3/3，directionVector
  runtime binding error 为 0/3；一条还用 `Model.SurfaceTraction.region` 精确获得 scoped note。剩余失败
  首先遗漏 `follower=OFF`，随后因聚合 verifier 反馈误判为 INP 编码问题，并暴露通用 `run_command` 可借
  Python subprocess 间接启动 Abaqus 的执行边界缺口，最终以 48 次模型请求耗尽。该批只确认结构化参数
  查询进入真实轨迹，不确认稳定成功率提升，也不支持继续增加 SurfaceTraction 专用语义。
- 2026-08-28 对 162 份 history、745 次 typed runtime call 做了面向失败成本的复审，并用三个独立
  licensed fixture 验证 Region/Set/GeomSequence、ODB Field/Value/NodeArray、Surface SIDE1/SIDE2 和
  BeamSection/orientation 边界。规则只在 2024 实机通过后人工加入 runtime notes，不从 Agent probe 自动
  写回。
- 本轮首次推荐的新实验 artifact 为 final13：5,480 cards、48 conflicts，cards SHA-256 为
  `f01e90a53107691c1e5abf82f219b1607a5294fb07e1ff069302a8ff284a2d87`；16 条 runtime notes 的源文件
  SHA-256 为 `2805dfaa0970e50d4312be1a78346995ad4c08e7315a64561d0d37b03b76b2c7`，固定 benchmark 为
  27/27。卡片减少一张来自 `Part.generateMesh` sibling mixin public overload 合并；`Spot.__init__`
  ambiguity control 未被误合并。
- final11 候选中的 SectionAssignment、DisplacementBC 和 ConcentratedForce notes 在 Product 对照后从
  final12/final13 移除：SectionAssignment automatic 三次共 6 次 CAE，off 为 4 次；后两条没有 Product
  treatment 且与 Skill 的 assembly-owned Region 正常路径重复。该裁剪确认 runtime evidence 是准入条件，
  不是永久保留条件。
- API repair 的三次对照中，旧 `Assembly.Set` note 将 1/3、14 次 CAE 改善为 3/3、6 次；新
  `Assembly.Surface.side1Edges` note 是 mixed positive（1/3、11 次到 3/3、9 次，实际投递 2/3）；
  `setElementType` 与 BeamSection 没有观察到调用节省。完整证据固定在
  `benchmarks/coding/api_repair_final13_evaluation_20260828.json`，因此不得把“verified”解释成必然减少
  调用或必然收敛。
- 两条 note 已帮助完成 API 修复的轨迹又暴露了 output contract 混淆：模型以自然语言结束后，把
  Pydantic 的 `Invalid JSON` retry 当成工作区 result JSON 错误，继续做 BOM/换行/原子写入探针。
  `decision-output-tool-clarification-v1` 现在显式说明该错误只指向最终 `CodingRunDecision`；两个单次
  follow-up 都以 2 次 CAE 通过。该结果只确认已复现路径的修复，不构成稳定率估计。
- final13 随后进入冻结 B31 from-scratch Gold 的三重复，automatic verified-only 为1/3、未过gate。两条
  failure都是文件前`ModelAPIError: Request timed out`；唯一task-started attempt以24次模型请求、8次
  Abaqus和1次Solver通过完整oracle。该轨迹实际投递
  `runtime:2024:FieldOutput.getSubset:field-value-boundaries`，证明note进入真实任务并最终采用；但模型只修复
  `u_field.position`，遗漏`sf_field.position`后又消耗一次Abaqus Python调用，不能声称调用节省。冻结证据
  位于`benchmarks/coding/beam_tension_b31_final13_evaluation_20260828.json`。
- 同一成功轨迹还在首个CAE调用复现`Model.BeamSection(beamOrientation=...)` keyword错误。独立licensed
  fixture随后验证普通BeamSection构造成功，而`orientation`、`beamOrientation`、`geometricType`均被
  Abaqus 2024拒绝。当前推荐final15不增加note id，只把`beamOrientation`加入既有BeamSection note scope；
  共5,480 cards、48 conflicts、16条notes、benchmark 27/27，cards SHA-256为
  `2dcf85685a011e149ae00828fd4988985a400fd1fde76c0b24b4964ac6f7f5c7`。证据被拆到
  `abaqus_2024_beam_section_orientation_observations.json`，没有修改final13的consumer evidence。
- `same-api-occurrence-scan-v1`要求在确证attribute不存在或keyword非法后，下一次runtime前检查目标脚本中
  同一owner/attribute或callable/keyword的全部出现位置并作相同窄修复；它来自上述真实遗漏，但目前只有
  prompt与单测证据，没有Product收益声明。
- final15与该收敛规则的同题B31 treatment为0/3。唯一task-started attempt在31次模型请求、12次Abaqus、
  0 Solver后耗尽；BeamSection与beam-orientation notes修复局部错误，但轨迹转入独立实例、mesh ownership、
  Assembly Set和probe/check脚本循环。另两条为文件前HTTP500和timeout。该负结果确认final15不能替代历史
  full-final9/no-Shell v6，也没有观察到`same-api-occurrence-scan-v1`收益。
- treatment进程启动时的ODB behavior regex允许可选`Odb`前缀，因此把CAE`MeshNodeArray.findAt`越界投递
  `openOdb` note。当前实现只匹配精确`OdbMeshNodeArray`，plain MeshNodeArray负向测试和原ODB正向测试均
  通过；冻结treatment仍标记pre-fix，不能重写为post-fix Product证据。
- 后续没有继续扩充Card，而是把已知Gold的运行文件权限收回宿主：strict`RuntimeScriptPolicy`只允许
  `build_model.py`、`beam_tension.inp`、`extract_results.py`进入对应vendor runtime。v7正式2/3，两个pass
  分别9/3/1与11/4/1次模型/Abaqus/Solver并通过oracle；一条31/12/0生产修复仍失败。三条均无policy denial，
  因此Product证据确认正常路径与无diagnostic漂移，拒绝路径由单测确认。该组合profile成为当前B31配置，
  但不把相邻0/3→2/3归因于Catalog或allowlist单项。
- 2026-08-29 的分级Abaqus ACI是独立opt-in能力，不改变final15 cards/hash，也不把当前48条`conflicts`
  解释成完整类型仲裁。现有conflict builder只比较官方与abqpy参数名集合，不能处理required/default、
  容器类型、alias或自然语言冲突；因此只有带licensed/冻结evidence id的诊断可hard block，Catalog/abqpy
  未确认项只warning。设计与Product边界见[ADR-0018](0018-enforce-verified-abaqus-aci.md)。

## 与现有决定的关系

- 扩展 [ADR-0008](0008-pydantic-ai-harness-abaqus-coding-run.md) 在 no-RAG 基线之后的可选实验能力；不
  替代单 Coder、动态 Abaqus Python、三个 runtime tools 或 host verifier。
- 继续遵循 [ADR-0004](0004-evidence-first-physics-verification.md) 的任务级消融和物理 gate。
- 继续遵循 [ADR-0005](0005-traceable-upstream-reuse.md) 的来源、许可证与可追溯复用要求。

## 相关文档

- [项目架构](../architecture/overview.md)
- [当前系统框架](../architecture/system-framework.md)
- [技术栈与工程讲解点](../architecture/technology-stack.md)
