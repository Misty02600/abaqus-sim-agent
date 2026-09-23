# 未完成计划

最后更新：2026-09-23

| 顺序 | 优先级 | 计划 | 状态 | 概览 | 排序依据 |
|---:|---|---|---|---|---|
| 1 | 高 | [PLAN-0006：轻量程序检查与人工审查](0006-domain-task-evaluation-baseline.md) | 进行中 | 新评测改为人工确认答复；一次真实补答后续写但预算耗尽、脚本有region错误；旧失败保留，T1仍待人工审查。 | 用户指定收敛当前任务范围，原有计划相对顺序不变 |
| 2 | 高 | [PLAN-0010：本地工作台集成](0010-local-web-workbench.md) | 进行中 | 工作台、模型预览、Web Solver、已有目录、删除会话和会话内权限已完成工程验证；两条新真实主线仍未运行。 | PLAN-0009已完成；真实结果与PLAN-0006分开保留 |
| 3 | 中 | [PLAN-0013：工程状态验证与 NAFEMS LE6 回归修复](0013-engineering-state-validation-and-nafems-le6.md) | 讨论中 | 调查 NAFEMS LE6 案例中 CAE 建模成功但网格为空的问题；补充工程状态验证规则并修复回归案例。 | 由公开力学案例测试暴露，需先明确失败边界 |

新UI计划的方案见[ADR-0025](../../adr/0025-local-web-workbench-over-coding-run.md)。以上顺序为当前安排，
PLAN-0006不构成UI离线接线/API工作的硬依赖；本次未更改其优先级、状态或既有范围。

已完成：[PLAN-0012：Agent 运行预算、超时、上下文压缩与收尾策略](../done/0012-agent-runtime-budget-and-context-policy.md)（普通产品 unlimited limits、runtime permission、可暂停 active wall、launch v7 compaction 与 Skill 绑定完成离线验证）、
[PLAN-0011：禁止 CAE 脚本内部提交 Solver Job](../done/0011-block-solver-submit-inside-cae-script.md)（最小 AST 阻断与离线合同测试完成，不构成动态 Python 沙箱）、
[PLAN-0008：三路UI接线资格与比较](../done/0008-assistant-ui-integration-qualification.md)（官方最小定制/ExternalStore/AG-UI均通过确定性场景，非产品UI或仿真能力）、
[PLAN-0007：用户提问与待答恢复](../done/0007-user-question-tool.md)（工程实现与离线验证，无真实模型收益结论）、
[PLAN-0005：目标运行配置与交互合同](../done/0005-align-target-agent-runtime.md)（离线配置实施完成，非任务收益）、
[PLAN-0001：收敛 RAG 证据基线](../done/0001-consolidate-rag-evidence-baseline.md)、
[PLAN-0002：RAG运行时证据服务](../done/0002-rag-runtime-evidence-service.md)、
[PLAN-0003：Coder有界证据使用](../done/0003-bounded-evidence-use-in-coder.md)（实现完成，行为gate未通过，默认off）、
[PLAN-0004：评测有效性校准](../done/0004-evaluation-validity-and-staged-path.md)（阶段A完成）。其后阶段B已独立运行，结果见[24条文档行为报告](../../../benchmarks/coding/document_behavior_v2_phase_b_20260912.json)；阶段C未执行。
