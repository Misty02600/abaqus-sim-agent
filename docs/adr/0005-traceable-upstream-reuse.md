# ADR-0005：有来源地吸收与重构 AbaqusAgent

## 状态

已采纳

## 日期

2026-08-19

## 当时遇到了什么

ADR-0001 要求本仓库保持独立实现，并默认不直接复用上游源码。完成对
`references/AbaqusAgent` 的源码级分析后，可以确认上游已经沉淀了有价值的 Abaqus
业务规则、提示词语义、案例检索流程、错误修复分工和 ODB 后处理脚本。为了维持“完全独立”
而重新发明这些部分，会制造低价值重复工作。

与此同时，上游把运行时客户端、大段文件内容和配置放进 Graph State，使用固定文件名同步执行
Abaqus，没有 durable checkpoint、幂等提交、取消、崩溃对账和独立物理验收。整仓迁移会把这些
边界问题一并带入本项目。

分析基线为上游提交 `f5462b3eaf34d21a958f495b80e40df45b3f39db`。上游仓库使用
MIT License；仓库中的案例语料还可能包含第三方 Abaqus benchmark 内容，其再分发权需要独立核验。

## 最后决定

本项目继续保留独立 Git 仓库、包结构、领域模型和维护历史，不作为上游 fork；但允许在充分理解后，
大幅吸收、重构或复用上游已经实现的设计与代码。

复用按三类管理：

1. **业务语义与设计借鉴**：在架构或源码分析文档中记录来源，并通过本项目的 typed contract、
   policy 和测试重新表达。
2. **重写或实质性改编**：记录上游仓库、基线提交、原文件或符号，以及本项目改变的行为和边界。
3. **直接复制代码、提示词或资产**：在文件头或统一的第三方声明中保留来源、MIT 许可声明和修改说明；
   如果构成软件的实质性部分，同时随分发物保留 MIT copyright 与 permission notice。

当前仓库的 `RequirementResult`、`SimulationSpec`、`ModelingPlan`、
`ArtifactManifest`、Agent Protocol 和 `SimulationExecutor` 继续作为实现骨架。
上游用于提供业务知识和 Abaqus 适配经验，不替代这些边界。

以下内容在当前阶段不直接迁入：

- 上游 Graph State、`LLMService`、路由代码和固定文件名同步执行器；
- 使用 `allow_dangerous_deserialization=True` 加载的 FAISS/pickle 产物；
- 尚未确认原始来源和再分发权的案例语料、输入文件和派生索引；
- 把 ODB 存在或未解析到错误等同于仿真任务成功的判定。

## 为什么这样选

这个决定保留本项目已经建立的可序列化状态、可恢复控制平面、执行副作用边界和分层验证，同时允许直接
利用上游积累的 Abaqus 专业经验。项目的独立性来自对行为和边界的理解、重构和验证，不来自刻意重复
已有工作。

明确复用层级也能区分“参考了一个思路”和“使用了受许可证约束的具体表达或实现”，避免模糊来源；
对案例数据单独核验，则避免把软件仓库许可证误当作第三方语料的再分发授权。

## 没有采用的方案

- 继续完全禁止源码复用：来源最简单，但会重复实现已有的 ODB 提取和 Abaqus 规则。
- 直接把上游 `src`、`database` 合并进本项目：启动快，但会继承不可恢复状态、
  固定 workspace、同步副作用和不透明数据来源。
- 先复制再补来源：短期省事，但无法可靠区分原始代码、改编代码和本项目实现。

## 带来的影响

- 有利：可以集中精力理解和加固现有业务闭环，并减少低价值重写。
- 有利：每个重要复用点都能回答“来自哪里、保留了什么、改变了什么、如何验证”。
- 代价：实质性复用时需要维护来源记录、许可证声明和修改说明。
- 风险：提示词和数据也可能构成受保护内容，不能因为它们不是 Python 源码就省略来源核验。

## 落实与确认

- 实施情况：已更新仓库本地指令、架构说明和源码分析；本次只形成复用边界，没有复制上游代码、
  提示词、案例数据或索引。
- 上游基线：`LIRAM-LIN/AbaqusAgent@f5462b3eaf34d21a958f495b80e40df45b3f39db`。
- 替代关系：本 ADR 替代 [ADR-0001](history/0001-independent-from-upstream.md)；ADR-0001 保留为历史记录。

## 相关文档

- [Agent 与首个完整实现范围](../architecture/agent-implementation-scope.md)
- [项目架构](../architecture/overview.md)
- [上游 AbaqusAgent](https://github.com/LIRAM-LIN/AbaqusAgent)
- [上游 MIT License](https://github.com/LIRAM-LIN/AbaqusAgent/blob/f5462b3eaf34d21a958f495b80e40df45b3f39db/LICENSE)
