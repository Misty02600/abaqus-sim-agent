# PLAN-0004：校准评测有效性并建立分阶段验证路径

| 状态 | 完成时间 |
|---|---|
| 已完成：阶段 A 测量校准；B/C 外部批次未执行 | 2026-09-12 |

## 问题与最终结果

原文档行为批次把任务结果、强流程判据、输出截断和 Provider 故障混入单个行为失败标签。用户先要求设计并自审测评路径，随后授权“按你说的实施”；落实范围为零外部调用的阶段 A，不是启动后续模型或持证批次。

已实现版本化的 strict 分栏合同、来源绑定的离线复核、显式 calibrated 配置、发送前 HTTP 尝试记录和下一批次的离线规格封存。旧 profile 默认仍是 legacy，保留 2400 上限；新 calibrated profile 不额外添加单响应上限。产品 CLI、物理 oracle、旧 Gold 合同、文档策略默认 off 和历史批次结果均未改变。

沿用 repo-docs 的决定与证据边界：[ADR-0008](../../adr/0008-pydantic-ai-harness-abaqus-coding-run.md) 的单 Coder 和受控运行、[ADR-0019](../../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md) 的 execution_evidence/task_oracle 分离、[ADR-0022](../../adr/0022-evaluate-bm25-dense-rrf-before-agent-adoption.md) 的检索与采用证据边界。没有新增编排器、数据库或第二套产品状态机。

## 已落实的测量边界

- task_acceptance、必要约束、protocol、termination_reason 分栏，旧 first-tool 规则仍可明确判失败；协议失败不自动覆盖任务结果。
- 正确澄清或正确停止只能通过本来允许它的题目，不能据此通过要求完整仿真交付的题目；不从 host completed/failed 直接推导领域成功。
- 先发生协议错误再被截断时，保留协议 fail 与后续语义 unknown。原始响应 finish_reason=length 用于确认截断，不靠 InfrastructureError 名称猜测。
- 先读已有文件、写不依赖缺参的输入检查说明，再询问厚度，可通过新语义；猜厚度、开展依赖缺参或用户禁止的工作仍失败。依赖关系须由宿主语义审查；厚度决策必须能在实际工具轨迹中找到，first-tool 顺序也与轨迹核对。
- 全部已启动尝试进入完成率分母，未启动槽位保留，未知值不填零，失败成本不丢弃。条件子集分数仅用于诊断，不能摘取其中的 4/4 宣传能力。
- 分栏检查的 pass/fail 需要来源引用；离线入口验证文件 hash、JSON pointer、病例覆盖、原报告 observation 一致性及复核前后字节不变。
- 原始 HTTP 未返回时 usage 不可得，已收到响应的 tokens 只是可观察部分，不代表完整账单。新 calibrated 模式增加发送前请求体记录，不记录 Authorization、请求头或 URL query。
- 固定 Gold 的网格、文件名、修改范围、extractor 字节和物理容差不放宽。合法替代解只在既有允许合同内接受；开放解法需要另立 Case/版本。

## 关键实现

| 位置 | 职责 |
|---|---|
| [behavior_measurement.py](../../../src/abaqus_sim_agent/evaluation/behavior_measurement.py) | strict 判据/检查/来源/成本/逐trial合同、分栏聚合、完整分母、澄清双规则 |
| [document_behavior_audit.py](../../../src/abaqus_sim_agent/evaluation/document_behavior_audit.py) | 已落盘 legacy 文档批次的离线复核，显式宿主语义标注，不调用模型 |
| [document_behavior_profile.py](../../../src/abaqus_sim_agent/evaluation/document_behavior_profile.py) | legacy/calibrated 独立配置，不共享可变 settings |
| [document_policy_behavior.py](../../../src/abaqus_sim_agent/evaluation/document_policy_behavior.py) | 显式 --measurement-profile；calibrated 请求体前置记录、独立 run ID、分栏评分说明；原默认不变 |
| [document_behavior_spec.py](../../../src/abaqus_sim_agent/evaluation/document_behavior_spec.py) | 只读核对源字节并封存24个未来trial槽位、预算和实现哈希；execution_authorized=false |
| [宿主复核标注](../../../benchmarks/coding/document_behavior_batch2_annotations_v2.json) | 全八场景的 post-hoc 判断、来源批次绑定及厚度依赖事实 |
| [阶段 A 验证记录](../../../benchmarks/coding/behavior_measurement_phase_a_20260912.json) | 测试、文件不变核对、最终报告/规格哈希与声明限制 |

## 八条旧轨迹的复核结果

| 场景 | 新语义观察 | 协议/测量限制 |
|---|---|---|
| local-evidence | 交付正确 | 无观察到的协议错误 |
| direct-support | 交付与引用正确 | 首次 assessment envelope 非法，后来修复；零错误协议分为fail |
| partial-followup | 任务 unknown | awaiting-assessment 时尝试follow-up被拒；随后2400截断 |
| unresolved-stop | 任务 unknown | 首请求超时，未观察到受控证据不足分支 |
| missing-release | 保留2024缺口并正确停止 | 首次 proposed_need 的 host origin 字段被拒后修复 |
| missing-physical-input | 新语义下正确澄清 | 先 list/find/部分分析再询问，旧 first-tool 仍fail；host无答复故failed |
| technical-failure-independent-work | 任务 unknown | 首响应2400截断，未调用文档服务，不能判断故障后的独立工作 |
| citation-duplicate-resume | 引用修正 unknown | same-run恢复与缓存命中已观察；随后2400截断，不能把缓存通过当引用通过 |

汇总为4条交付/正确澄清/停止、4条语义unknown；终止原因为4正常、3输出截断、1Provider。所有8次尝试保留，30次模型请求、29个HTTP响应、4次受控查询、0次持证运行。三次length响应均为completion_tokens=2400。

原报告的3 pass / 4 failed_model_behavior / 1 invalid_provider及failed gate不覆盖、不回填。新列是事后语义复核，不是新trial、通过率改善或默认启用依据。源HTTP字节在本次复核前后绑定，不追溯声称旧批次最初已完整封存它们。

## 验证结果

| 验收项 | 实际证据 |
|---|---|
| 历史/产品不变 | 修改前捕获的557个旧批次、既有benchmark及产品coding/verification/execution文件，收尾0个hash变化 |
| strict与正反例 | 未知字段/隐式类型转换、缺检查、无来源pass、重复trial、越界路径、错误pointer、源字节漂移、伪称已澄清均拒绝 |
| 终态/分母/成本 | failed-host正确澄清、完整交付题拒绝单纯澄清、先违规后截断、手工patch、未启动槽位、无应答tokens未知 |
| 配置真实HTTP路径 | MockTransport经过实际Go模型适配器，legacy与calibrated请求体仅输出cap不同；messages/tools/thinking相同；0外部请求 |
| 无响应尝试记录 | 注入ReadTimeout，发送前记录仍存在，测试凭据/请求头/query不进入记录 |
| 物理与守卫回归 | 聚焦154项通过，包含原plate合法单元选择、stale结果、假JSON、错误mesh/physics、B31合同、证据引用与恢复 |
| 全量离线回归 | pytest -q -m 'not abaqus'：1126 passed / 27 deselected；最后新增的轨迹事实反伪造检查由最终35项聚焦集验证通过 |
| 格式检查 | 7个改动Python文件的ruff check与format --check通过；git diff --check通过 |
| 离线复核可重复 | 最终报告与独立重复生成报告SHA-256一致：fbf818dba9829eb90e4e2e92c50191cbefe2d9e4f7fb0902e93a5571346768be |
| 下一批次已形成规格 | 24槽位、288次模型请求上限、trial wall合计8640秒、0Abaqus；最终spec SHA-256：7dea367f49fddd028110d1ec19338280c4c720404225724d74a15de809d12a87 |

本轮没有调用真实模型、真实检索排名或licensed Abaqus，没有提交Git。源代码与测试路径可复现实现；大体积报告位于本地 var/audits/behavior-measurement-v2，以上表格与版本化阶段记录提供不依赖该目录才能理解的结果摘要。中间生成物保留，引用最终 *-final.json，不能混用早期spec。

## 自审结论与仍然存在的限制

1. 修正判据不能用来掩盖失败：保留旧gate、所有失败尝试及已有违规；unknown不是pass。
2. 固定段落回放只测收到证据后的使用行为；第二次自动返回angles不证明模型能检索到它。
3. 语义判断仍由宿主审查提供，聚合是确定性的；不是自动理解任意自然语言的评分器，也不是独立专家复核。原文精确引用本身不能证明语义适用。
4. 当前 off/on 会改变检索路径，不能直接归因为强制回评收益。选择组合比较，或先实现并验证同后端adapter，再作单因素协议对照。
5. 省略max_tokens不是无限输出；仍受native、请求数、总时间预算约束。thinking disabled是本次保留的显式实验设置，不冒充所有生产调用的默认。
6. 源文件/源码hash是内容绑定，不是OS沙箱或旧HTTP字节的追溯真实性证明；不能据这次工程校准声称物理能力提高。
7. 阶段B的24次、阶段C的18次均只是建议规模；小样本不证明稳定成功率、统计等价或任意CAE能力。

## 后续测评路径：均未执行

### B：受控文档行为

先审批已封存spec中的模型、24次trial与成本；三个独立output root各执行八场景，不替换失败样本。预算沿用12请求/2查询/360秒，只有声明的中断允许same-run恢复；不同时改Prompt、Skill、后端、索引或守卫。执行前重新核对全部输入/实现/source哈希；任何改动需新seal。当前不能仅凭spec文件执行权限变更。

现有离线audit入口专门读取旧profile；新批次仍需按冻结rubric提供宿主逐项语义判断，再用分栏合同聚合，不能把旧host status/count_matches作为自动语义grader。没有观察到关键分支或发现评分器缺陷时，先修测量，不通过反复加样本凑门槛。

### C：真实链路与持证对照

另行审批。先核对真实检索链路、完整语料/向量/encoder绑定，不用它调参。然后明确比较产品组合还是隔离协议，冻结同一模型、任务、seed、oracle、Catalog/ACI/allowlist等共同配置。

建议plate缺厚度闭环、B31截面变更、一个已有独立oracle的真实修复任务，3题×2臂×3次=18次。任务在预跑前按覆盖需要选择，按题/重复分块交替臂顺序，隔离workspace和可写缓存。报告逐题联合验收、胜平负及全尝试成本；不以成功样本调用数替代总成本，不凭协议合规率启用默认。

### D：回归池与新任务家族

现有plate/bar/beam及五个变更Case继续作为熟悉题回归池，不称held-out。按受影响边界选回归，不要求本轮全跑；只有真实扩展需要时另建家族隔离的新题，先审查正反例和容差，再调用模型。已查看轨迹的题转为development，不能继续用它调参并保留held-out名义。

## 调研依据

- [Anthropic Agent评测实践](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)：结果/轨迹分离、评分器校准与环境对齐。
- [SciCode](https://scicode-bench.github.io/)、[ScienceAgentBench](https://arxiv.org/abs/2410.05080)：科研程序、领域/数值结果和成本。
- [τ-bench](https://arxiv.org/abs/2406.12045)：终态与重复可靠性，不引入额外用户模拟Agent。
- [OpenAI编码评测审计](https://openai.com/index/separating-signal-from-noise-coding-evaluations/)、[SciCode-Verified预印本](https://arxiv.org/abs/2608.04975)：警惕隐藏实现要求、过紧容差与欠规格；不把其量化发现外推本项目。
