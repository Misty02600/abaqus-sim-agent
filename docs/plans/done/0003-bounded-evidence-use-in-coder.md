# PLAN-0003：模型应用侧实行有界查证与明确的继续/停止策略

| 状态 | 完成时间 |
|---|---|
| 已完成：实现已验，真实模型行为gate未通过，默认关闭 | 2026-09-12 |

## 背景与目标

依赖[PLAN-0002的证据端口](../done/0002-rag-runtime-evidence-service.md)。RAG 即使返回真实、相关段落，也可能没有
覆盖问题的每个条件；当前模型还曾在漏掉规定转角单位时判断 sufficient。不能靠一个相关性阈值或一次
模型自评解决这个问题。

本计划由**同一个 abaqus_2024_coder 加宿主规则**管理查证，不新建检索Agent、Reviewer或LangGraph编排。
目标是让缺口不被悄悄遗忘、补查有预算、已知阻塞不被忽略；不保证模型能发现所有未知知识或正确理解所有引文。

## 当前实现与需要增加的部分

- [旧文档工具](../../../src/abaqus_sim_agent/coding/concept_lookup.py)已有重复检测与
  `reserve_concept_lookup`，默认 `max_concept_lookup_calls=2`，但没有按查证链组织的补查/回评合同。
- [dependencies](../../../src/abaqus_sim_agent/coding/dependencies.py)拥有计数；
  [resume](../../../src/abaqus_sim_agent/coding/resume.py)已合并查询journal与累计usage，应在此基础上恢复
  新的查证状态，不能另起一个会清零预算的循环。
- [CodingRunDecision](../../../src/abaqus_sim_agent/coding/contracts.py)只有 needs_input、
  ready_for_verification、failed。保留这三个终态选择，最终outcome继续由宿主与原verifier形成。

## 确定的方案

### 1. 只增加一个显式实验profile

已增加 `document_policy_mode=off|bounded_v1`（CLI对应 `--document-policy-mode`），默认off；旧文档模式
和历史协议不变。`bounded_v1` 必须显式配置PLAN-0002端口和已有文档查询预算，不能静默接入新的索引。
该profile首先用于无Abaqus的应用行为测试，不能凭接线通过就成为普通CLI默认。
配置组合明确为：policy=off维持原行为；bounded_v1要求已有concept_retrieval_mode=tool，并替代该模式的旧
文档工具registrar，不能同时注册两个同名search_abaqus_docs。bounded_v1与concept=off同时出现则拒绝配置。
policy与backend绑定在同一run内固定，不能通过resume关闭策略绕过未决状态；预算调整仍走既有显式amendment。

独立组件已放入 `coding/document_policy.py`，类型位于 `coding/document_policy_contracts.py`；确定性状态转换与
日志分别位于 `document_policy_state.py` 和 `document_policy_journal.py`，已通过
agent/dependencies/service/resume装配接入。被候选manifest绑定的旧concept_lookup、document_evidence
及检索实现保持不变，不复制它们的正文/检索算法。若请求/续跑持久格式需要演进，显式版本化并验证旧模式兼容。
应用只依赖自身的 `document_search_port.py` 协议，CLI组合根选择PLAN-0002的显式适配器，不由Agent选择后端。

### 2. 查询前登记“要确认什么”

应用为查证登记至多8个 `EvidenceNeed`：id、需确认的条件、来源、是否关键、影响的下一步。
来源区分 user_declared、task_contract 和 model_proposed；宿主分配/维护ID。

- 用户/固定任务合同指定的关键条件不可被模型删除或降为可选。
- user_declared/task_contract身份由宿主赋予，模型只能提交model_proposed；不能通过自报来源伪装成用户指令。
- 模型从自由文本提出的要点不能冒充宿主已经验证的需求。它可能初次就漏列某个条件，这是协议的已知限制。
- 条件超过本profile上限时明确报告，不能裁掉尾部条件。未登记已知业务模型值时，不能让RAG补造这些值。
- 已有任务、工作区或运行证据足够时，不强制发起检索；精确Python API优先使用显式Catalog，本地文件问题
  先检查工作区，缺少会改变物理模型的必需用户输入先走既有澄清。

### 3. 搜索之后必须作一次结构化回评

仍使用 `search_abaqus_docs` 名称，但在该profile下注册版本化输入/结果，而不是修改旧协议的语义。
应用侧输入包含 question、observed_context、首次查询的 proposed_needs，以及补查时的 follow_up_of
和 unresolved_need_ids；首次与补查采用互斥分支。宿主合并已有强制need后登记，向RAG端口只传查询、受保护
scope、search_id和deadline，不把need标签或宿主验收答案塞入检索/重排文本。
宿主接收PLAN-0002的receipt后登记待回评状态。已增加仅记录分析的 `record_document_assessment` 工具，
不执行脚本，不授予额外权限，也不是第二个Agent。

模型回评包含：search_id、每个已登记need的 supported/partial/missing/conflict、引用的evidence_id与精确
原文片段、适用范围限制，以及下一步建议。只要求简短证据说明，不收集长篇思维过程。

宿主只做可确定的检查：

- 回评覆盖全部已登记need，不能省略仍未解决的条件。
- 引用ID属于本run/当前revision允许使用的receipt，原文确实存在；不能引用未返回的内容或伪造来源。
- 不能删掉硬版本、产品、来源约束；unknown不能被改写成已验证适用。
- receipt为unavailable/rejected或没有证据时，不能声明获得了RAG支持。

**引文存在不等于引文支持结论。**这些检查通过，只表示回评结构和来源引用成立；模型的supported仍是
model-assessed，不能成为物理正确性或任务通过证明。来源冲突的语义识别同样主要依赖模型，不伪装成确定性能力。

### 4. 补查由宿主批准，最多一次

| 边界 | 规定 |
|---|---|
| 每条查证链 | 首次查询 + 最多一次语义补查，以宿主root_search_id标识 |
| 整个run | 沿用累计max_concept_lookup_calls，默认2；链上尚可补查不代表run还有预算 |
| 补查输入 | 指向具体未解决need；新问题保持原意并继承原硬约束、来源和backend绑定 |
| 同义绕行 | 改写问题、context或need名称不能创建额外额度；不能假造新root绕过同链限制 |
| 原样重复 | 不重新搜索；只在依赖重新核验通过时返回原receipt并标记cache_hit，不新扣查询额度 |
| 技术故障 | 当前本地端口不自动重试，不以换个问法绕过integrity/model/timeout故障 |
| 预算计数 | 验证通过并实际派发前先落盘reservation；失败、超时也消耗该次查询，不成功才算一次 |

补查不是第二个独立tool loop，仍由Pydantic AI/Harness原循环执行；所有回评、重试结构纠错继续消耗原模型
请求与wall-clock预算。没有第三轮自动补查、自动联网、自动增加来源或跨run经验回写。
root由宿主绑定到当前已登记查证需求，不接受模型自报root。活动链未完成回评/处理前，不允许用新的首次查询
分支重开它。该规则约束已登记链，不宣称能自动识别所有自然语言同义问题；run级累计上限始终兜底。

### 5. 继续还是停止，按影响决定

| 当前情况 | 模型应用侧动作 |
|---|---|
| 已有证据足够，不需要查文档 | 直接按原流程工作，不强制检索 |
| 找到证据且全部已登记条件被模型判断支持 | 经宿主引用/约束检查后回到原工具流程；仍需原权限、运行检查与verifier |
| 仅部分支持，存在可搜索缺口且有额度 | 只针对缺口补查一次；保留已支持内容及未解决条件 |
| 无证据、冲突或补查后仍不足 | 输出已知部分和具体缺口，不自行补造结论 |
| 缺的是会改变物理模型的必需用户输入 | 既有needs_input澄清；不得先调用Abaqus或拿常见值填充 |
| 关键知识或指定文档来源缺失 | failed，failure_stage使用明确的knowledge/evidence原因；可在summary说明部分已知内容和所需资料 |
| 缺口不影响一个明确独立的下一步 | 可继续该步，但未解决条件仍保留；不能因此把整项任务宣告完成 |

在该profile下，文档返回后的待回评状态，以及**已登记且明确未解决的关键条件**，应在相关Abaqus调用与
ready_for_verification前检查；拒绝不消耗Abaqus预算。普通文件阅读和整理可以继续。未能证明下一步不依赖
关键缺口时，保守停止依赖它的执行。
“无关步骤”的依据是任务/用户已声明的依赖边界；不能仅凭模型一句“不影响”解除关键阻塞。没有明确边界时，
允许普通文件检查，但保守阻止Abaqus执行。模型回评supported仍只回到原执行检查，不增加任何权限。

这不是通用语义防火墙：宿主不能自动知道模型遗漏了什么，也不能凭精确引文检查判定所有科学结论。
模型supported只能回到原有受控工具入口，不能新增权限、跳过原检查、替代execution_evidence或task_oracle。
部分答案保存在summary/产物中时，不能把仍缺关键条件的任务伪装成completed。

### 6. 状态与续跑由宿主管理

独立组件已使用workspace外的版本化 `document_policy.jsonl` 保存need注册、reservation、receipt、assessment
和链结束状态转换；`document_policy.head.json`锚定完整前缀，`PolicyCheckpoint`已接入宿主resume v4快照。
写入必须持有同一state的既有不可继承OS锁，模型与RAG worker不获得修改该ledger的工具。
这里指不提供直接改写入口，不宣称已有OS级不可信租户隔离。

- resume合并ledger与快照，累计模型/查询预算不清零，补查次数不可重置。
- 已完成receipt复用前必须核验依赖；不是只比较索引目录名或manifest指针。
- 中断留下的只读查询reservation记为abandoned并保留已占额度，默认不自动重跑；不伪装成仍有可用结果。
- 新task revision使旧need/assessment退出当前有效集，原记录保留；新任务仍继承run累计预算。
- ledger损坏或receipt漂移fail-closed，不从模型聊天文本推测或重建“成功查证”。

这只是本地文件后端的查证状态，不是数据库事务、分布式协调或外部Job exactly-once；原三个Abaqus工具的
needs_reconcile规则不变。

## 实施与验收

1. 先用fake证据端口实现应用状态、预算、引用检查和续跑；不调用真实模型或Abaqus。
2. 接PLAN-0002端口，确认同一Coder能消费新receipt；off和历史tool模式保持原行为。
3. 冻结下列8类模型行为场景，每类至多一个新run，模型请求上限显式设为12，Abaqus使用假工具；记录全部
   原始响应与工具轨迹。Provider失败算无效样本，不补跑至通过，不修改旧实验成绩。

| 场景 | 主要验收 |
|---|---|
| 已有工作区证据足够 | 零查询，正常继续 |
| 文档直接支持登记条件 | 正确引用并继续，不把found说成已验证 |
| 相关但漏一个已登记条件 | 回评必须保留缺口，实际触发一次补查并消费第二次证据 |
| 补查仍没有必要证据 | 关键步骤停止，保留缺口，不发起第三次 |
| 指定版本缺失或来源冲突 | 不放宽约束、不强行合并，正确表达来源限制 |
| 缺物理输入 | 先澄清，查询和Abaqus均不先执行 |
| 技术故障但存在独立的可做工作 | 只继续无关步骤，不使用损坏/不存在的receipt |
| 伪造引用、重复请求及resume | 机械拒绝/去重，预算与同链次数正确恢复 |

确定性测试另外覆盖异常输入、quota不足、reservation后崩溃、坏ledger、超时worker、错误scope、引用存在
但语义不支持的反例。后者只能由独立标注/人工审阅识别，不能把宿主机械检查通过当成语义通过。

完成标准分开报告：

- **工程合同**：所有边界测试通过；已登记关键缺口与非法引用不能绕过宿主状态检查；预算和resume保持。
- **模型行为**：实际观察到正确补查分支及受控停止；逐场景由预定标签审阅，不由模型自报验收。
- **不作承诺**：8个smoke不是稳定成功率，不证明真实仿真收益。行为验证失败时保留实验profile，报告未通过，
  不默认启用、不继续改提示词刷到通过。

## 第一阶段实施结果

| 部分 | 已实现及验证的边界 |
|---|---|
| 独立策略与类型 | strict首次/补查互斥输入、宿主need ID/root ID、至多8个need；模型不能伪造host来源、物理输入身份或降级已有必需条件 |
| 回评 | 覆盖链上全部need，精确引用同链/当前revision的已交付原文；unknown不升级；supported明确为model-assessed |
| 补查与额度 | 查询前先fsync reservation，失败/取消仍占额度；每链最多一次补查，与共享run计数共同限制；技术故障/废弃查询/失效receipt阻止当前revision的换名重试 |
| 门禁 | `execution_gate`只读状态；实际执行前应调`validate_execution_gate`重验所依赖的关键receipt；门禁拒绝不调用Abaqus或增加其计数，普通文件检查可继续 |
| 独立步骤与输入 | 只有host声明的依赖豁免可放行无关执行，不能豁免ready；缺物理输入先阻止查询/执行，host确认输入后才能解除 |
| 持久状态 | 同一CodingRun锁内追加hash链与head；完整事件领先旧快照可重放，坏JSON、尾部截断、错误转换/head拒绝；未完成reservation恢复为abandoned且不重查 |
| 修订与计数 | 复用实际`CodingAgentDependencies.concept_lookup_calls`，恢复取日志与宿主计数较大值；host预算修订不清零，任务revision更换need/scope但不复用旧receipt或重置run计数 |

[45项专项测试](../../../tests/test_document_policy.py)已通过，包括真实第二次**假端口**调用、第三次拒绝、
伪造引文、缺条件、来源漂移模拟、旧快照间隙、取消/崩溃、host输入、独立步骤以及原依赖计数的组合测试。
故意保留“引文确实存在但解释错误仍能通过”的反例，避免把结构检查包装成语义判断。
没有真实RAG查询、生成模型请求或Abaqus调用。验证摘要见
[第一阶段记录](../../../benchmarks/coding/document_policy_phase1_20260912.json)。

最终全仓回归1069通过、27项持证跳过（90.26秒）；BasedPyright零错误/警告，Ruff规则检查与本次6个Python
文件格式检查通过。旧基线/BGE的28项seal文件、PLAN-0002记录的22项源码hash均未改变。

## 第二阶段实施结果

| 部分 | 已实现/验证 |
|---|---|
| 配置 | `DocumentPolicySpec`严格JSON；路径相对文件解析，无环境覆盖/回写。有效spec和实际内容binding写入request/launch，resume不重新读取外部spec |
| 显式启用 | `--document-policy-mode bounded_v1 --document-policy-spec ... --concept-retrieval-mode tool`；默认off。新模式从spec选索引，旧concept-index-dir仅用于另外显式选择的diagnostic advisory |
| 同一Coder | 新registrar替代旧文档registrar，仅一个search_abaqus_docs，另加record_document_assessment；未增加Agent、通用Shell或宿主Python工具 |
| 真正门禁 | 三条runtime helper在授权检查后、preflight/预算/runner之前执行文档门禁；ready及预算耗尽验收都先检查，不能绕过未解决关键条件 |
| 原生续跑 | 新模式写launch v2/resume v4，checkpoint与journal合并，累计usage不清零；现有amendment更新预算/goal。旧off模式保留v1/v3、不输出新增null字段 |
| Reconcile顺序 | 未决Abaqus效果仍先进入needs_reconcile；此前不会重开查证链或覆盖已有文档checkpoint |
| 物理输入 | spec中的physical_input需clarification_field_id，已有宿主澄清流程答复后才更新need；这不是新的数值/物理正确性验证器 |
| 独立步骤 | 可显式声明kind/path/sha256及关联host条件；只放行当前bytes匹配的批准脚本，改写后失效；不能豁免ready或缺物理输入，也不证明传递依赖/科学独立性 |
| 真实重验 | 独立Windows worker调用既有snapshot/原文/关系校验，原始诊断保留；采用同一Job生命周期机制，可超时/取消，不联网、不执行模型可控宿主脚本 |

本阶段新增[13项接线测试](../../../tests/test_document_policy_integration.py)，使用真实Harness、CodingRunService、
CLI与确定性FunctionModel，覆盖fake runtime/query和真实失败重验worker。接线中复现SDK单个BaseModel参数
展开后nested strict tuple校验失败；回评工具采用`protocol="document-assessment-v1"`加`assessment`对象，
保留strict JSON路径，不改SDK、不增加list强制转换。首次查询仍是`query`对象分支。

另外两次[真实后端接线](../../../src/abaqus_sim_agent/evaluation/document_policy_wiring_smoke.py)均通过：每次4个
确定性FunctionModel请求、1次真实RAG、3次独立重验、0次Abaqus，报告文件通过execution_evidence，记录resume v4。
两次总时长88.39/59.97秒，后一次RAG服务自身40.55秒；不能单因果归为优化，也不是SLO。检索源/模型binding
保持原值，旧基线/BGE的28项seal文件未变。只证明实际工具和状态链路，不证明模型理解/采用、检索质量或物理收益。
最终全仓1082通过、27持证跳过（109.54秒）；BasedPyright与Ruff规则检查通过，本次13个Python文件格式检查通过。
详情见[第二阶段记录](../../../benchmarks/coding/document_policy_phase2_20260912.json)。

## 第三阶段：首批无效样本与当前阻碍

已冻结[8类场景与预定标准](../../../benchmarks/coding/document_policy_behavior_cases_v1.json)，使用完整、经过
源文件核验的两个官方段落进行受控证据回放/故障注入，以稳定触发补查与停止分支；不是检索排名实验。
driver为[document_policy_behavior.py](../../../src/abaqus_sim_agent/evaluation/document_policy_behavior.py)，
其8项确定性测试通过。真实RAG接线仍由第二阶段证据承担，不能把本批受控回放说成live retrieval。

本批固定Go/Flash、每run最多12次模型请求/2次查询/360秒、HTTP timeout=60秒、HTTP retries=0、
请求max_tokens=2400和thinking=disabled，每场景仅一个新run。原文fixture、场景、全部产品源码、Skill和
运行设置在首个请求前封存；第8场景原计划在首张收据落盘后注入一次同run中断，不是Provider失败重试。

实际8次HTTP请求全部在生成前返回400，错误类型均为`MissingSessionID`，指出缺少`x-opencode-session`。
没有ModelResponse、工具调用、查询或Abaqus调用，预定resume分支也未到达。因此是**0个有效行为样本、8个
Provider无效样本**，不是模型行为通过率0%，不能评价模型是否正确引用、补查或停止。原始请求体、HTTP响应、
日志、seal和一次性completion均保留，未修配置后补跑。见[本批记录](../../../benchmarks/coding/document_policy_phase3_20260912.json)。

当前[Go官方接入要求](https://opencode.ai/docs/go/#where-can-i-use-it)明确要求客户端标明自身User-Agent，
并为每个conversation发送稳定的`x-opencode-session`。现有`coding/model.py`未设置这两项，故当前接入不满足
要求。Pydantic AI会自动添加框架级User-Agent，不等于本项目自身标识；本次明确观察到的拒绝原因只有缺session，
不是两种独立错误。不中途修改adapter或重发，也不猜测要求具体何时改变，更不能把此失败归因于RAG或模型质量。
新driver的8项测试及全仓1090项测试通过，27持证跳过，BasedPyright和Ruff规则检查通过；它们不能替代缺失的行为证据。

### D-001 已处理：接入修复及新的独立批次

用户授权后，共享Go adapter已要求调用方提供conversation ID，仅发送带版本域的SHA-256，并注入
`abaqus-sim-agent/<version>` User-Agent。同run resume保持相同session，不同run隔离；调用方不能覆盖这两个
host-owned header。CLI与全部产品evaluation调用方已传入各自run ID，其他网关拒绝误传conversation ID。
离线测试检查实际HTTP请求header；全仓1092项通过、27项持证跳过后才启动独立batch-2。
旧batch-1保持原样，不覆盖、不回填成绩。

### Batch-2行为结果

新批次只改变transport修复，沿用已封存的8场景、受控原文、Go/Flash、Prompt、预算、2400-token输出上限、
360秒wall time与`http_retries=0`。共30次Provider请求，29次获得HTTP 200，一次首次请求timeout；4次受控
证据查询、1次cache hit、0次live ranking、0次Abaqus。

主机按运行前标准复核为：

- 通过3项：已有本地证据零查询；直接证据支持并正确引用/继续；指定2024版本缺失时保持hard scope并以
  `knowledge_evidence`停止。
- 模型行为失败4项：partial场景跳过必需回评、直接补查而被宿主拒绝；缺物理输入最终虽正确澄清且零查询/
  runtime，但澄清前先读写文件，未满足first-action gate；技术故障独立工作场景未产生完整响应；伪造引用/
  重复/resume场景确认了同run恢复与cache hit，但未完成回评/纠错终态。
- Provider无效1项：补查仍不足场景在首次响应前timeout，未观察到行为，不计成模型行为失败。
- 三个非Provider场景触发固定2400-token输出上限；按预定规则没有增加上限、简化Prompt或重跑。

因此8场景gate失败，`bounded_v1`不获得真实模型行为资格，保持显式、默认off。局部正向分支只作观察，
不能声称稳定成功率、通用补查/停止能力、检索提升或仿真收益。完整结果见
[batch-2记录](../../../benchmarks/coding/document_policy_phase3_batch2_20260912.json)；原始host review位于
`var/audits/document-policy-live-v1/batch-2/host_review.json`。

技术故障停止只在宿主明确开启新task revision时重置；普通resume或增大查询预算不清除该状态。
独立组件允许host在新revision显式改变scope；当前CLI的document spec/binding在整个run内固定，改变它须新run，
不能靠goal文本或普通resume修改。依赖豁免只能由宿主任务边界赋予，
模型提出的affects_step不产生豁免。所有host_*方法均不属于模型工具输入。
hash链/head用于完整性与丢写检测，不是防恶意重写的签名，也不是数据库事务；当前没有多进程协调服务。

## 最终状态

三阶段工作均已执行：工程合同、产品接线和真实模型行为评测完成。实现保留为显式实验profile，但行为gate
未通过，普通默认路径仍off；没有新增持证Abaqus记录，也没有自主补查收益结论。本计划以负资格结论完成并
移入done，不继续改提示词或补跑至通过。
如果实现需要改变最终decision种类、放宽执行权限或破坏旧持久格式，必须先说明并另行决定，不能以本计划扩权。

相关依据：[ADR-0008](../../adr/0008-pydantic-ai-harness-abaqus-coding-run.md)、
[ADR-0010](../../adr/0010-budgeted-official-concept-retrieval.md)、
[原补查行为限制](../../../benchmarks/rag/evidence_reader_json_v2_live_20260911.md)。
