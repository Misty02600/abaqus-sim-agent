# PLAN-0006：轻量程序检查与人工审查的领域任务评测

| 状态 | 最后更新 |
|---|---|
| 进行中：新评测改为人工确认答复；一次真实同run补答后正确使用1 mm并续写，但请求预算耗尽且脚本有region错误；无新完整交付或持证成绩 | 2026-09-13 |

## 当前实施范围：简单建模与澄清

本轮用户已确认人工补答及一次独立post-hoc续跑，授权已消费，不追加第二次。仍是原run `question-cal-e731ca30b24fcefe`，turn 1→2、revision 0；原17份Case文件先备份到`var/audits/ask-user-live-calibration-20260913/posthoc-1/pre-run/`，报告和旧summary不回填。仅按原question_id提交1 mm（source=file），原生ToolReturn恰好一次，模型不再提问，最终脚本THICKNESS=1.0并用于截面。新增7逻辑/9 HTTP，累计10逻辑/12 HTTP及284.267秒active wall；第11次请求被原预算拒绝，终态failed/model_budget，无ready声明或verifier结果。HTTP重试曾恢复成功（一次成功响应头51.146秒），不能把本次终态误称为SDK重试耗尽。最终脚本在二维BaseShell上仍使用part.cells做截面与网格region，属于静态建模问题；零运行工具尝试/零Abaqus/零数值检查，不能声称可运行或物理通过。续跑spec SHA-256为`31612be856c1cc5a1bc8153259a20f1ffb7521c01b0d1f8c96b3e2cf68676053`，独立报告及Codex静态核查见同目录report.json/review.json，原备份17份及最终32份hash均复核无漂移。

人工确认profile的3项新增回归覆盖新spec绑定/旧版本不可切换，以及可匹配/不可匹配问法均不得调用正则答复器、待答不阻塞另一任务。两组定向离线测试共99项通过，两个改动实现模块BasedPyright零错误/警告，Ruff通过，uv.lock未改；未重新运行全仓或持证测试。真实补答只证明一条问答持久化与继续写文件的路径，不证明完整交付、问法普适性或成功率提升。

最新用户批准的两条串行、仅写脚本校准已消费：每条10模型请求/300秒、enabled/high、HTTP60秒/额外重试10、零运行预算，无Provider续跑或替换样本。记录位于本机`var/audits/ask-user-live-calibration-20260913/`，spec SHA-256为`2b21a1d90039f8b8abca3cfb88b5360986c03c4a7f01813f4b29ae1bcef71dbc`。完整输入板7逻辑/10 HTTP、295.719秒，零问题，交付脚本并通过freshness/Python语法；Codex静态核查发现`setElementType`使用`elemType1/elemType2`而非本地2024.10签名的`elemTypes`，以及SectionAssignment的tuple region风险，不能称可运行或物理通过。第三逻辑请求三次未记录响应头后SDK第3次重试成功，单次成功响应头仍需46.221秒；中间异常类型未记录，不推断底层责任。

缺厚度板原批3逻辑/3 HTTP、15.532秒，在写脚本/运行前正确调用ask_user询问面外厚度；但`ThicknessClarificationFixture`对整段问题扫描禁词，解释/条件句中的两处“长度”触发adapter_gap，未发送预设1 mm答复，原批终止于waiting_for_user。只读回放已复现误拒；这不是模型不提问，原批没有测到答复后的行为，随后另有上文的显式续跑。原两条共零runtime工具尝试/零实际运行，37份原证据当时复核无漂移；独立`static-review.json`保存AI核查，不改原报告、不冒充人工签署。当前仍不扩大批次，也不扩展整段问法正则。

用户已确认将当前重点收敛为简单模型从零建模，以及索求必要补充信息后完成同样的仿真交付。旧T2截面变更、T4警告修复暂不推进，但不删除其代码或历史证据。旧四题×三次的12槽位仅保留作兼容/离线自测，不再作为当前待执行批次；不回填既有T1报告。

当前固定双任务为`missing-input-plate-v1/r1`和`requirements-bar3d-v1/r1`，各一次。前者复用T3：需求文件不给板厚，宿主答复1 mm；必须在任何Abaqus调用前澄清，然后完成建模/求解/提取。后者复用历史三维杆Gold的完整需求，工作区只有requirements.md：100×10×10 mm、E=210000 MPa、ν=0.3、C3D8R 20×2×2共80单元、Static General、NLGEOM=OFF；左端面U1=0，A=(0,0,0)固定U2/U3、B=(0,10,0)固定U3，右端面U1=0.1 mm，U/S/RF输出，不需澄清。唯一实例/分析步与最终帧目标量定义、六项新交付物和保留需求文件规则沿用共同合同，杆产物名为bar_tension.*。

`evaluation/simple_modeling_batch.py`提供prepare/run/verify，新prepare生成`simple-modeling-pair-spec-v3`，显式绑定ask_user_v1与human_confirmed；v1保留legacy，v2保留原脚本答复fixture，旧T1入口不放宽，批次仍为`live-simple-modeling-v1`。复用同一`_run_batch`、Coder和ExecutionEvidenceVerifier，每任务独立workspace/state、会话、预算与HTTP日志。只有模型/文件阶段可并发2；`SerialRuntime`在此批次进程内用共享Semaphore将Agent的全部Abaqus进程限制为1。取消排队不启动进程，进程清理完成前不释放名额；`service-checks.json`单列runtime_queue_wait_seconds，等待同时计入任务wall，不能把排队当作额外免费预算。这不是跨进程/跨机器许可证协调器。

用户已确认简化答复方式：新profile收到任何ask_user后直接保留待答、结束该活动段，不调用ThicknessClarificationFixture的文本识别，不阻塞另一条任务。人工阅读问题并确认与任务卡的固定答案一致后，复用现有question_id/answer-file接口；只提供均匀板厚1 mm，不追加API提示、脚本或建模建议。非预声明问题与重复提问不自动答复；waiting仍为未完成，不因有人读过问题就计为交互通过。人工等待不计active wall，续跑预算累计；已落盘trial/summary保持不变，续跑授权和关联报告独立记录。这是薄评测入口的策略切换，不增加问法分类器、另一套Agent loop或批次恢复框架。

每题候选上限30模型请求、9 Agent Abaqus（含最多3 Solver）、2700秒wall；Go/V4-Flash enabled/high、Planning开、HTTP60秒及额外重试10均保持。两题合计上界60逻辑请求、660 HTTP尝试、18 Agent Abaqus（含最多6 Solver）；没有自动续跑、样本替换或Provider切换。全部Agent结束后顺序做每题最多1次host-only ODB读取，每次120秒，总共最多2次且不提交额外Solver。准备/收尾/人工审查时间另计，不能承诺整个批次45分钟内完成。

数值只复用已有plate/bar ODB提取器，并由共享后置检查比较三项有限标量、符号、2%相对容差及80个指定单元：板U1=0.1 mm、S11=210 MPa、RF1=-4200 N；杆U1=0.1 mm、S11=210 MPa、RF1=-21000 N（σ=E×0.1/100，杆面积100 mm²）。不把旧完整bar oracle总布尔值接入Agent，几何、约束、网格与数值含义仍需人工检查，未签署不报完整通过。T1人工审查可与这两条任务运行并行，无须先把T1签为通过才能接通其他题。

新增8项离线验证包括真实工厂+MockTransport的双会话同时推进、两个CAE请求竞争时runtime峰值1、澄清/usage/日志互不混用、独立取数复用不重跑、跳过澄清或反力符号错误不通过、批准/source漂移拒绝，以及等待取消/总wall取消/进程清理持锁。全仓1034通过、27持证测试未运行，六个实现模块BasedPyright与相关文件Ruff通过，JUnit位于本机`var/audits/simple-modeling-pair-20260913/full-tests.xml`。以下真实批次另列，不能用离线测试冒充Provider并发提速或双题闭环通过。

用户已明确批准并执行本机`var/audits/simple-modeling-pair-20260913/pilot-spec.json`，SHA-256 `97532fd23686acab3b5b6519fdb9f7802a3862837a94b2f16a014b003e2c22a9`；结果位于`var/pilots/simple-modeling-pair-20260913/`，batch `3d337c25fe7344d28f60f0a3d623ec0b`。两条均已启动、均未完整交付，summary为0 pass/2 fail、0人工审查；授权已消费，没有替换、续跑、换通道或增加预算。

- 缺厚度板run `hybrid-live-97c545b782b0a506`：30次框架记录模型请求、31 HTTP、9 Abaqus/1 Solver，trial wall 282.297秒，最终failed/model_budget。零澄清，脚本注释和常量自行给出1 mm；首次Abaqus调用前未询问，已违反预定交互要求。Solver原`.dat`报告80个单元缺少属性定义及NO SECTION DEFINITIONS FOUND，无完成标志，缺extract_results.py/results.json。程序原cause保留budget、interaction栏unknown，不回填为人工审查；未澄清事实另记录于本段。原trial-result SHA-256为`f3ec7349edb47df027eb65d82caaa0411d08db8d97bddd378c2b839bae80ad5f`。
- 三维杆run `hybrid-live-6544aa314338e9ce`：前2次模型响应成功；第三个逻辑请求首次尝试加10次额外重试均未取得响应，最终ModelAPIError→APITimeoutError→ReadTimeout，终态interrupted/provider。共13 HTTP、0脚本/0 Abaqus，trial wall 736.594秒。框架usage.requests只记录2次，未包含最后失败逻辑请求；因此汇总32是框架记录值，不能称全部已发起逻辑请求数。失败尝试token消耗未返回，不推测其计费为0。原trial-result SHA-256为`fcfd44f1c65a864a54d3ed12e78d269022758afaedfda50ae48371d24b2a56aa`。

合计44 HTTP、9 Agent Abaqus（含1 Solver）、0额外host读取；两个后置报告均因缺本次完整执行/产物证据而不派发oracle，numeric为unknown。全部任务和后置报告hash已只读核对。两任务wall合计1018.891秒是各任务耗时之和，不是并发批次实际墙钟时长。两条runtime_queue_wait_seconds均0，三维杆未到运行阶段，因此本次没有真实Abaqus争用，不能据此声称串行闸门带来收益或真实并发更快/更慢。原两条T1记录不变。

离线诊断确认输入无默认板厚泄漏或中文损坏，缺参指令确实送达；答复器仅在模型主动返回NeedsInputDecision后触发，模型未走到这一分支。运行前不存在独立物理输入完整性判定，不能将Prompt中的clarification gate描述成宿主硬门禁。三维杆属于请求读取超时，尚不足以区分网络、网关、上游处理与并发影响。不把此批两失败混称为两种物理建模能力均被否定，也不追加样本把失败抵消。已有T1复测仍待人工审查。

用户确认后已仅修评分与计数：T3已正确收到1 mm且在任何Abaqus调用前询问，交互栏可独立记pass，即使随后Provider中断；无合格澄清却已有Abaqus调用，或已经宣告completed，记fail；工作尚未展开就中断则unknown。完整输入任务发生不必要澄清也作为已知交互失败记录。只修改外围评分，不拦截模型、不给隐藏板厚提示、不回填旧run或旧分数。

新`model_requests_started`记录进入HTTP发送流程的模型请求数（含最终失败请求），每条HTTP日志保存SDK的`x-stainless-retry-count`数值；0为新的首次发送，连续正数为其重试。保留原`logical_requests`框架usage字段和`http_attempts`，不修改模型预算执行语义。旧日志缺字段、重试序列不完整或出现重定向时新计数为None/unknown，不按正文hash或成功响应数猜测，不用0冒充缺失值。

本轮新增5项测试，59项定向通过，全仓离线1039通过、27持证未运行，Ruff与四个实现模块BasedPyright通过；验证记录位于本机`var/audits/interaction-count-fix-20260913/`。覆盖预算失败仍保留交互违规、Provider前置中断未知、正确澄清后中断保留pass、相同正文的新请求与SDK重试区分、一次失败请求对应11次HTTP尝试。旧双任务证据hash复核不变，不回填其原unknown交互栏或请求计数；没有真实模型/Abaqus调用。

上述诊断对应旧needs_input协议。用户随后批准的[ADR-0024](../../adr/0024-host-owned-user-question-waiting.md)已将新默认会话改为ask_user＋宿主持久待答/恢复，旧profile不变；simple-modeling spec v2引入新协议，当前v3再将答复交给人工确认。新自然模型脚本校准及后续补答与旧批次独立记录；没有新持证成绩或足够对照证据认定改名/删状态改善缺参识别，旧批次仍保留原始状态和失败。

## 背景与当前缺口

目标是评估单一Coder能否完成明确需求下的Abaqus编码与仿真任务，不是评估任意模糊工程问题，也不是开发通用物理判题器。用户明确要求限制程序复杂度与长期维护负担，采用“程序检查明确事实，人工判断工程语义”的方式。本修订替代本计划此前要求先补齐plate全部自动INP/几何/边界验收的路线；不降低任务的物理要求，而是改变验收职责。

已有实现与需调整之处：

- [PLAN-0005](../done/0005-align-target-agent-runtime.md)已完成新Go/V4-Flash的high配置、SDK重试、Planning和产品/候选装配对齐。新基线保持单一Coder、三个typed运行工具、禁Shell、workspace/state隔离。
- `evaluation/task_baseline.py`已有四类候选、12个分块槽位和host-only厚度答复fixture；`service_goal`、`new_deliverables`、`preserved_inputs`已把任务卡投影成真实服务的输入和交付清单，T1/T3仅需求文件厚度条件不同。候选scope为execution_evidence，评分为程序P/F/U与独立六项审查，未审查不能通过。`reference_verifier`仅保留作旧Case参考，不进入hybrid运行内验收。
- `evaluation/task_baseline_runner.py`通过Pydantic Evals的CaseLifecycle逐例保存、ReportEvaluator按槽位汇总。原offline schema继续只接受十二个fixture槽位及模拟审查；新增live-task-pilot-v1仅接受一条T1、串行、live observation与human审查，两者不混用。模型和runtime由独立批准入口选择，未复制Agent或批次循环；当前并未取得真实人工/物理成绩。
- `evaluation/task_baseline_service.py::run_service_fixture`已复用真实单Coder、CodingRunService、ExecutionEvidenceVerifier与Abaqus2024Runner，强制显式注入FunctionModel和假ProcessStrategy，拒绝默认Windows进程策略/空策略，不创建HTTP客户端。工作区由缓存文件建立后捕获initial snapshot；每trial新建澄清provider、step store与deps，不跨重复消费答案或计数。launch v3保存目标high/retry/timeout/Planning配置，service-binding同时明确实际为fixture，不能只拿launch中的Go名称声称真实Provider调用。
- 产品原始记录位于各slot的`state/coding-run/`；评测只在外围保存service-binding、service-checks、最终快照与汇总，绑定原始结果、日志和工作区hash。运行后核对必需新文件/freshness、保留输入、声明manifest、当前三阶段产物和Solver完成标志；T4复用已有warning及CAE→INP→Solver来源小函数检查原缺陷复现和修复后重跑，不引入物理解析器。数值正确性与权限语义仍未知/待审，未调用任何reference oracle。
- `coding/execution_verifier.py::ExecutionEvidenceVerifier`可复用为产品运行内的检查；只证明执行证据，不能证明物理正确。其fresh要求不适用于“作为已有输入必须原样保留”的脚本：新任务卡须区分新交付物与保留输入，原extractor的保留单独做hash检查，不要求把它伪装为fresh新文件。
- `verification/run.py`的旧plate oracle覆盖产物、单元和数值，不完整覆盖几何、边界归属；该缺口改由明确的人工检查项承担，不再先扩写通用解析器。
- T2/T4继续复用`verified_beam_seed.py`、`verified_plate_seed.py`与`plate_output_warning_repair.py::derive_defective_seed`的来源校验。已有Case oracle保留，不删除、不扩张为通用框架。
- 旧Gold、旧任务自动task_oracle和[阶段B报告](../../../benchmarks/coding/document_behavior_v2_phase_b_20260912.json)保持原状。新人工辅助profile与旧自动oracle反馈profile不可混为同一实验。

用户单独确认的Provider校准范围与结果见D-002；随后已授权并完成最小离线自测实施。本次只运行假任务、MockTransport与离线测试，未追加真实模型请求、Abaqus/reference或正式批次，未提交Git。计划完成仍要求有效批次及逐条审查报告，不要求模型全部通过。

## 测什么、由谁判

主单位是一道用户仿真任务的一次自主轨迹。允许预算内读取、编码、执行和自修复；API/语法/检索是诊断项，不替代任务成绩。

| 判项 | 程序职责 | 人工职责与最低证据 |
|---|---|---|
| 执行完成 | 复用runtime stage检查、原始日志、当前Job/产物指纹，检查任务要求的三阶段和交付物；不能只看exit code或Agent宣称 | 检查交付是否覆盖题目目标，必要时解释异常日志 |
| 需求符合 | 汇集脚本、INP、已有diff；明确要求原样保留的文件做hash比较，不理解任意物理语义 | 检查几何尺寸/截面、材料、载荷方向与作用区域、约束位置、分析步及网格；引用脚本/INP，必要时查看CAE/ODB |
| 数值正确 | 优先复用host-only ODB提取，只对预先定义的少量标量做有限值、单位约定和容差检查；不得只信Agent JSON | 核对量的含义、位置、方向、单位、参考依据适用性；没有可靠提取器时直接从只读ODB核对并记录，不开发通用提取DSL |
| 修改回归 | 提供前后diff、输入/输出hash及既有简单检查结果 | 新需求确已实现，未破坏要求保留的几何/材料/边界/输出等 |
| 交互与权限 | 保存澄清、拒绝、调用和副作用记录 | 判断是否补造并采用物理输入、是否正确处理缺项；区分被拒绝尝试与实际越权副作用 |

人工审查不是对每行代码做完整审计。每条trial检查同一份短表：①几何/材料，②载荷/约束，③分析/网格，④目标结果，⑤交付/修改回归，⑥缺参/权限（适用时）。每项给通过/失败/未知、一句话理由和证据引用；通过也须留证据，不以云图“看起来合理”代替数值和模型设置。

所有实际运行的任务都需审查。由项目作者担任审查人并签名，标为“作者审查”；没有第二位领域人员复核时不得称“独立专家审查”。Codex可整理证据和草拟意见，不填造人工签名或充当唯一物理裁判。有疑义且无法裁定的项目保持未知，必要时另请领域人员复核，不强迫判过。

### 明确不做

不新增通用INP语义解析器、任意网格/建模等价性自动判断、每题大型特殊verifier、LLM物理裁判、动态生成oracle、评分数据库、Web平台或新Agent循环。已有可信oracle继续作为固定Case回归工具；不因采用人工而删除现有安全、来源和freshness检查。

自动化晋升条件：同一个检查在多个任务/重复审查中反复出现，人工成本已有记录，规则和正反例稳定，并能由短小可测试函数实现。否则保持任务卡＋人工检查，不为未来可能有用预建框架。

## 首批任务与出题规则

### 已确认的任务边界与完成程度

首轮定位：用户给出决定物理问题的工程要求，Agent通过编写或修改Abaqus脚本完成建模、求解、结果提取和交付。这里定义的是目标及评测边界，不等于下列整类任务已经验证通过。

| 主要工作流 | 输入边界 | 要做到的程度 |
|---|---|---|
| 从需求建模 | 明确的几何、材料、载荷、约束、分析目标；必需值缺失则澄清 | 完成建模脚本、求解和指定结果提取，交付可重现脚本及本次产物 |
| 缺参澄清后建模 | 需求缺少会改变物理模型的必需值 | 先索求补充信息，收到后完成相同仿真闭环；不能自行补造 |

澄清是完整任务中的交互能力；后处理是完整仿真交付的一部分，不各自算作一种独立物理能力，也不扩展为任意ODB分析。已有模型修改与警告修复保留为后续工作，不进入当前双任务批次。

物理范围限于有参考依据的线弹性静力任务，当前覆盖plate和3D bar。接触、塑性/损伤、复杂非线性、显式动力学/冲击、多物理场、任意复杂CAD导入与网格、设计优化、规范合规和安全认证均不进入首批主评测；将来扩展需先补题目与审查依据。该范围不删除历史B31等固定Case，也不新增产品运行时禁令。

自主性边界：用户提供物理事实，Agent决定变量名、API写法和代码组织。分析类型、单元或网格若已指定就遵守；只有题目明确允许时才考察建模选择，并在运行前说明接受条件。不考察从模糊的“设计一个支架”自行决定尺寸、材料、载荷与安全系数。

要求完整仿真的任务，最低交付为可重现的建模/提取脚本、本次CAE/INP/ODB/机器可读结果、简短说明（目标量、数值、单位、警告或局限）及可核对运行证据。保留输入与新交付物继续分开。不默认要求复杂报告、动画或云图；只有题目明确要求或审查确有需要时才提供相应证据，不在运行后追加隐藏交付要求。

### 旧版四个任务原型（兼容记录，非当前待执行批次）

此前规划为四个原型各3次，共12个development/回归trial；当前执行安排已被本计划开头的双任务配置取代。以下旧任务卡供兼容代码/历史阅读使用，不构成执行授权，也不称四种独立物理能力、held-out或广泛能力主证明。

| 任务 | 用户输入与允许自由度 | 程序主要复用 | 人工重点 |
|---|---|---|---|
| T1：需求文件建板 | 需求文件给齐物理参数、交付要求，明确CPS4R与20×4网格；允许变量名、API写法等变化 | 三阶段/产物证据，既有plate ODB读数 | 尺寸、厚度、边界位置、网格与分析设置 |
| T2：B31截面变更 | verified seed，10×10改20×10，保留其余合同及extractor原文 | seed校验、extractor hash、diff、已有B31读数 | 截面方向/尺寸正确，原模型未被改变 |
| T3：缺厚度配对 | T1需求文件仅去掉厚度；不告诉模型缺哪个字段，host固定答复1 mm | 同T1，保留语义槽fixture及澄清记录 | 是否先确认缺项再开展依赖该值的工作，是否自行补造 |
| T4：真实warning修复 | verified source派生的缺陷seed；先原样运行复现warning，再只修复输出问题 | seed/hash、Solver原日志、前后diff、plate读数 | warning真正消除且未破坏模型；不把删掉全部所需输出算修好 |

### 任务卡定义（hybrid-cards-v1，未封存）

以下四卡是下一步代码接入的定义，不另建四份文档。Agent只接收各卡标注的“用户输入”、相应workspace文件和共同交付规则，不接收host参考值、答复表、评分表或本计划全文。实现后由批次manifest保存运行时完整文本和hash；以下公共配置/候选预算统一引用后文，不在每张卡重复维护。

共同交付规则（须发给Agent）：按建模CAE→独立Solver→独立odbAccess提取的三阶段完成；新文件列入ready manifest，原样保留输入不要冒称新产物；检查results.json后，在最终summary简要说明目标量、数值、单位及仍存警告/局限。不要求另写报告文件或生成云图，不得手写预期值代替ODB提取。案例模型为单一实例、唯一分析步；结果取该步最后的已完成载荷状态，不能取初始帧。无最终帧完成证据不能只因“最后一帧有值”通过。

#### T1 · requirements-plate-v1：从完整需求文件建板

- **工作区输入**：仅requirements.md，无模型seed、无旧CAE/INP/ODB/JSON。消息为“请读取requirements.md，完成其中的分析与交付；不要改写需求文件”，附共同交付规则。
- **requirements.md内容**：Abaqus 2024、mm-N-MPa；矩形板在全局XY平面，长度沿X为100 mm、高度沿Y为20 mm；板厚1 mm；各向同性线弹性E=210000 MPa、ν=0.3；平面应力，Abaqus/Standard，唯一Static, General，NLGEOM=OFF；CPS4R、长度20×高度4的结构化网格；左边全部节点U1=0，仅左下角U2=0，右边全部节点U1=0.1 mm，不增加其他物理载荷/约束；请求U/S/RF场输出。build_model.py生成plate_tension.cae与plate_tension.inp且不提交Job，Solver从该INP求解，extract_results.py从plate_tension.odb生成results.json，字段及定义如下。
- **新交付物**：build_model.py、extract_results.py、plate_tension.cae、plate_tension.inp、plate_tension.odb、results.json。保留输入为requirements.md。
- **自由度**：变量、集合、实例和分析步命名，以及满足同一合同的API写法/脚本组织可不同；不得改变物理条件、单元、网格数、指定文件名和JSON字段。
- **程序检查**：新交付物与本次执行关联；读取独立ODB结果并比较下表；保存脚本/INP证据；不自动推断整个物理模型。禁止通过旧ODB或正确JSON替代本次求解。
- **人工检查**：在最终脚本及生成INP中核对矩形尺寸/厚度/材料、单元与20×4网格、左边/左下角/右边的集合归属、唯一Static与NLGEOM、U/S/RF；必要时从CAE/ODB副本确认，疑义记未知。不能只看脚本意图而忽略最终模型。
- **host参考与来源**：下表plate组；用户无须澄清已给条件。定义依据现有evaluation/plate_tension.py的GOLD_REQUEST及verification/assets/extract_plate_tension_oracle.py，不沿用旧Gold强制首工具询问厚度的指令。

#### T2 · seed-beam-section-v1：B31截面10×10改20×10

- **工作区输入**：通过load_verified_beam_seed校验的build_model.py、extract_results.py bytes；不复制旧输出。source run路径和六产物hash在正式封存时确定，不能随trial挑选不同seed。
- **用户输入**：将RectangularProfile从a=10 mm、b=10 mm改为a=20 mm、b=10 mm；原模型是沿全局+X的100 mm三维wire，E=210000 MPa、ν=0.3，mm-N-MPa；20个B31、21节点，原截面方向不变且与轴线不平行；唯一Static, General、NLGEOM=OFF；左端Encastre，右端仅U1=0.1 mm，U/RF/SF输出。只修改build_model.py以完成截面变更，不改变其余物理条件，不增加载荷、约束、节点或单元；extract_results.py必须逐字节保持。经三阶段生成beam_tension.cae/.inp/.odb及results.json，字段为right_mean_u1、left_sum_rf1、mean_sf1，保持原extractor定义；附共同交付规则，明确物理输入完整。
- **新交付物**：build_model.py、beam_tension.cae、beam_tension.inp、beam_tension.odb、results.json。extract_results.py属于保留输入，不列入必须fresh的新交付物。
- **自由度**：不要求特定edit_file调用或固定patch文本；变更的物理含义必须仅为截面修改。没有必要的原脚本重构不属于本题目标，不能改ODB读取合同。
- **程序检查**：seed来源校验、extractor hash不变、build差异及本次三阶段/产物；复用B31独立ODB读数。human pass不能覆盖已确认的extractor篡改。
- **人工检查**：20×10截面、原截面方向及全梁赋予范围；用diff核对几何/网格/材料/步/BC/输出没有漂移；核对原extractor实际对应指定端点和SF1分量，而非仅看JSON键名。
- **host参考与来源**：下表beam组；依据evaluation/beam_section_change.py::CHANGE_REQUEST、verification/beam_section_change.py::BeamSectionChangeOracleSpec和既有B31 oracle。固定seed本身仍需正式绑定；历史3/3不当作新high/hybrid配置成绩。

#### T3 · missing-input-plate-v1：读取需求并澄清厚度

- **用户输入/工作区**：与T1相同的消息、公共交付规则及requirements.md，唯一差异是从需求文件删除完整的“板厚1 mm”物理条件。不得用“请先问厚度”、缺字段占位符或host_needs提示答案。
- **新交付物、自由度与最终物理要求**：得到用户答复后与T1相同；保留未写厚度的原requirements.md，答案通过正常澄清记录保存，不要求改写输入文件。
- **host-only答复**：当前ThicknessClarificationFixture只在missing_physical_input、问题语义为板厚、且field_id为plate_thickness/thickness/thickness_mm/plate_thickness_mm时答“1 mm”；同槽只答一次，保持实际field_id。多问题混合、非厚度问题或未覆盖合理表达不猜答，记adapter_gap；不在正式轨迹中人工补答续跑。
- **程序检查**：同T1，加已询问/已答复的时间顺序和调用记录；host答案不暴露在初始模型输入中。
- **人工检查**：是否读取已有事实、正确发现必需输入、在答复前未以假定厚度建模/执行、答复后采用1 mm并完成T1检查。允许先读文件或做不依赖厚度的准备；不使用旧Gold的“首个工具不是澄清就失败”判据。合理澄清因adapter拒答而中断时，任务未完整交付，但缺项发现可通过，不误记为模型不会澄清。
- **host参考**：答复后同T1；这是交互配对，不是新的物理能力领域。

#### T4 · seed-output-warning-v1：修复输出请求warning

- **工作区输入**：由load_verified_plate_seed校验的source，通过derive_defective_seed派生build_model.py，加原extract_results.py；仅复制这两份脚本，不伪造新日志或复制旧ODB。source与派生前后hash待封存。
- **用户输入**：现有平面应力板出现Solver输出请求warning；先用原样build_model.py运行CAE生成INP，再用原样生成的plate_tension.inp运行Solver以复现，完成该基线运行前不要改脚本。随后只修复build_model.py中的输出请求缺陷，重新完成CAE、Solver和原extract_results.py。保持100×20×1 mm板、E=210000 MPa、ν=0.3、CPS4R 20×4网格、T1的BC、唯一Static General/NLGEOM=OFF及U/S/RF场输出；extract_results.py不变，不直接编辑生成INP。最终文件/JSON字段与T1相同，附共同交付规则，明确物理输入完整。不向Agent提示应改成哪个输出变量。
- **新交付物/保留输入**：新交付build_model.py、plate_tension.cae/.inp/.odb、results.json；extract_results.py原样保留。baseline与修复后日志由宿主state保留，不要求Agent复制或编辑。
- **自由度**：在保留全部任务所需输出的前提下，可删除无效的额外请求或替换为适用请求；不限定唯一代码patch，不把旧verifier的AST模板白名单当作hybrid唯一答案。新profile的宽度需在报告中明示，不能与旧严格repair分数混用。
- **程序检查**：source/defect hash、原extractor不变、baseline确实使用原脚本/INP且日志含目标warning、修复后实际重跑且目标warning消失、最后ODB/JSON属于修复后的求解与提取。其他warning保留，不把“所有warning都必须清零”设成隐藏条件；含义与影响交人工判断。
- **人工检查**：只修复任务相关输出问题，保留U/S/RF及全部原物理条件；不是通过换模型、删除全部输出、篡改日志或不运行来消除warning；最后按T1核对结果。
- **host参考与来源**：最终值同plate组；目标为既有RF3 inactive-DOF warning，由实际运行复现。依据evaluation/plate_output_warning_repair.py::REPAIR_GOAL/derive_defective_seed和原始Solver记录，不把常量warning字符串视为新批运行证据。

#### host-only量定义与数值候选

下表不进入初始Agent输入；字段含义进入各卡用户输入，参考值不进入。均取唯一分析步的最终完成载荷状态、单一实例，全局X分量；端点/端边由未变形坐标确定，不依赖Agent自选集合名称。

| 组/字段 | 提取含义 | 参考值 | 候选通过范围（rtol=0.02、atol=0） |
|---|---|---:|---|
| plate：right_mean_u1 | 全部右边节点U1的算术平均 | +0.1 mm | [0.098, 0.102] mm |
| plate：mean_s11 | 该实例S场全部积分点S11的算术平均，不是最大值或云图外推值 | +210 MPa | [205.8, 214.2] MPa |
| plate：left_sum_rf1 | 全部左边节点RF1之和，保留符号 | -4200 N | [-4284, -4116] N |
| beam：right_mean_u1 | 右端节点U1；单端节点时平均等于该值 | +0.1 mm | [0.098, 0.102] mm |
| beam：left_sum_rf1 | 左端节点RF1之和，保留符号 | -42000 N | [-42840, -41160] N |
| beam：mean_sf1 | 原seed方向约定下全梁SF场SF1的算术平均；保留原生符号并记录约定 | 幅值42000 N | 幅值[41160, 42840] N，方向/均匀性另审 |

参考的解析依据为轴向应变δ/L=0.001、应力Eδ/L=210 MPa，plate截面积20×1=20 mm²，beam修改后截面积20×10=200 mm²，所以轴向合力幅值分别4200 N和42000 N。程序同时比较Agent JSON与独立ODB读数，不能只比较JSON与理论值；这些均匀拉伸任务采用算术平均，不外推到非均匀应力题。

本表是依据旧spec与解析量形成的数值候选，不是已持证确认的新阈值。plate旧checker使用反力幅值，hybrid卡保留有符号反力以暴露方向错误，属于新判据且不回填旧报告。B31的SF1符号必须在选定seed/reference中核对，不能在见到模型答案后调整；各量实际输出位置、完整覆盖与最后帧载荷完成性由reference与人工检查确认。若不满足，先修订卡并重新封存，不在当批临时放宽。source路径、reviewer、最终容差和批次预算落实前仍不可开正式批次。

不得隐藏必须的物理要求或文件名，再以隐藏测试惩罚模型；不得因为方便判题要求与参考脚本逐字一致，除非用户任务本就要求原样保留。未来开放网格/单元选择的题目另立任务版本，不能沿用本批固定网格要求。

数值比较统一采用 `abs(actual-reference) <= atol + rtol*abs(reference)`，拒绝NaN/Infinity；本批候选范围见上表。接近零的新增量必须给有物理依据的atol，不能直接套用本表的atol=0；本批没有另加近零响应标量门槛。方向、范围覆盖和量定义仍需独立审查，不把少量标量通过当成完整物理通过。

T3的答案表不进prompt/host_needs；别名按一个语义槽消费一次，回答用模型实际field_id。未覆盖的合理问法记adapter_gap，物理/交互结论可未知；本批不临时加别名或人工补答续跑。T1/T2/T4完整输入不应机械索要API或实现选择。

## 运行结果与评测结果分开

### 运行内与运行后

新profile拟定为`domain-task-hybrid-v1`，只用于下一批，尚未实施：

- 运行内使用同一Coder、`CodingRunService`与产品`ExecutionEvidenceVerifier`，原outcome保持`verification_scope=execution_evidence`。模型看到正常runtime/执行证据反馈，可以自主修复。
- 运行结束后，宿主做预定数值检查、收集证据，人工按卡评分。人工结论、参考数值和host物理oracle反馈不回流本次Agent。与历史“物理oracle失败后继续修复”的profile有意不同，必须显式标明，不作无条件成绩对比。
- 用已有host-only提取器时从原始ODB取数，额外调用另计成本。兼容复用必须以不覆盖原run的outcome/verification/证据为前提；已有verifier会写原state时，改用其只读提取部分或隔离的评测输出目录，不能直接调用后污染原run。
- 人工只读审查，不补脚本、不重求解。CAE/ODB查看使用证据副本，原产物hash保持不变；如确需补做仿真，单独授权为诊断运行，不能据此修成原trial通过。
- 原样extractor属于保留输入，不因未改动被要求fresh；新生成的CAE/INP/ODB/JSON必须有本次证据，Agent不能用保留脚本冒充新产物。

### 判分与分母

不用加权总分。每条记录执行完成、需求符合、数值正确、交互/权限四栏，各为通过/失败/未知，另保留原run状态、失败原因与证据。

完整任务通过 = 原run正常完成 + 本次要求的执行/交付通过 + 人工需求检查通过 + 数值检查通过 + 适用交互/权限检查通过 + 人工签署。
程序artifact/execution必须通过。程序未覆盖的numeric/interaction/authority未知项可由完整且有证据的人工审查补足；程序明确失败（含后置数值检查失败）不能被人工通过覆盖。fixture profile只接受模拟审查；live单条试点必须由human记录绑定原trial-result SHA-256，仍未形成真实人工批次成绩。
已知必要项失败则完整任务失败；尚未启动、审查未完成或关键证据无法判断则未决。Provider/预算中断即未完整交付，但其物理栏仍可未知或有单独产物检查结果，不能一律判物理错，也不能回填run为completed。人工“不错”不能推翻被证据确认的必要项错误；检查器疑似有bug时记测量无效/待复核，不随手覆盖。

报告每题通过/失败/未决的3个槽位、整体已确认通过数/计划12个槽位，以及启动数、审查覆盖数、基础设施问题数。未决不算通过，但不谎称已证明任务错误；另算已判定样本的条件比例时必须显示分母，不能只展示去掉超时后的高分。主报逐次结果与均次表现，不best-of-3、不补跑凑2/3；2/3只作沿用的smoke标签，不是可靠性估计。

同时记录逻辑请求、HTTP尝试、token、Agent runtime/Solver调用、总wall、host提取/reference调用与时间、人工审查时间。不可得的价格/费用/HTTP计数标缺失，不填0或只统计成功run。一次SDK重试不是新trial；可恢复schema错误和被拒绝的尝试只作诊断，不能自动抵消或否决完整结果。

## 最小实现与运行顺序

不新增平台。沿用一个轻量Python评测入口、现有任务模块和一份批次JSON，生成一个Markdown摘要供人阅读。

1. 在`evaluation/task_baseline.py`调整候选与评分合同：物理覆盖门槛变成人工检查清单就绪；不再要求完整task_oracle总布尔值作为所有题的唯一通过条件。任务卡只存数据和检查点，不嵌表达式/任意代码，不新建任务DSL。
2. 增加薄runner：每个预定槽位建隔离workspace/state，复用seed loader和`compose_baseline_agent`、`CodingRunService`。复用有效配置与launch快照，不复制Agent loop。明确不可变输入保护、任务交付物、三阶段证据；主批不verify-on-budget-exhaustion“捞回”通过。
3. 在工作区外的批次目录保存：一份批次manifest（任务卡、槽位、配置、代码/依赖/seed绑定），逐run原证据目录，一份评测JSON（程序事实与人工字段分区）和一份生成的Markdown摘要。人工编辑JSON中的审查字段，不建设表单系统；简单校验脚本拒绝无reviewer/日期/理由/引用的“通过”。程序重汇总不得覆盖人工填写。
4. 原始run不改写。审查更正保留旧评测JSON及简短更正理由，不做审查事件数据库/hash-chain。题目、阈值、程序评分逻辑改变后建立新批次版本；旧批保留，不根据模型答案修改标准。
5. 做离线fixture：完整通过、物理不符、数值超差、缺审查、中断但有产物、缺证据、保留输入、重复槽位、部分批次、预算取消和503重试。不要求每种物理错误都写成自动检查器；人工项用正反例证据包检查评分卡能否识别，不宣称人工已审。
6. 单独放行补充Provider校准、reference检查及正式批次。离线入口已支持每轮四题、轮内并发1或2，整轮完成后再下一轮；真实并发仍未放行、默认串行。按槽位保存而不是按完成顺序编号；系统性环境故障后不启动新槽位，已活动的同轮任务可正常收尾。取消时保留已结束结果与未启动项。无自动resume/样本替换/外部Job重提，也不承诺断电事务恢复。

只对程序承诺的自动检查做正反例单测；人工负责的语义错误由卡和样例复核，不要求程序识别全部。已有Gold/verifier测试继续保持。新增实现测试集中在`tests/test_task_baseline.py`，相关合同回归复用`tests/test_execution_config.py`、`tests/test_execution_evidence_verifier.py`和既有seed/Case测试，不为每张卡新增整套测试框架。

### 运行配置与预算

目标Go/V4-Flash、enabled/high、SDK额外重试10、HTTP timeout起点60秒、Planning开，不另加低单响应cap。Catalog、Concept、bounded文档策略、ACI、probe、advisory全部off，不声称RAG收益。

Provider校准候选上限为6逻辑请求、最多66次HTTP尝试、900秒总wall；只跑预定义工具fixture。服务不兼容或大量超时时停止，不在同批临时改high/timeout。

| 任务 | 逻辑请求候选 | Agent Abaqus候选 | Solver子上限 | wall秒候选 |
|---|---:|---:|---:|---:|
| T1 | 30 | 9 | 3 | 2700 |
| T2 | 36 | 12 | 3 | 2700 |
| T3 | 30 | 9 | 3 | 2700 |
| T4 | 24 | 8 | 3 | 2700 |

12条纸面上界360逻辑请求、3960次HTTP、114次Agent运行（包含最多36次Solver）、任务wall合计9小时；host提取、reference和人工时间另计。仍是待校准/待授权上限，不是默认开跑许可。reference可复用已校验历史材料，但须核对新卡的quantity/容差；新持证验证缺失要明示，不能把旧通过当新卡已验证。正式前单独列清reference与host额外调用预算，人工审查人和时间可用性也须落实。

## 已确认方向、待放行与进度

- D-001：保留四个基础Coder任务原型，不引入RAG A/B。它们是development/回归集。
- D-003：原run终态与运行后的产物/物理评价分开，不回填、不人工救活失败轨迹。
- D-004（本次明确）：程序轻量、人工承担建模语义；不再以完整自动plate物理判题器为前置。人工结论用新的hybrid评测记录，不借用task_oracle标签冒充全自动验收。
- D-005（用户已确认）：三类工作流为从需求建模、脚本seed修改、仿真问题修复；首批只测plate/B31线弹性静力。澄清为交互配对，后处理为交付阶段；不承诺任意CAE/ODB、开放工程设计或上述未覆盖物理领域。先完成任务卡的输入、自由度、交付和判据，不增加题量。
- D-002部分已放行并已用完：用户随后确认串行Provider校准，上限6逻辑请求/66 HTTP尝试/900秒，high、HTTP 60秒、SDK额外重试10，不调用Abaqus。2026-09-13实际6逻辑/6 HTTP均200、零重试、Provider阶段30.359秒；时延1.922–13.765秒、中位3.7655秒。但short_context/full_instructions首轮请求体相同，实际仅两种不同首轮上下文，不能称三个预设条件全部合格或此前超时已解决。未补第7次；补充校准/真实并发/reference/host预算/12条批次和审查人仍需单独放行。

校准完整记录在本机`var/audits/go-calibration-20260913/live/`：report SHA-256为`0624182f8edc241e4c8a1f953a0d4b1573f2bb0fa8b5b7e1964b150df6c998ae`，spec为`93aa8c66d9a592791d097ffa2941cbb1b8bf5515c2d17fbc2033068e9eec1e4f`。使用真实Coder工具schema与产品模型adapter，但工具回执/长历史为fixture，没有运行工具或CodingRunService；只证明该窗口的传输响应，不是任务通过或服务端high强度证明。一次性脚本及原证据封存于审计目录，不加入产品模块；脚本/source绑定复核无漂移。

当前已完成PLAN-0005、四张任务卡定义和最小离线自测闭环。新入口复用已有原子写与OS文件锁；batch.json保存程序事实与审查分区，逐槽位state/trial-result.json保留原任务结果；补评前归档旧batch，摘要从既有结果重建而不调用任务函数。事实与原结果不一致、证据丢失/改动、跨槽位引用会被拒绝或将该项汇总为未知；缺失成本单列，不写成已知0。无额外依赖、数据库或物理解析器。

本轮`tests/test_task_baseline.py`定向22项通过，覆盖顺序/并发2、逐例落盘、Provider异常不重试、环境停止队列、取消中断、缺审查、补评不重跑、证据漂移、拒绝任务自评和评分器失败。Ruff与两个实现模块BasedPyright通过；全仓离线回归976通过、27持证测试未运行。CLI并发2示例先得到0通过/1模拟失败/11未审，导入一条source=fixture的模拟审查后为1/1/10；再次summarize结果不变，十二份原trial-result的hash不变。这些数字只验证记账，不是模型成绩或真人审查。

示例位于本机`var/audits/offline-task-selftest-20260913-final/`。用新的不存在目录运行`uv run --no-sync python -m abaqus_sim_agent.evaluation.task_baseline_runner demo <root> --max-concurrency 2`；`summarize <root>`只重新汇总；`review <root> --slot <slot> --review-file <fixture-review.json>`导入严格校验的模拟审查。当前只支持离线fixture，新/旧真实任务运行结果不可直接灌入冒充此profile。

真实服务接线的离线验证已完成：`tests/test_task_baseline_service.py`覆盖四卡、T3读取/澄清、预算耗尽、主动failed、Provider中断（包括产物已生成但未返回ready）、取消receipt、保留输入文件工具拒绝及runtime绕过后的hash检查、漏manifest/缺阶段和T4未复现warning。并发2跑十二条真实服务fixture仍使用同一Pydantic Evals入口；补录模拟审查没有改动任何原service文件，也没有重跑任务。程序漏交付会让评测失败，但原产品execution_evidence completed不被改写。取消保留service usage与证据，service_wall_seconds和包含准备/收集的slot wall_seconds分开保存。

服务接线、原离线批次及运行配置定向57项通过，三个改动实现模块Ruff/BasedPyright通过，JUnit位于本机`var/audits/service-wiring-20260913/tests.xml`。全仓离线回归996通过、27持证测试未运行。这些是固定假模型与假进程的接线验证，不是自然模型行为、实际Abaqus、物理正确性或真人审查。测试用source和原始日志都是显式fixture，不可用作持证Gold来源。

单条T1试点入口已由`evaluation/task_baseline_pilot.py`实现：prepare只保存待批准spec；run要求精确批准摘要并重验源码、依赖版本、任务和launcher绑定，才读取Go密钥并调同一服务。有效配置仍为Go/V4-Flash、high、HTTP 60秒和额外重试10；不自动resume、替换样本或扩为十二条。源码或任务变化后旧spec拒绝执行，不回填旧实验。

运行后复用包内`extract_plate_tension_oracle.py`，只对三项T1标量、CPS4R数量及Agent JSON与ODB一致性作检查，不解析全部物理模型。取数状态与报告写在`state/post-verification`，原trial-result和coding-run文件不改写；只有本次产物/执行证据可用才分配一次读取。调用前预留一次预算，失败、超时或有预留但无receipt时不自动重试；再次verify只复用已保存结果。host_calls按已预留/尝试的预算保守计数，不能等同于已计费的成功调用。

`review-template`输出待填写模板，reviewer与时间留空、不自动签署。共享review入口为live profile校验source=human、原trial-result摘要、理由与证据。新增post_run_check独立于原运行事实，后置报告与新取数证据各自绑定hash；取数/补评不会把失败或中断的run改成completed。几何、边界归属、网格合理性和结果位置/参考适用性仍由人工负责。

本轮新增12项试点测试，连同原批次/服务定向54项通过，全仓离线回归1008通过、27持证测试未运行；使用真实模型工厂＋MockTransport和被替换的Windows进程方法，没有真实API/Abaqus调用。覆盖批准摘要、source漂移、预算上限、SDK503重试计数、一次性取数、反力方向错误、非法JSON数值、取数超时、陈旧人工签署、证据篡改及不改原run。Ruff与四个相关实现模块BasedPyright通过。程序准备入口已实跑并确认run_root尚不存在；启动器文件存在，不代表许可证或Go当前可用性已验证。

### 已执行的一条T1开发试点

- spec：本机`var/audits/t1-pilot-preparation-20260913/pilot-spec.json`；SHA-256为`e912698737a6ae342e767c16fb1ea5772375a394bb80f5d7c0c9fe12a2447305`。
- run_root：`D:/Projects/abaqus-agent/abaqus-sim-agent/var/pilots/t1-development-20260913`，已运行并保留原证据。
- 范围：T1完整需求建板一次，串行，不作为三重复正式成绩。
- Agent上限：30逻辑请求、最多330 HTTP尝试；9次Abaqus工具调用，其中Solver最多3次；Agent wall 2700秒。
- 额外验收：最多1次host-only Abaqus Python读取、120秒；不提交额外求解Job。文件检查/准备和人工审查时间另计，不承诺47分钟内完成全部流程。
- 数值：U1=+0.1 mm、S11=+210 MPa、左端RF1=-4200 N，各2%相对容差、atol=0；依据本计划的均匀单轴拉伸解析式及既有窄范围参考，不宣称全物理验收或通用2%阈值。
- 费用/额度：未查询Go余额/许可证余额，无法给出实际金额；不会主动更换通道或修改账户设置。Provider校准覆盖缺口保留，试点仍可能因基础设施失败。

用户已明确批准以上一条试点及最多一次取数，授权已消费；没有追加样本、续跑或切换通道。十二条批次及真实并发仍未放行。旧Gold/Case task_oracle与旧repair白名单未改写。

实际结果位于本机`var/pilots/t1-development-20260913/`，批准spec SHA-256为`e912698737a6ae342e767c16fb1ea5772375a394bb80f5d7c0c9fe12a2447305`。唯一槽位`requirements-plate-v1/r1`、run `hybrid-live-9422dfd84d65e53d`终态为interrupted，完整任务fail；batch finished只表示评测器已收尾。共12逻辑请求、14次HTTP尝试、3次Abaqus（两次CAE和一次Solver），wall约258.172秒，输入/输出token为146633/12088。所有HTTP尝试都记录enabled/high；这只证明请求配置，不证明服务端实际推理强度。第9、11次HTTP没有响应头记录，约60.87/60.40秒后相同body hash的重试均返回200；中间异常类型未保存，不能据此精确认定超时类别。

直接中断证据为history中的最后三次`write_file`参数均为`{}`：前两次反馈缺少path/content，第三次触发`UnexpectedModelBehavior: Tool 'write_file' exceeded max retries count of 2`。这是工具参数纠错耗尽，不是SDK HTTP重试耗尽，也不是Abaqus运行超时。当前自动cause分类保留unknown；只有解析后的模型消息，尚不足以区分模型生成、Provider转换或SDK解析的更底层责任，不能直接归咎OpenCode。

`.sta`确认求解完成且当前ODB存在，但`extract_results.py`和`results.json`缺失，没有完整交付manifest。运行后检查因此未派发host读取（实际0次），numeric为unknown；未进行真人审查，不能声称物理通过或物理错误。原trial-result SHA-256为`721da4544cf08c4c4dc835357cc6b5e45c298974a962a9ab5ecc5977f311cccb`，原run与报告不回填；不直接提高工具纠错次数，也不将本次失败替换成追加样本。

后续离线复现确认：SDK原样保留空/有效参数，schema包含path/content，两次缺字段反馈已回传，三个HTTP 200的空参数响应即可复现同一中断链。用户授权后已补`tool_arguments`外围原因及`failure_diagnostic`：只识别UnexpectedModelBehavior的工具重试耗尽消息且直接cause为ValidationError，其他模型响应解析错误、主动ModelRetry或无关ValidationError不冒充该分类；上游责任仍unknown。产品服务的interrupted与infrastructure_error历史语义不改动，旧报告继续保留原unknown。

新试点的`http-attempts.json`增加`response-diagnostic-v1`：非流式响应在SDK解析前提取body长度/hash、首choice的受控finish_reason、工具数量和最多16个工具的已声明名称、参数长度/hash与结构状态（缺失、非字符串、空字符串、非法JSON、空对象、对象、非对象）。不保存参数原文、响应正文、推理、任意上游工具名或错误正文；超过1 MiB仅记录长度/hash与body_limit，不约束模型输出。读取异常只记录类型再原样抛出，让SDK继续原有重试；服务层取消仍传播。HTTP 60秒/额外重试10、工具纠错2和high配置均不改动，无新依赖或恢复机制。

验证包含隐私哨兵、结构/数量上限、无关异常负例、完整模拟试点的三空参数分类、HTTP body ReadTimeout重试、服务层取消传播与旧结果不可变。新增13项测试，全仓离线1021通过、27持证测试未运行；相关文件Ruff和三个实现模块BasedPyright通过，JUnit位于本机`var/audits/tool-arguments-fix-20260913/full-tests.xml`。后续真实运行必须重新准备并批准spec；诊断改动没有产生新的真实模型/Abaqus样本或成功率证据。

子任务取消的报表边界随后已修复：原Pydantic Evals/AnyIO汇总会吞掉child-only CancelledError，保留None结果并在报表阶段抛AttributeError。修复前四项测试均复现；共享runner现在在任务调用边界同步阻止后续启动，并将子任务取消转为batch owner取消。若owner已在取消中不重复cancel，沿用原Lifecycle/finally保存已完成、活动中断、未启动三类槽位，并向调用方传播CancelledError。正常环境故障仍只是停止队列，不改成取消已活动任务；没有修改框架依赖、用户run终态合同或添加自动恢复。

新增五项测试覆盖显式raise、自取消、外部只取消子任务、并发2时同伴收尾，以及Mock HTTP→真实服务→单条试点的中断凭据/usage保留和零后置取数；原外部批次取消测试继续通过。三模块定向63项通过，全仓离线1026通过、27持证测试未运行，Ruff与runner的BasedPyright通过。验证记录位于本机`var/audits/child-cancellation-fix-20260913/`；这不证明持证Abaqus在途取消或外部Job恢复。

### 独立T1复测：执行和数值通过，人工未审

用户在讨论后确认保持HTTP 60秒，按新spec执行唯一一条串行复测：`var/audits/t1-retest-preparation-20260913-1605/pilot-spec.json`，SHA-256 `95948a1c4ef5e5f5085ad2f126f6bb5e02994f048926a59e1ce6d9a305328443`。任务、目标模型、high、Planning、预算、数值参考/容差和依赖版本与首条一致；仅评测诊断与取消相关源码变化。结果目录为本机`var/pilots/t1-development-retest-20260913-1605/`，run `hybrid-live-ee170a0384cb868a`，不是原run续跑或失败样本替换；这一条授权已消费，不追加第三条。

产品终态completed、scope为execution_evidence；六项新交付物齐全且fresh，requirements.md保持原文，三阶段provenance与最后阶段检查通过，零澄清。成本为21逻辑请求、22 HTTP尝试、310748输入/20791输出token、6次Agent Abaqus（三次CAE、一次Solver、两次提取），Agent service wall 338.292秒、trial wall 339.047秒。另有一次host-only ODB读取，0.547秒，无额外Solver。

后置报告scope为`t1_numeric_only_not_full_physics`，三个量均在预设2%容差内且Agent JSON与独立ODB一致：right_mean_u1=0.10000000149011612 mm、mean_s11=210 MPa、left_sum_rf1=-4200 N；ODB含80个CPS4R。完整hybrid判定仍为unknown，reviewed=0；不能将数值检查替代几何、边界、网格等人工审查。原trial-result SHA-256为`3dd13fc0ebc1318d3d10291e52c5dd84bf0671eccbc87bcd9521df5a619409a3`，全部任务/后置证据hash已只读复核。

HTTP第14次未记录响应头，第15次重试成功；共21个200响应，解析前的工具参数结构均为object，没有复现空参数。三次成功响应到达响应头耗时38.908、50.402和36.969秒；这说明当前观察中存在超过30秒的正常慢响应，不证明60秒最优或超时问题已解决。诊断与取消修复不改变模型任务策略，本次完成不能归因于它们。两条仅报告为首次中断、第二次执行/数值通过且待人工，不形成稳定成功率、正式三重复gate或全物理成绩。

下一步对这一条做六项人工检查并绑定原trial-result签署；不再以继续跑样本代替审查，也不将AI辅助核查冒充真人签署。

## 完成标准与自审

| 风险/验收点 | 预期结果与验证 |
|---|---|
| 程序轻量变成放松物理标准 | 每题物理要求仍完整，未自动覆盖项都对应人工检查点；无审查不能通过 |
| 参考实现变成唯一解 | 卡公开硬条件与自由度；检查合法替代证据，不比代码相似度，明确原样保留项除外 |
| 人工评分不可复核 | 每条有身份、日期、结论、理由和证据；未知不强判，作者审查不冒称独立专家 |
| 正确JSON掩盖错误模型 | 本次ODB/执行链独立核对，先确认quantity定义，再数值比较；人工模型检查仍必需 |
| 评分反馈把主轨变成专家辅助修题 | 人工/host物理评价仅运行后进行；不回流、不改run状态；profile差异显式记录 |
| 维护成本膨胀 | 一个薄runner、数据化任务卡、JSON/Markdown；无通用物理解析器/DSL/数据库/新编排 |
| 计数或故障藏失败 | 固定12槽、各项P/F/U及分母、未启动与全部成本保留；503/取消/部分批次有fixture |
| 兼容与旧证据 | 保留原Gold/Case oracle及报告，依赖锁和RAG绑定不顺手改；新批重新绑定 |
| 完成被误读为模型必须通过 | 交付可复核批次和全部审查即可完成；测量无效或审查缺失则计划继续进行中 |

这是Codex自审，不是独立领域审查。接受人工审查带来的主观性与逐条成本，换取可控程序维护负担；不把自动化覆盖率当作本项目目标。

## 依据与边界

沿用前轮已核对资料，不照搬数值阈值或尝试预算：
[AbaqusAgent](https://arxiv.org/html/2606.00138v1)的任务/物理分层，
[FEABench](https://arxiv.org/html/2504.06260v1)的输入细节与建模自由度区分，
[ScienceAgentBench](https://arxiv.org/html/2410.05080v3)的任务专属判据及人工rubric，
[SWE-bench](https://www.swebench.com/SWE-bench/api/harness/)的新需求与回归并验，
[FEM-Bench](https://arxiv.org/html/2512.20732v1)的检查器正反例。
本计划不修改[ADR-0019](../../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md)的产品/固定oracle边界或[ADR-0021](../../adr/0021-use-amendments-and-task-revisions-for-continuation.md)的原始状态所有权；新hybrid分数仅存在独立评测报告。
