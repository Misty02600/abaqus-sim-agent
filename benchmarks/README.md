# 测评与证据入口

测评实现主要在`src/abaqus_sim_agent/evaluation/`，本目录保存题目、协议和冻结报告。
部分API实验脚本仍绑定历史配置与本机资源；它们的位置不表示可以用当前默认配置直接复跑。

## 按测量对象查入口

下表的模块名均以`abaqus_sim_agent.evaluation.`为前缀，可用`python -m <模块名> --help`查看参数。
这里列的是代码入口；实际执行可能需要模型凭据、许可证、冻结资源或匹配的原环境，`--help`本身不启动测评。

| 测量对象 | 模块入口 | 题目、方法与证据 |
|---|---|---|
| 完整力学仿真 | `public_mechanics_suite` | [Coding入口](coding/README.md)、[15题历史结果](coding/basic_linear_results_overview.md)；已有批次仍使用其冻结worker |
| API修复、静态反馈帮助修复 | `api_repair`、`runtime_feedback_repair` | [修复协议](coding/runtime_feedback_repair_protocol_v2.md)；修复任务不计入15题从零仿真成绩 |
| Skill选择 | `skill_routing_behavior`、`structural_topic_skill_routing` | [基础路由](coding/skill_routing_protocol_v1.md)、[专题路由](coding/structural_topic_skill_routing_protocol_v1.md)；加载正确不等于工程答复正确 |
| Skill使用效果 | `foundation_skill_behavior`；审查入口`foundation_skill_behavior_audit` | [行为协议](coding/foundation_skill_behavior_protocol_v1.md)；保留独立语义审查 |
| 文档使用行为 | `docs_tool_behavior`、`document_policy_behavior` | [证据层级与历史结果](../docs/architecture/verification-evidence.md#api文档与专业知识)；受控证据回放不等于真实检索 |
| 检索、重排与证据交付 | `rag_evidence_baseline_cli`；其他检索命令见右侧入口 | [RAG入口](rag/README.md)；只测检索链时不调用完整仿真任务 |
| 简单建模与任务基线 | `simple_modeling_batch`、`task_baseline_pilot` | [Coding入口](coding/README.md)；`task_baseline_runner`的CLI仅用于离线自测/审查 |
| API意图与发现历史实验 | 保留`api_catalog/`中的原脚本 | [意图实验](api_catalog/intent_suite_expansion_20260914.md)、[发现实验](api_catalog/api_discovery_loop_v1_20260915.md)；不是新增的通用产品命令 |

## 代码与结果的边界

- 参数解析和命令分发可以放在独立`*_cli.py`，执行和评分函数留在各自模块。现有`public_mechanics_suite`及`api_repair`命令保留为薄转发，外部调用路径不变。
- 对应题目数据、原报告和seal保留原值；整理入口不改变冻结运行协议，也不删除批次worker或源码快照。
- 输出位置由各入口原有的`--root`、`--run-root`或`--output`指定，可以是项目内被忽略的目录或项目外目录；不以目录名判断测评类别。
- 工程回归继续在`tests/`；Pydantic Evals生成评测结果，Phoenix保存可查询的追踪与评分副本。[如何解释结果](../docs/architecture/verification-evidence.md)
