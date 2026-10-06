# Coding 评测入口

[所有测评入口](../README.md)包含Skill、文档行为、检索/API等专项。

## 领域仿真

- [简短案例清单](domain_mechanics_inventory.md)：20个公开案例按物理能力组织，经典、Ansys、NAFEMS仅作来源标签。
- [15道计分题已有结果](basic_linear_results_overview.md)：一页历史概览，区分正式运行、事后结果和证据缺口；不是当前协议的新成绩。
- [合同与证据详表](domain_mechanics_case_details.md)：物理判据、逐题证据、当前预算和资源条件。
- 当前入口：`python -m abaqus_sim_agent.evaluation.public_mechanics_suite --help`。

新批次使用 `public-mechanics-e2e-v29-declared-results`，`prepare`默认选已确认的15题：均布梁、圆轴、厚壁圆筒、孔板、VM184及NAFEMS LE1–LE10。
数值评分比较模型提交值与隐藏参考值，不再运行独立ODB读取或强制JSON与另一套读数一致；实际三阶段执行与交付证据保留。
题设不提供参考答案；首次提交固定留证，最多两轮反馈、三次提交，首次与最终自动评分分别报告。
新批次拒绝未经答案内容审查的外部参考附件，累计预算与恢复沿用同一CodingRun；详见[结果报告约定](result_reporting.md)。
全部20题仍保留定义；`--case`可指定子集或历史/未入选题。历史及LE11可以产生诊断记录，但不增加评分分母。
`manifest.case_ids`是实际运行选择，`manifest.scoring_case_ids`冻结15题评分范围；报告按两者交集逐物理Case计数。
自定义子集的`full_set_selected=false`，不能把子集结果称为完整15题成绩；VM184三工况仍只计一道题。

真实Case由原生Dataset组织，task调用现有Service并返回验收证据；`TaskPassed(Evaluator)`与`SuiteSummary(ReportEvaluator)`产生结果。
运行中由原生CaseLifecycle更新progress.json/progress.md，完整报告在调用结束时生成。
独立report只读重建，不重跑业务；执行档案保留该次评分错误，不读取reviews文件或要求逐项人工确认。
通过范围为执行、交付及提交数值，不扩大为全部建模细节已核实。规则、原生报告、审查记录及重建方式见[结果报告约定](result_reporting.md)。
新worker的`run/continue/resume/report/launch --phoenix`可显式记录真实attempt并上传已算评分，默认关闭；
报告重建不产生新的仿真追踪，详见[Phoenix接线](result_reporting.md#phoenix追踪与评分)。

## Metadata约定

[CaseMetadata / SourceRef](../../src/abaqus_sim_agent/evaluation/case_metadata.py)使用严格、禁止额外字段的Pydantic模型。
`MechanicsCase.metadata`是唯一维护位置；不保留第二份顶层`source`，也不把已有`family`手工重复为标签。

| 字段 | 含义与校验 |
|---|---|
| `metadata.description` | 测评目的；去首尾空白后非空。原`MechanicsCase.description`继续描述物理题设 |
| `metadata.tags` | `CapabilityTag`小词表；至少一项、不得重复。Python构造使用tuple，JSON中为array；标签不决定计分 |
| `metadata.source.kind` | `project_analytic`、`ansys`或`nafems` |
| `metadata.source.reference` | 非空的理论依据或外部题号/引文 |
| `metadata.source.url` | HTTP(S)链接；Ansys/NAFEMS必须填写，项目解析题可为空 |
| `metadata.source.document_version` | 可选的非空文档版本，不是Abaqus运行版本 |

`adaptation`含建模限制和取数要求，继续留在物理Case；`metrics`继续拥有目标和容差，Evaluator不解析元数据描述。
构造时立即验证；批次加载通过`TypeAdapter(list[MechanicsCase]).validate_json()`统一验证，拒绝错误类型、未知字段/标签和无效来源。
`prepare`将元数据随物理合同保存进`cases.json`并绑定原始文件摘要。JSON/Markdown报告读取该批快照，并核对Case与manifest的摘要及选择一致性，
不从今天的Case注册表回填说明；元数据损坏或旧协议均拒绝覆盖报告。旧批次使用原worker，不自动迁移或补填字段。

端到端描述运行范围；按物理Case计数，输入变体不增加领域仿真题权重。旧plate/bar/B31 Gold的独立入口和专属测试已退出；冻结报告、请求和参考脚本保留，用于解释历史，不作为当前成绩。
参考脚本、假模型工程测试、真实Agent正式结果、事后恢复分开报告；v29自动通过表示实际执行与提交数值指标通过，不表示独立核实完整模型。

## 修改、修复与行为

- [plate修改suite](plate_model_change_suite_evaluation_20260825.json)、[梁截面修改](beam_section_change_evaluation_20260827.json)、
  [梁弯曲合同](beam_bending_convergence_evaluation_20260827.json)：逐Case验收。继续消费经过原合同/hash校验的已有种子，无法通过已删除的Gold驱动新建种子批次。
- [API修复](api_repair_regression_evaluation_20260824.json)、[runtime feedback repair](runtime_feedback_repair_protocol_v2.md)：固定修复任务，独立于从零仿真。
- [力学行为题](foundation_public_mechanics_protocol_v1.md)、[Skill routing](skill_routing_protocol_v1.md)：行为测评，不计入领域仿真完成率。
- [产品执行smoke](product_open_cube_execution_evidence_20260831.json)、[会话恢复](coding_run_resume_provider_smoke_20260902.json)：产品执行与状态边界，不是物理验收。

Skill专项的三份入口共用[skill_observation.py](../../src/abaqus_sim_agent/evaluation/skill_observation.py)读取既有history/analysis/outcome。
`read_skill_evidence`提取事实，`score_skill_route`只计算两套路由测评的加载判据；各入口的`observe_case`保留原输出字段与本族参数。
Foundation行为测评仍将语义结论留给独立审查，不把路由分数当作工程正确性。新批次的实现hash包含共享模块；旧批次不回填。

线性基础与结构专题两组Skill路由的新运行版本为`*-skill-routing-v2-native-evals`：
各自的`run_evaluation`构造Dataset，以单题task调用原有CodingRunService；
[skill_routing_evals.py](../../src/abaqus_sim_agent/evaluation/skill_routing_evals.py)提供共用Evaluator和ReportEvaluator。
`pydantic-evals-report.json`保存原生输出、断言、失败及汇总，原`report.json`的统计字段读取同一原生汇总。
路由通过只判断加载集合，不表示工程回答正确；Service异常仍按原合同写入观察后继续下一题，
准备/读取失败停止后续业务调用。异常时可保存原生诊断报告，未完成环境绑定核对时标为false，不生成成功completion。
模型client在构造Agent/Service失败时也会关闭；任务和评分自动重试均关闭。

这一接线已用确定性模型经过真实Service及文件工具验证；检索直接作为task、力学执行器作为task也有独立受控测试。
完整力学入口已迁移至v26原生Dataset；检索生产入口仍保持原状。Foundation行为审查保持独立，
不据离线路由测试宣称真实模型或物理收益。

相关运行仍可能需要本机Catalog、知识快照、许可证或原始种子。历史报告按各自冻结合同解释，不作为当前版本成功率。
旧Gold冻结报告和请求仍保留在本目录；原执行实现可在Git历史基线`aa7116f`查阅，不再提供独立运行入口。

## 命令与Python实现

公开力学的参数解析/分发位于[public_mechanics_cli.py](../../src/abaqus_sim_agent/evaluation/public_mechanics_cli.py)，
实际prepare/run/report/pause等操作仍由[public_mechanics_suite.py](../../src/abaqus_sim_agent/evaluation/public_mechanics_suite.py)负责。
API修复同样由[api_repair_cli.py](../../src/abaqus_sim_agent/evaluation/api_repair_cli.py)解析参数，调用
[api_repair.py](../../src/abaqus_sim_agent/evaluation/api_repair.py)的原生Dataset执行和评分函数。
原来的`python -m ...public_mechanics_suite`、`python -m ...api_repair`和worker导入的`main()`保持可用，转发到唯一命令实现。

此分离只改变代码职责；选题、默认参数、退出码和冻结校验保持原语义。直接调用Python操作也须满足对应批次绑定，
不能从活动checkout绕过public mechanics的snapshot校验。

两组Skill路由原生入口（`skill_routing_behavior`、`structural_topic_skill_routing`）将内部单题取消传播到本次批次调用，
关闭当前client后退出，不启动下一题，也不写成功完成标记；外部批次取消保持同样的收尾边界。
这是对Pydantic Evals 2.51子任务取消行为的公开接口适配，不是自动重试或新增恢复机制。
