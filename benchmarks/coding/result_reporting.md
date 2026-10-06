# 基础线性评测结果与自定义规则

新批次协议为`public-mechanics-e2e-v29-declared-results`。当前Pydantic Evals 2.51.0组织真实Case执行、单题评分和整批汇总；
现有CodingRunService、外部执行、物理verifier和恢复策略继续拥有原始运行事实。

## 首次提交与有限反馈

模型题设保留完整物理输入、交付物、取值位置/分量/单位、壳表面、平均或外推方式，以及自主网格收敛检查要求。
参考答案、相对答案的偏差和官方样例结果只留在评测侧。v29不再附加外部整包参考目录：入口拒绝
`--knowledge-resources`，避免把未经答案内容审查的文件传给模型；内置领域Skill及API Catalog继续使用。
公开题仍可能被模型从训练或其他资料获知，此合同不保证答案绝对未知。

每次有效`ready_for_verification`完成声明先在`state/submissions/001`等独立目录保存
`submission.json`（声明、累计用量、文件摘要）和`workspace/`字节副本，再验证实际建模、求解、提取与交付物的执行证据，并比较提交值与隐藏参考值及容差。
缺失交付也计一次提交。`result.json`保存完整内部评分，评分异常另留`error.json`；后续修复不覆盖前次目录。
反馈只生成受控的失败指标或交付检查名称，原诊断文本不进入模型消息。宿主将实际写入history的反馈记为
`verification_feedback`事件；报告据此统计反馈轮数。

首次失败后最多两轮反馈，最多三次提交；通过、第三次失败、累计预算耗尽或模型明确失败时停止。
评分器异常归为`evaluation_error`，不邀请模型修复，也不记成答案错误。
提交前自调试不计反馈次数，所有阶段共用200次模型请求、80次Abaqus调用（含至多10次Solver）和5400秒活动时间。
这些均沿用同一CodingRun的累计预算。普通产品没有开启提交留证回调和有限反馈控制，验收行为不变。

恢复继续读取原提交序列、预算和基线。快照复制或评分中断、反馈检查点未完成、提交文件损坏时拒绝自动续跑，
不会根据修复后的工作区补造首次成绩，也不自动重新执行独立评分；该边界不承诺跨文件事务恢复。
每次提交复制实际工作区，因此大型ODB会增加磁盘和复制时间；副本不使用会随原文件修改的硬链接。

原生报告和派生报告分别保存`first_submission_status`、`final_submission_status`、`submission_count`、
`feedback_rounds`和逐提交累计`submission_usage`。状态为`passed`、`failed`、`not_submitted`或
`evaluation_error`；未评分的中断提交属于测评未完成/错误，不计答案失败。原生断言
`first_submission_pass`与`final_submission_pass`记录自动评分，`task_pass`只合并实际执行/交付与提交数值检查，不设置人工需求审查门槛。
每项通过率以计划中的物理Case为分母，每题只占一个权重；修复轮次、评分错误和未交付不增加题目数。
首次通过则最终同为通过；从未声明完成明确显示未交付。预算耗尽后不额外验收以制造一次首次提交。
新合同只进入新批次，既有v26 worker、workspace、state、报告及80/30预算均保持原值。

## v29声明结果、数值评分与通过范围

`verification_policy=declared_results_v1`只比较本次`results.json`中有限、非布尔的数值与隐藏参考值及容差。
验收记录用`reported`保存实际评分值；原生报告的相对误差也来自这些提交值。缺项、字符串、布尔、NaN或无穷大均为提交失败。
新入口不再运行另一套ODB提取程序，不比较JSON与独立读数，不要求单实例、精确取值节点或指定的提取API。
已无其他活动消费者的公开力学独立读取器、有界INP检查及重复数值比较逻辑撤除；旧冻结worker保留其自身副本。

执行层继续核对非空交付声明、fresh文件、Python 3.10语法、最后一次已用runtime阶段、当前CAE/INP/ODB/JSON的执行顺序和hash来源、
Solver完成状态，以及CAE不生成Solver产物。该层复用产品`ExecutionEvidenceVerifier`和既有逐阶段provenance，不创建另一套作业或评分框架。
题设继续明确几何、材料、载荷、约束和目标取值，模型自主完成；测评不解析INP核实这些建模细节，也不转交为逐项人工确认。仅凭执行记录和提交数值不能排除硬编码或提取错误。
旧自动geometry/material/EVOL identity检查也随读取器撤除，不把这些能力继续算作新自动评分。新协议使用`numeric_ok`表示提交值的数值判定，不表示独立复测ODB或核实全部物理建模。

孔板和实体悬臂继续要求粗细两套INP/ODB和两次Solver执行。顶层指标来自细网格；`coarse_metrics`对象分别保存粗网格的
`hole_max_s11`或`tip_u1_x/tip_u2_y/tip_u3_z`。提取脚本须从各自ODB取值，宿主按提交的同名指标检查相对细网格变化不超过1%；
未交粗网格数值不能仅凭第二个文件名通过。模型须说明两套取值方法一致，几何与物理条件不变。

15题逐项保留位置、分量、单位和物理面：LE1/LE10保留D点全局S22，LE2/7/8/9保留物理外表面及相应方向，LE4/5保留壳中面，
LE6保留板中心下表面面内最大主应力，并从同一位置的应力张量计算；厚壁圆筒保留r-z面积加权平均；孔板保留孔壁S11最大值，
实体梁保留自由端面各工况方向位移最大值，梁/轴保留由截面合力恢复名义应力。允许合理插值、外推及坐标转换，不能改成附近峰值或Mises。
壳表面需按实际法向和截面积分映射，不强制SPOS、固定Gauss点数或一套外推算法。逐题合同见[案例详表](domain_mechanics_case_details.md)。

v27的首次留证、最多两轮反馈、累计预算和未完成检查点拒绝恢复规则全部沿用；新的结果声明合同只用于v29新批次，v26/v27/v28历史成绩不回填。

### 自选作业名与结果声明

继续使用ReadyForVerificationDecision.deliverables，每项包含relative_path，可选role。力学测评必须明确唯一primary ODB；粗细网格题另声明唯一coarse ODB。例如：

```json
{"deliverables": [{"relative_path": "beam_final.odb", "role": "primary"}, {"relative_path": "beam_check.odb", "role": "coarse"}]}
```

以上仅展示ODB角色，实际提交仍包括其余要求的文件。model.cae、build_model.py、extract_results.py、results.json和report.md仍按题设交付；本项放开的是Job及其INP/ODB名称。主结果用于顶层指标，粗结果用于coarse_metrics。普通产品允许省略role，不额外增加验收条件；角色随原提交声明持久化，不建立第二套映射清单。

从声明ODB寻找成功Solver记录，依据实际job=和input=参数核对当前INP指纹、ODB与该Job的STA产物，再核对CAE生成来源、完成状态及之后的JSON提取执行。输入名不必等于Job名，STA按Job定位。角色缺失、重复、不是ODB或没有匹配执行均拒绝，不根据fine字样、大小或时间自动选择。

PublicMechanicsRunner对工作区INP动态记录前后状态，Solver同时记录ODB和STA，保留同字节重写的证据。来源链证明对应产物和执行顺序，不宣称已监控任意Python实际打开了哪个ODB或排除硬编码；v28取消独立ODB读数的边界继续成立。CAE历史文件删除误判是另一项待修问题，本次未改变其规则。

## 结果文件

| 文件 | 用途 |
|---|---|
| `report.json` / `report.md` | 批次运行状态、计分范围、任务结论、现有usage和元数据导航 |
| `pydantic-evals-report.json` | 原生`EvaluationReport`，含assertions、scores、labels、metrics、理由和analyses |
| `pydantic-evals-report.txt` | 原生报告的可读视图；关闭默认断言平均值和投影耗时，展示自定义汇总 |
| `progress.json` / `progress.md` | 原生Case生命周期发布的最新逐题进度；包含处理中、已处理、中断及已有判定，不是最终成绩或恢复状态 |

`run/continue/resume`通过原生Dataset调用现有单题执行器，task返回持久证据的Observation；
TaskPassed和SuiteSummary直接对这次结果评分。完成后报告接收原生结果，不重新评分掩盖evaluator_failures。
`report`则只读取已存证据，用同一组评分器重建；不会调用Agent、独立ODB reader或Solver。
两条路径均为`repeat=1`，不启用task/evaluator自动重试。

`report.json.native_result_kind`区分`case_execution`和`saved_result_projection`。
原生Case的`attributes.duration_scope`进一步区分实际执行加证据读取，以及跳过/已完成Case的只读投影。
`task_duration/total_duration`不能替代Agent活动时间；后者仍读`metrics.persisted_active_wall_seconds`。
框架`report.failures`表示task适配/证据投影异常；Service已保存的Provider中断仍来自`run_status/failure_kind`。

每次Dataset正常返回时，完整原生结果同时保存在`control/evaluation-<invocation_id>.json`，保留该次评分错误及恢复范围。
它是不可复用的结果档案，不是恢复状态机。根目录报告是当前派生视图，显式report可读取已有运行记录重新生成；
不得拿后来的重建结果覆盖原执行档案来隐藏当时的评分失败。

## 异常留存

正常run结束时保存每个Case完成时的原生评分快照。v29不读取reviews文件，不产生pending_review或requirements_ok，不要求逐项人工确认INP解析规则。
已有审查文件保留原字节，但不影响本批计分或Phoenix反馈。

任务适配或单题评分异常通过CaseLifecycle立即追加到已有`control/events.jsonl`的`case_evaluation_error`事件，
保留Case、调用ID、阶段及错误摘要，不保存完整框架检查点。根目录报告的`evaluation_errors`及“已记录的评测异常”
展示这些历史事实；取消后没有完整原生执行报告时，已有错误也不会随内存结果消失。

没有宿主运行证据但已有任务适配错误时，报告显示setup_error而非not_started；之后出现有效Service结果时，
以该结果描述当前运行，旧错误仍留在历史。只读重建的原生report.failures反映这一次投影的异常，不等于历史没有错误。
事件日志不重建Agent会话，也不推断未决Solver是否执行；恢复仍由Service决定。错误证据追加失败会显式传播，
不能伪称已留存；派生progress文件写入失败仍只警告，不影响业务或评分。

## 执行与中断边界

新协议的Case并发由`Dataset.evaluate(max_concurrency=...)`管理，Abaqus runtime仍由独立semaphore限流。
默认两者均为1，现有可选上限2不变。并发2时改为按空闲Case槽位补题，区别于旧协议等待整波完成；
该调度变化只进入v26新批次，不回填旧批次或用旧worker混跑。

任务在观察到Provider/setup/证据基础设施异常后设置停止状态并使health失效；未启动题只读已有证据，
不创建业务工作区、不调用模型或Solver；已在执行的题自然收尾。普通task_failed继续下一题。
显式pause仍走已有标记与Service取消路径。单题取消传播到批次owner，外部批次取消同样等待在途任务清理；
这是公开asyncio取消组合，没有修改Evals内部实现或增加会话恢复状态机。

run只能启动新批次；continue只执行从未启动题；resume只接受原入口允许的中断Case，继承Service预算和基线。
每次Dataset仍包含原冻结全题集，非本次执行范围只读原证据，因此恢复子集不会缩小评分分母。
采用原生并发调度、CaseLifecycle逐题进度、最终EvaluationReport的组合：setup标记Case处理中，
teardown直接使用原生ReportCase/ReportCaseFailure及已有判定更新`progress.json/progress.md`。
处理数量包含实际执行和只读投影，不等于通过题数；未启动、缺失判定和取消不补造通过或物理失败。
取消传播时SDK可能已进入下一Case的生命周期，但业务停止检查仍在副作用前执行；进度阶段不证明模型或Solver已经启动。

启动时不重建完整报告或上传旧评分。新批次在本次调用结束时生成完整报告；continue/resume期间保留上次报告，收尾再更新。
运行中查看progress文件，也可查逐题state、attempt、stdout和Phoenix。
进度只作派生展示，不重新评分、不接管Service状态、不创建另一套调度或trace；写入失败警告后业务继续。
独立report重建不更新这份运行进度。进程强杀后进度可能停留在处理中，恢复判断必须读取Service证据。

取消导致Dataset未返回完整报告时，宿主持久事实仍在，收尾尝试只读重建全题集；后续也可独立运行report。
进程强杀或磁盘错误不能保证finally成功，不据此承诺自动恢复Python协程或外部Solver exactly-once。
绑定变化时报告按invalid_evidence处理；传入原生执行报告却与当前执行证据不一致时显式拒绝，不能静默套用旧分数。

## 单题规则

[TaskPassed](../../src/abaqus_sim_agent/evaluation/mechanics_grading.py)实现`Evaluator`：

- `execution_ok`来自已有verifier的执行/交付阶段结果；缺少验收记录时未知。
- `numeric_ok`来自提交值与冻结数值目标的检查，不宣称覆盖全部建模语义。
- 只有原run为completed、执行/交付与数值检查均通过、已有自动验收通过且仍在正式合同内，才产生`task_pass=True`。
- 明确的任务失败产生`task_pass=False`。未运行、中断和缺失判定不伪填为数值失败；没有布尔判定时用label保留状态。
- 有amendment/task revision或超过冻结显式resume次数的结果标为posthoc，不用它回填正式成绩；正常的预算内resume仍可保持正式资格。
- 绑定改变、记录损坏、scope/policy不符标为invalid_evidence。单题评分器异常由原生evaluator_failures记录。

宿主运行分类原先把completed命名为formal_pass，新报告改为`completed`；`task_verdict`另行表达真正的评测结论。
原始outcome、验收事件、工作区、raw日志和旧报告不会因此被改写。

## 整批规则

`SuiteSummary`实现`ReportEvaluator`，用`ScalarResult`和`TableResult`返回统计。

- 计划分母为冻结`manifest.case_ids`与`manifest.scoring_case_ids`的交集，一题一票；VM184三工况合计一道题。
- 通过率为已确认通过数/计划题数，未运行、中断、证据异常、评分异常和posthoc记录不从分母消失。
- 同时显示各状态数量；未开始或中断的题目不伪填为已证实的数值失败。
- 历史/未入选题可以显示诊断结果，但不计入主统计；没有计分题时不生成通过率，避免除零或虚构0%/100%。
- 自定义子集仍用`full_set_selected=false`标注，不能冒充完整15题结果。
- 资源表只合计已记录数值，并报告有值的题数；未知费用不填0，活动时间之和不是并发批次总耗时。
- 不把`report.averages().assertions`作为任务通过率，也不按断言数量或标签计算权重。

使用该批次原worker的`report --root <ROOT>`命令可重建视图。新worker拒绝覆盖v27及更早报告，
新判定语义需新批次，不能对旧成绩自动升级。离线接口测试通过不代表15题真实Agent/物理验收通过。

## Phoenix追踪与评分

新准备批次的冻结 worker 支持以下显式入口；先按[README](../../README.md#按需查看本地-phoenix-trace)启动本机服务：

```powershell
uv run --no-sync python D:/abaqus-runs/my-batch/worker.py run --root D:/abaqus-runs/my-batch --phoenix
uv run --no-sync python D:/abaqus-runs/my-batch/worker.py resume --root D:/abaqus-runs/my-batch --case circular-shaft-pure-torsion --phoenix
uv run --no-sync python D:/abaqus-runs/my-batch/worker.py report --root D:/abaqus-runs/my-batch --phoenix
```

`continue`同样支持；后台`launch --action run|continue|resume --phoenix`将开关传给实际worker，
启动器自身不初始化 exporter。`prepare/preflight/doctor/pause`不接受该开关。
开关属于本次宿主命令，不写入冻结任务配置，恢复不会根据旧追踪记录自动开启。
旧冻结worker不因当前代码更新获得此能力，也不修改旧seal或报告。

每次实际`_run_case`运行建立独立`evaluation.case` span，记录批次、Case、attempt及已有run ID；
`session.id`沿用run ID。并发Case互相隔离；允许的resume另开追踪，原attempt保留。
它覆盖真实执行及收尾，含排队与工具等待，不能当成Agent active wall；后者仍读保存的usage指标。
报告中的原生Case trace/span ID仍指向实际attempt；task duration按duration_scope解释，report重建仍只是投影耗时。

`<ROOT>/telemetry/<case_id>/<attempt>.json`保存本机collector、trace/span ID，以及attempt、outcome、
resume_state和验收事件文件的摘要。这是可缺失的观测关联，不属于运行ledger，也不参与计分。
报告只关联最新attempt且摘要匹配的记录；未采集的恢复、证据变化、关联损坏或collector地址改变时跳过上传。
缺少历史追踪时不会补造运行。外部审查文件不参与本协议的评分和追踪关联。

先保存全部本地报告，再复用Phoenix client上传已有断言、数值误差和状态；同一span、名称及identifier更新
同一条annotation，重复生成报告不会新增一次运行。对应证据或数值缺失时，受管评分字段更新为unknown，
不遗留旧的通过/数值副本；原始报告不会为了上传而补造断言。未开启`--phoenix`时只生成本地报告。

服务不可用、上传失败或关联文件写入失败不改变outcome及计分；没有可靠补传队列。服务恢复后可显式重建报告，
但实际运行的span若未送达，不保证仅重传评分就能恢复追踪。Phoenix里的旧attempt评分仍表示其当时状态，
不能按所有attempt的annotation数量计算物理题通过率。

以下接收验证与v26工程验证使用当时的人工审查计分语义；v28不沿用该审查门槛，历史证据不回填。
既有[2026-09-29本机接收验证](../observability/public_mechanics_phoenix_20260929.json)通过实际`_run_case`入口与报告桥接，
以离线Service/模型/runtime样例产生两题、一次中断后恢复，共3条Trace、6个span、40条annotation。
重复报告保持数量不变；加入审查可更新通过评分，撤回后score变为null、label变为unknown，REST读回6个span。
仅验证本机接收与评分更新；未运行真实模型/Abaqus，未开展本轮浏览器交互或服务重启测试。

## v26工程接入验证

2026-10-02：168项相关离线测试通过，Ruff与BasedPyright通过。运行入口测试覆盖原生执行/评分、
Case与runtime并发分离、Provider失败后停止新题、同run恢复及continue不重做完成题、评分错误保留、
单题与批次取消后的全题集报告、追踪关联和旧协议拒绝。原单题执行器、恢复选择、题设和TaskPassed的AST保持不变；汇总判定读取抽取为共用函数，原判分语义由回归覆盖。
额外验证了批次运行中逐题进度可见、report不伪造运行进度，以及进度写入失败不改变任务或评分。
这是代码接线和故障边界验证；未运行v26的真实模型/Abaqus批次，也未重做上述历史Phoenix后端接收实验。

组合故障修复另外覆盖：运行中新增/撤回合法审查、真正验收/用量漂移仍拒绝、任务或评分异常后再取消、
随后恢复另一题或重新执行成功时仍保留历史错误。修复只使用现有报告字段扩展和批次事件日志，没有新增恢复系统。
