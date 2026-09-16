# 架构阅读入口

当前产品是一个本地 Abaqus Coding Agent。模型写代码，宿主管理预算、问答和验收；会话恢复与外部 Job
恢复是两个不同边界。普通产品只验执行证据，固定评测另验物理合同。

| 想知道什么 | 阅读入口 |
|---|---|
| 现在能做什么、公开入口和组件职责 | [架构总览](overview.md) |
| 部署、解释器、状态与数据所有权 | [系统框架](system-framework.md) |
| Agent 权限、协议和可选 profile | [Agent 范围](agent-implementation-scope.md) |
| 一次任务如何提问、继续、修改和完成 | [CodingRun 流程](flows/simulation-run.md) |
| Solver 运行、超时与崩溃有什么保证 | [Job 生命周期](flows/job-lifecycle.md) |
| API discovery、精确查证与文档检索如何区分 | [知识接入](knowledge-access.md) |
| 哪些结论有哪种验证，局限是什么 | [验证证据](verification-evidence.md) |
| 每项技术承担什么真实职责 | [技术栈](technology-stack.md) |
| 为什么采用当前设计、哪些决定已经失效 | [ADR 索引](../adr/README.md) |

本目录说明当前事实。未完成的工作在既有 [计划索引](../plans/todo/README.md)，
已接受决定的原始取舍保留在 ADR；实验细节链接冻结报告，不在各架构页重复累计。
