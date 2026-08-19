# Agent 与首个完整实现范围

## 结论

本项目首个完整实现是：**5 个专业 Agent + 1 个只在失败分流时调用的 Supervisor + 一组不冒充 Agent 的
确定性服务**。

Agent 工程链路要完整实现 typed contract、独立工具权限、子图、trace、失败语义和评测；有限元能力先限定
为 Abaqus/Standard、线弹性、小变形、单 `Static, General` step 的两个规则模型族。对超出范围的需求要
返回 `unsupported` 或进入人工澄清，不能依靠一段通用 prompt 生成“看起来像能跑”的输入文件。

这是已经确定的目标范围，不是当前完成声明。当前代码只有领域模型、Agent Protocol、State 和 Mock
Executor 骨架，六个 Agent、确定性 renderer、Abaqus adapter 与 Gold cases 都尚未实现。

## 参考 AbaqusAgent 到底实现了什么

对照基线是 `LIRAM-LIN/AbaqusAgent@f5462b3eaf34d21a958f495b80e40df45b3f39db`。README 将六个节点都
称为 Agent，但真实源码是 **4 个 LLM 角色 + 2 个确定性执行节点**：

| 参考角色 | 真实实现 | 本项目的处理 |
|---|---|---|
| Interpreter | LLM 改写需求，并检查 geometry、material、BC、load、output 五类文本项；缺失或 JSON/模型异常都可能直接结束 | 保留五类完整性和“不补造”；改成 typed spec、字段来源和可恢复 clarification，系统故障不伪装成缺字段 |
| Architect | LLM 分类 case name/domain/category/material，再做 FAISS top-k、domain 硬过滤和 name/category/material 重排；未真正输出 element、step、mesh、region 或 load plan | Architect 必须输出完整 `ModelingPlan`；检索是独立只读端口，首版不迁移 FAISS/RAG |
| Input Writer | LLM 根据一个完整参考案例生成或重写固定 `AbaqusInput.inp`，并直接覆盖文件 | 保留 `physics from spec, syntax from reference` 与引用一致性规则；Agent 输出 typed deck draft/patch，确定性 renderer、preflight 和 publisher 产生新 generation |
| Local Runner | 无 LLM；固定 job 名，`shell=True` 同步执行，按日志与 ODB 路由 | 映射为 Job Service + Executor，不称 Agent；加入 ledger、幂等、poll、cancel、timeout 和 reconcile |
| Reviewer | LLM 根据日志、当前文件、参考案例和历史给自然语言修复建议；自身不改文件 | 保留 Reviewer/Writer 分权、精确标识符和最小修复；改成 typed diagnosis、patch、evidence 和 no-progress guard |
| Visualization | 无 LLM；启动 Abaqus Viewer，读取最后 step/frame 的 `U`，输出位移 CSV 与 U/U1/U2/U3 PNG | 映射为 headless ODB Extractor + Artifact Service；增加 S/Mises/RF 证据，不称 Agent |
| Supervisor | 不存在 | 本项目新增，但只在 guard 给出多个安全失败路线时调用 |
| Verifier | 不存在；ODB 存在和位移导出成功被近似当作成功 | 本项目新增，综合需求忠实度与确定性数值证据；最终 gate 仍由 policy 聚合 |

参考案例库有 104 条记录，覆盖 7 个 domain：静力弯曲 30、振动 21、非振动动力 14、静力压缩 14、
静力拉伸 12、屈曲 10、静力扭转 3。材料实际为 metal 83、composite 11、polymer 6、concrete 4；虽然
分类配置还声明 ceramics 和 foam，当前语料没有对应案例。

这应准确理解为“LLM 可以参考 104 个 benchmark-style 案例生成和修复一个本地 `.inp`”，不能理解为
七类分析已经具备统一契约、可靠执行、数值验收和回归测试。参考仓库也没有自动化测试、durable
checkpointer、Supervisor、Verifier、HITL 或异步 Job 生命周期。

## 什么才算本项目的 Agent

一个角色同时满足以下条件，才算已经实现的 Agent：

- 有独立、必要的领域判断目标，而不是只做文件 IO、校验、路由或状态搬运；
- 使用 strict Pydantic 输入输出，不以裸字符串或 `dict[str, Any]` 作为合同；
- 有私有 per-invocation 子图上下文，不拥有父图全局 phase；
- 有显式工具白名单，即使白名单为空；
- 有独立失败语义、预算和降级路径；
- trace 中记录 role、prompt/model/profile、输入输出、evidence 与工具版本 hash；
- 有独立 fixture、合同测试和评测，不只出现在一个端到端 smoke test 中。

六个角色可以首版共用同一个模型 provider，但必须有各自的 prompt/policy 版本和合同；不为角色数量强行
安装多个模型 SDK。

## 六个 Agent 的精确边界

| Agent | 输入 | 输出与完成条件 | 允许工具 | 明确不做 |
|---|---|---|---|---|
| Interpreter | 原始需求、既有 draft、带稳定 ID 的澄清回答、support profile version | `RequirementOutcome`：`complete / needs_clarification / unsupported`、partial/final spec、typed issue/question、字段 provenance；完整 spec 必须通过确定性 schema/单位/范围/scope 校验 | 首版无外部工具；可读单位与领域词表 | 不选 element/mesh/step，不检索案例，不补造数值，不把 provider/解析失败说成用户缺字段 |
| Architect | 已确认 spec/revision、support profile、typed capability evidence，重设计时可读 prior plan/diagnosis | `ModelingPlanProposal`：solver/procedure、维度、geometry recipe、region、material/section、step、BC/load→region、mesh/element、outputs、assumption/risk/evidence；每个 spec 约束都有落点 | 只读 capability/keyword registry；首版不启用向量 RAG | 不改 spec，不生成 INP，不写文件，不提交或验收 Job |
| Input Writer | spec/plan revisions、initial/repair mode、受 guard 批准的 patch、base generation/digest、typed keyword evidence | `DeckDraft/ChangeSet`：受支持模板 profile、实体与集合命名、mesh 参数、材料/step/BC/load/output 描述、base digest、变更摘要和来源；renderer/preflight/publish 成功才形成 artifact | 只读当前 artifact 与受控 keyword evidence | 不写任意路径，不直接发布 CAS，不运行 solver，不覆盖旧 generation，不自行诊断或改变物理意图 |
| Reviewer | spec、plan、当前 artifact/digest、Job observation/attempt、结构化日志与引用、历史 fingerprint/change/diagnosis | `DiagnosisProposal`：enum category、root cause、精确 evidence、typed patch、protected invariants、verification checklist、uncertainty；证据不足返回 manual/inconclusive | 只读日志、artifact 和故障知识 | 不输出完整 INP，不修改 artifact，不重跑，不决定全局 route |
| Verifier | spec、plan、artifact/Job lineage、typed ODB extraction、确定性 check results、tolerance/evidence | `VerificationAssessment`：executable/fidelity/physics 分项 claim、evidence、gap 和 `PASS / FAIL / INCONCLUSIVE` 建议；所有 required outputs 必须有证据 | 只读 artifact、metric 和公式/reference evidence | 不修模型，不触发重跑，不覆盖确定性 gate，不写 `COMPLETED`，不把 ODB 存在当 PASS |
| Supervisor | 当前 phase、诊断摘要、预算、revision/digest、非空 enum `allowed_routes` 和 evidence refs | `FailureRouteDecision`：只能选择 `repair / redesign / clarify / manual / stop` 中已允许的一项，并说明理由与风险 | 首版为空，最多只读压缩 trace | 不参与幸福路径；不 submit/cancel，不改 phase，不绕审批/gate，不宣告成功 |

Supervisor 的调用规则是确定性的：候选为 0 时直接失败，为 1 时直接执行，只有大于 1 时才调用模型；输出
再次经过 allowlist 与预算校验。

## 不是 Agent 的组件

| 确定性组件 | 职责 |
|---|---|
| Requirement/Plan Validator | schema、单位、support profile、region 与 spec-plan 映射检查 |
| Capability/Keyword Registry | 版本化的 Abaqus/本项目支持事实；首版代替不透明 RAG 上下文 |
| Deck Renderer 与 INP Preflight | 把受限 `DeckDraft` 渲染为 `.inp`；检查 keyword、block、引用、作用域、预算和 protected spec diff |
| Artifact Publisher | staging、SHA-256、不可变 generation、manifest 和 lineage |
| Job Service / Executor | reserve、submit、poll、cancel、timeout、reconcile，以及 Mock/Abaqus adapter |
| Log Parser / Fingerprinter | 从 `.dat/.msg/.sta/.log` 提取结构化错误、稳定 fingerprint 和 no-progress 信号 |
| ODB Extractor / Physics Checker | headless 提取 U/S/RF/Mises、reference/invariant/tolerance 计算 |
| Parent Router / Guard | phase、预算、合法路线、审批和 terminal invariant；不是 Supervisor |
| Run Coordinator / Event Store | 启动与恢复父图、machine wakeup、持久事件和 SSE 投影 |

首版不实现通用 Abaqus AST。Renderer 只覆盖下面列出的 template profile；它可以由明确的 Python renderer
或受控模板实现，但不能把自由文本 LLM 输出直接当成可提交的 `.inp`。

## 首个受支持的有限元问题范围

| 维度 | 承诺支持 |
|---|---|
| Solver / procedure | Abaqus/Standard；一个 `Static, General` step；`NLGEOM=NO` |
| 物理 | 线弹性、各向同性、小应变/小变形、准静态 |
| 模型拓扑 | 单 deformable part、单 instance、单 material、单 section、单 step、无 contact |
| 几何 profile | 2D 参数化矩形板/拉伸试样；3D 参数化长方体/矩形截面悬臂梁；边/面均由轴向规则命名 |
| Dimensionality / element | 2D plane stress `CPS4R`；3D continuum `C3D8R` |
| Material | 必填 Young's modulus 与 Poisson ratio；单一均匀线弹性材料 |
| Unit profile | 明确声明且全链一致的 `m / N / Pa` 或 `mm / N / MPa`；缺单位必须澄清，不能猜 |
| Boundary condition | 分量位移、固定/encastre 边或面，以及消除刚体运动所需的受控约束 |
| Load | 2D 边 traction；3D 面 pressure/traction；规定端位移；悬臂自由端面通过 RP + kinematic coupling 施加集中力 |
| Mesh | 确定性 Cartesian structured mesh；按目标尺寸或各方向 element count；总 element/node 数有预算 |
| Field output | 必须有 `U`、`S`、`RF`；Mises 由 S 提取；按请求增加受支持分量，首版不承诺任意 history output |
| ODB evidence | step/frame metadata、节点坐标、U 分量/模、RF、S 分量/Mises、极值及位置；JSON/CSV manifest；显式关闭 ODB |
| Visualization | U magnitude 与 S Mises 静态 PNG、表格和下载链接；不启动交互式 Viewer |
| 成功 | `.inp` preflight、solver/ODB executable、specification fidelity、physics validity 全部通过 |

当前 `AnalysisType.STATIC_GENERAL` 与这个边界一致。CPE4R、圆杆、带孔板、轴对称、beam/shell element、
多 part、接触、塑性、几何非线性、buckling、frequency、dynamic/Explicit、热-力耦合、复合材料、用户
子程序、CAD/STEP/IGES、外部/custom mesh 和 HPC 都不是首个完整实现的 supported capability。

## 修复范围与预算

自动修复只允许保持已确认物理意图不变的操作：

- keyword/block 顺序或闭合；
- 缺失、错名或作用域错误的 set、surface、material、section、instance；
- node/element 编号与引用；
- section/material assignment；
- 缺失的受支持 field output；
- `Static, General` increment/control 的受限调整；
- 同一 template 与 element family 内的受限 mesh refinement。

自动修复不能改变 geometry dimensions、unit profile、material constants、load、BC、analysis target、
procedure 或 element family。后几类变化必须回到 Architect/clarification 并重新审批。

预算固定为：

- 初始 generation 之外，最多 2 个自动 repair generation；
- 最多 3 个真实 Abaqus Job attempt；
- 同一 error fingerprint 连续出现 2 次，停止自动循环；
- 每次修复必须产生不同 digest、保留 base lineage，并重新 preflight 与审批；
- `UNKNOWN` 永不自动重提。

## 最小验收证据

### Agent 级

- Interpreter：完整请求形成 valid spec；缺材料产生稳定问题；非法 E/ν/单位被 validator 拒绝；resume 后
  沿同一 revision 链完成；坏 JSON 是 invocation failure，不是“五项全缺”。
- Architect：每个 BC/load/output 都绑定真实 region；用户约束不能被 evidence 覆盖；contact/dynamic 请求
  明确 unsupported；冻结输入留下 prompt/model/evidence hash。
- Writer：Gold plan 形成可渲染 deck；dangling set/material/section 被 preflight 拒绝；repair 产生新 digest
  与 base lineage；角色没有文件系统和 Executor capability。
- Reviewer：undefined set 保留精确大小写并提出最小 typed patch；protected material/load 不改变；重复
  fingerprint/no-progress 被 guard 拦截；证据不足进入 manual。
- Verifier：solver 成功但单位/load 映射错时 fidelity FAIL；数值或平衡不满足时 physics FAIL；缺 oracle
  时 INCONCLUSIVE；只有三层全过父图才完成。
- Supervisor：0/1 候选不调用；多个候选只能返回 allowlist；伪造 `submit/completed` 被 schema/guard 拒绝。

### 两个 licensed 数值 Gold

1. **2D 矩形板单轴拉伸**：`CPS4R` 结构化网格，左边轴向约束并消除一个横向刚体自由度，右边均匀
   traction。验收平均 `S11 ≈ F/A`、端位移 `≈ FL/(EA)`、横向应变符号和反力平衡；反力相对误差目标
   不超过 1%。
2. **3D 矩形截面悬臂梁**：`C3D8R`，固定端 encastre，自由端 RP coupling 施加横向集中力。验收总反力
   与外载误差不超过 1%，端位移相对 Euler–Bernoulli 参考值约 5%–10%，粗细两档网格端位移变化不超过
   5%。固定端边缘最大 Mises 不作为主要 Gold target，因为它可能对网格敏感。

另外必须有：错名 NSET/material/output 的受控修复案例；缺字段 clarification；contact/Explicit/复合材料
拒绝案例；Mock 的重复提交、重启、取消、超时、`UNKNOWN` 和提交崩溃窗口。Mock 只证明控制面，不计入
真实 Abaqus 或物理正确率。

## 当前合同与目标范围的差距

| 当前骨架 | 必须演进成什么 |
|---|---|
| `SimulationSpec.geometry_description` 与 BC/load/output 的 `list[str]` | typed geometry dimensions、named regions、BC/load/output、unit/provenance 和 support profile |
| `RequirementResult` | partial draft、status、typed issue/question、字段来源和需确认假设；provider failure 单独表达 |
| `ModelingPlan` 的六个字段 | solver/procedure、geometry recipe、region、section、step、BC/load mapping、mesh、output、risk/evidence/version |
| `Sequence[str]` retrieval/log context | `EvidenceRef/EvidenceBundle`，包含 source/version/digest/score/snippet；首版 evidence 可以为空 |
| `InputWriterAgent.build() -> ArtifactManifest` | Agent 返回 `DeckDraft/ChangeSet`；只有 renderer/preflight/publisher 可以返回最终 manifest |
| `ArtifactManifest` 单路径与 digest | run/spec/plan/base generation lineage，以及每个 file 的 digest/size/media type/provenance |
| `DiagnosisReport` 的自由 category/evidence string | enum category、root cause、typed patch/evidence、uncertainty 和 verification checklist |
| `VerificationReport.passed` 与平面 checks | executable/fidelity/physics 三个 `PASS/FAIL/INCONCLUSIVE` gate；总结果由 deterministic policy 派生 |
| `SupervisorDecision.next_role: str` 与完整 `Mapping[str, Any]` state | typed `RecoveryRoute`、最小 `FailureRoutingContext` 和双重 allowlist/budget 校验 |
| 当前内存 Mock 的五态 Job | 完整 Job FSM、持久 ledger、scripted failure/cancel race/restart attach；Executor 仍不是 Agent |

不能在当前 Protocol 上只补 prompt 就称为完成；上述合同、权限、子图、trace 和独立测试必须共同进入真实
调用路径。

## 扩展边界

以下能力只能在增加相应 `AnalysisType`/support profile、typed schema、renderer、ODB extraction、物理 gate
和 Gold cases 后成为 supported：

- `CPE4R`、圆杆、带孔板、轴对称和更多静力模板；
- B31、shell、二次单元与多 part；
- eigenvalue buckling、frequency；
- contact、plasticity、NLGEOM、Riks、dynamic/Explicit、thermal/coupled；
- 经过来源核验的 typed case cards 和 RAG 消融；
- custom mesh、HPC 与 remote runner。

上游原始案例、FAISS/pickle、任意脚本执行、固定全局 workspace 和交互式 CAE Viewer 不进入本项目产品
依赖。扩展分析类型时也不要求追平上游案例数量；每一项先满足同样的合同、执行和验证门槛。

## 相关文档

- [完整系统框架](system-framework.md)
- [一次仿真 Run 的工作流](flows/simulation-run.md)
- [Abaqus Job 生命周期](flows/job-lifecycle.md)
- [技术栈与工程讲解点](technology-stack.md)
- [AbaqusAgent 源码设计分析](../scratch/abaqusagent-source-design-analysis.md)
- [ADR-0002：使用 LangGraph 构建分层多 Agent 系统](../adr/0002-langgraph-hierarchical-multi-agent.md)
- [ADR-0004：以证据和物理验证定义任务成功](../adr/0004-evidence-first-physics-verification.md)
- [ADR-0005：有来源地吸收与重构 AbaqusAgent](../adr/0005-traceable-upstream-reuse.md)
