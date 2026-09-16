# 未完成计划

最后更新：2026-09-14

| 顺序 | 优先级 | 计划 | 状态 | 概览 | 排序依据 |
|---:|---|---|---|---|---|
| 1 | 高 | [PLAN-0006：轻量程序检查与人工审查](0006-domain-task-evaluation-baseline.md) | 进行中 | 新评测改为人工确认答复；一次真实补答后续写但预算耗尽、脚本有region错误；旧失败保留，T1仍待人工审查。 | 用户指定收敛当前任务范围，原有计划相对顺序不变 |
| 2 | 高 | [PLAN-0011：禁止 CAE 脚本内部提交 Solver Job](0011-block-solver-submit-inside-cae-script.md) | 讨论中 | 把 Solver 只能经 `run_solver_job` 启动从 Agent 协议补成确定性代码边界。 | 影响执行预算、provenance 与阶段边界完整性（Codex 初定） |
| 3 | 高 | [PLAN-0010：本地工作台集成](0010-local-web-workbench.md) | 进行中 | 工作台、模型预览与 Web Solver 长任务边界已完成工程验收并整理提交；两条新真实主线仍未运行。 | PLAN-0009已完成；真实结果与PLAN-0006分开保留 |

新UI计划的方案见[ADR-0025](../../adr/0025-local-web-workbench-over-coding-run.md)。以上顺序为当前安排，
PLAN-0006不构成UI离线接线/API工作的硬依赖；本次未更改其优先级、状态或既有范围。

已完成：[PLAN-0008：三路UI接线资格与比较](../done/0008-assistant-ui-integration-qualification.md)（官方最小定制/ExternalStore/AG-UI均通过确定性场景，非产品UI或仿真能力）、
[PLAN-0007：用户提问与待答恢复](../done/0007-user-question-tool.md)（工程实现与离线验证，无真实模型收益结论）、
[PLAN-0005：目标运行配置与交互合同](../done/0005-align-target-agent-runtime.md)（离线配置实施完成，非任务收益）、
[PLAN-0001：收敛 RAG 证据基线](../done/0001-consolidate-rag-evidence-baseline.md)、
[PLAN-0002：RAG运行时证据服务](../done/0002-rag-runtime-evidence-service.md)、
[PLAN-0003：Coder有界证据使用](../done/0003-bounded-evidence-use-in-coder.md)（实现完成，行为gate未通过，默认off）、
[PLAN-0004：评测有效性校准](../done/0004-evaluation-validity-and-staged-path.md)（阶段A完成）。其后阶段B已独立运行，结果见[24条文档行为报告](../../../benchmarks/coding/document_behavior_v2_phase_b_20260912.json)；阶段C未执行。
