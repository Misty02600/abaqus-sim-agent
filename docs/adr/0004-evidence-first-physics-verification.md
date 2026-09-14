# ADR-0004：以证据和物理验证定义任务成功

## 状态

已采纳

## 日期

2026-08-19

## 当时遇到了什么

多个公开 CAE Agent 项目把脚本可执行、solver 正常退出或结果文件存在作为主要成功信号，但这些信号不能证明模型忠实于用户需求，也不能证明数值结果符合物理规律。

[FEABench](https://github.com/google/feabench) 报告 Agent 可执行率达到 0.88，Gold 集中却只有 2/15 得到有效数值目标；[MechAgents 的可靠性研究](https://arxiv.org/abs/2408.13406) 也出现可执行率 100% 而任务成功率 0% 的条件，并观察到同模型 Critic 对错误结论的附和。若本项目只展示“生成并跑通 Abaqus”，就无法证明多 Agent 的实际价值。

## 最后决定

将任务成功拆成三个必须独立报告的 gate：

1. **Executable**：输入通过静态预检，solver 完成，声明的结果 artifact 可读取。
2. **Specification fidelity**：几何、材料、单位、分析步、边界、载荷、网格、输出和用户确认的假设与 SimulationSpec 一致。
3. **Physics validity**：解析解或参考结果误差、问题相关的物理不变量，以及必要时的网格收敛通过确定性容差。

solver 完成但后两层失败时，整体任务不得标记为成功。

Verifier 在确定性提取和数值检查之后独立判定，只能输出 VerificationReport 和证据，不能静默修改模型。Reviewer 只能基于 spec、完整原始日志、结构化错误和可追溯知识提出有限 patch；相同错误或无进展达到预算后转为重新设计或人工接管。

项目建立版本化 Gold benchmark，并满足以下要求：

- 首批使用少量人工核验案例，覆盖解析 happy path、需求歧义、非物理输入、输入/求解失败、有限修复，以及取消、超时、崩溃恢复和重复提交。
- Evaluator 的隐藏答案、容差和故障真因与 Agent 可见 artifact 分离。
- 每个 run 保存代码、模型、prompt、图、配置、检索证据和 artifact hash 指纹，以及结构化 Trace 和 badcase。
- 至少比较确定性 baseline、单 Agent、分层多 Agent、去 Reviewer、去 Verifier；RAG 上线时增加有无 RAG 消融。
- 可执行率、建模忠实度、物理通过率、恢复可靠性、成本和负面实验分别报告，不用一个总分掩盖失败类型。

RAG、MCP、前端和微调只有在这个固定评测体系中证明收益或满足真实消费者需求后才进入核心路线。

## 为什么这样选

物理验证是 CAE 项目区别于通用 RAG/Agent 展示的核心壁垒。确定性 gate 能防止 Agent 互相说服却没有事实依据，也能让不同模型、角色和检索策略在同一题集上公平比较。

求职作品中，[diagnosis-agent](https://github.com/kanglongh/diagnosis-agent) 保留不足 1pp 的 Agent 边际收益，[PaperAgent](https://github.com/ztt0216/PaperAgent) 和 [DevAgentOps](https://github.com/ultrabababa/dev-agentops-platform) 将固定题集、重复运行、确定性 scorer、Trace 和 badcase 作为证据。这种实验纪律比增加框架和服务数量更能支持可追问的简历结论。

## 没有采用的方案

- 只用 solver exit code、stderr 或 ODB 存在判断成功：只能覆盖执行层。
- 只用 LLM-as-a-judge：难以稳定验证数值容差、单位、平衡和恢复不变量。
- 增加多个同模型 Critic 投票：缺少独立证据时容易附和，并增加 token 和延迟。
- 先建设大规模 RAG、GraphRAG 或微调数据集再做 benchmark：无法判断复杂能力是否真正提升任务成功。
- 只报告最佳运行：隐藏随机性、失败模式和恢复成本。

## 带来的影响

- 有利：每个“成功率”都有可复现定义；能量化多 Agent、Reviewer、Verifier 和 RAG 的真实增益；负面结果仍有研究价值。
- 代价：需要人工制作 Gold cases、参考解、容差、故障注入和 evaluator。
- 风险：有限 Gold 集可能被过拟合；需要隐藏 evaluator、冻结测试集，并逐步加入分布外案例。

## 落实与确认

- 实施情况：首个二维板拉伸 Gold 已实现宿主确定性 verifier、模型不可见的 ODB oracle、artifact freshness、
  solver/`.sta` 完成证据、element type/count、U/S/RF 数值和 Agent `results.json` 交叉检查；pydantic-evals
  smoke 连续运行三次并要求至少两次通过。确定性 reference model 已在本机 Abaqus 2024 通过一次持证
  integration，验证真实 solver/oracle；通用 specification fidelity、更多 Gold、分布外案例、消融和三次
  Agent 持证运行仍待完成。
- 代码或测试：`src/abaqus_sim_agent/verification/`、`src/abaqus_sim_agent/evaluation/plate_tension.py`、
  `benchmarks/coding/`、`tests/test_plate_tension_verification.py`、
  `tests/test_plate_tension_evaluation.py`。

## 相关文档

- [项目架构](../architecture/overview.md)
- [ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun](0008-pydantic-ai-harness-abaqus-coding-run.md)
