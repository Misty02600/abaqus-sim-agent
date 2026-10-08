# 测评与证据入口

测评实现主要在`src/abaqus_sim_agent/evaluation/`，本目录保存题目、协议和冻结报告。
当前维护通用测评入口；已结束的API开发实验与Foundation配对试验只保留题目、协议和冻结证据。

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

## 已退出维护的实验代码

API候选内容审计、意图改写、语义发现查证、历史失败题包生成和整任务API效率观察的10个专用脚本，
以及Foundation旧配对pilot/follow-up的驱动、题集装配、审计、trace汇总和prepare脚本，已从当前树删除。
它们的专属工程测试随实现移除；当前Skill路由、Foundation行为评测和API修复继续维护。
原Catalog多子命令CLI及BM25/E5意图检索实现、停用词资源与专属测试也已删除；资源构建仅保留
`abaqus-sim-agent-catalog`命令，直接调用[构建函数](../src/abaqus_sim_agent/api_catalog/build.py)，
精确查询、产品对象方法发现和离线Catalog校验函数保留。

这些实现可从Git基线`9d95aea`按原路径查阅，例如：

```powershell
git show 9d95aea:benchmarks/api_catalog/run_intent_suite.py
git show 9d95aea:src/abaqus_sim_agent/evaluation/foundation_followup_v2.py
```

[意图实验](api_catalog/intent_suite_expansion_20260914.md)、[发现实验](api_catalog/api_discovery_loop_v1_20260915.md)
和[Foundation follow-up](coding/foundation_followup_v2_20260915.md)等原报告保持原文；其中的脚本路径、链接和命令
属于当时源码，不能直接用于当前checkout。历史批次仍须使用自身的worker、源码快照和匹配环境；上述Git基线
只用于查阅被删除的实现，不替代批次seal或证明旧实验可以在当前环境重跑。

## 代码与结果的边界

- 参数解析和命令分发可以放在独立`*_cli.py`，执行和评分函数留在各自模块。现有`public_mechanics_suite`及`api_repair`命令保留为薄转发，外部调用路径不变。
- 对应题目数据、原报告和seal保留原值；删除活动树中的专用实现不改变冻结运行协议，也不删除已有批次目录里的worker或源码快照。
- 输出位置由各入口原有的`--root`、`--run-root`或`--output`指定，可以是项目内被忽略的目录或项目外目录；不以目录名判断测评类别。
- 工程回归继续在`tests/`；Pydantic Evals生成评测结果，Phoenix保存可查询的追踪与评分副本。[如何解释结果](../docs/architecture/verification-evidence.md)
