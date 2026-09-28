# Coding 评测入口

## 领域仿真

- [简短案例清单](domain_mechanics_inventory.md)：20个公开案例按物理能力组织，经典、Ansys、NAFEMS仅作来源标签。
- [15道计分题已有结果](basic_linear_results_overview.md)：一页历史概览，区分正式运行、事后结果和证据缺口；不是v25新成绩。
- [合同与证据详表](domain_mechanics_case_details.md)：物理判据、逐题证据、当前预算和资源条件。
- 当前入口：`python -m abaqus_sim_agent.evaluation.public_mechanics_suite --help`。

新批次使用 `public-mechanics-e2e-v25-native-result-grading`，`prepare`默认选已确认的15题：均布梁、圆轴、厚壁圆筒、孔板、VM184及NAFEMS LE1–LE10。
全部20题仍保留定义；`--case`可指定子集或历史/未入选题。历史及LE11可以产生诊断记录，但不增加评分分母。
`manifest.case_ids`是实际运行选择，`manifest.scoring_case_ids`冻结15题评分范围；报告按两者交集逐物理Case计数。
自定义子集的`full_set_selected=false`，不能把子集结果称为完整15题成绩；VM184三工况仍只计一道题。

结果通过Pydantic Evals的`TaskPassed(Evaluator)`与`SuiteSummary(ReportEvaluator)`生成；缺失需求审查时保留待审，
不把自动oracle通过扩大为完整任务通过。规则、原生报告、审查记录及重建方式见[结果报告约定](result_reporting.md)。

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
参考脚本、假模型工程测试、真实Agent正式结果、事后恢复分开报告；results-first自动通过不替代完整规格审查。

## 修改、修复与行为

- [plate修改suite](plate_model_change_suite_evaluation_20260825.json)、[梁截面修改](beam_section_change_evaluation_20260827.json)、
  [梁弯曲合同](beam_bending_convergence_evaluation_20260827.json)：逐Case验收。继续消费经过原合同/hash校验的已有种子，无法通过已删除的Gold驱动新建种子批次。
- [API修复](api_repair_regression_evaluation_20260824.json)、[runtime feedback repair](runtime_feedback_repair_protocol_v2.md)：固定修复任务，独立于从零仿真。
- [力学行为题](foundation_public_mechanics_protocol_v1.md)、[Skill routing](skill_routing_protocol_v1.md)：行为测评，不计入领域仿真完成率。
- [产品执行smoke](product_open_cube_execution_evidence_20260831.json)、[会话恢复](coding_run_resume_provider_smoke_20260902.json)：产品执行与状态边界，不是物理验收。

相关运行仍可能需要本机Catalog、知识快照、许可证或原始种子。历史报告按各自冻结合同解释，不作为当前版本成功率。
旧Gold专属记录可在Git历史基线`aa7116f`查阅，不再由本目录提供独立运行入口。
