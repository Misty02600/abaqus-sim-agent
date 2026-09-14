# ADR-0010：以显式受预算工具接入官方建模概念检索

## 状态

已采纳

## 日期

2026-08-23

## 当时遇到了什么

Exact API Catalog 能回答方法签名和动态容器差异，但不能判断 `CPS4R` 应与 Solid Section 而不是 Shell
Section 配合。修正后的从零建板消融中，full Catalog 的失败项三次进入 Solver，`.dat` 已报告 80 个单元
缺少属性，模型仍多轮后才识别 element/section 不兼容。

本地 Abaqus 文档索引已经包含所需证据。普通 dense 查询会把 Shell 页面排在前面；加入 `CPS4R`、
`*SOLID SECTION` 和 product 过滤后，exact-symbol-augmented dense 查询能稳定返回二维 Solid Element、
Solid Continuum 和 Solid Section 证据。此前该索引只有独立 CLI/benchmark，没有 Agent 工具。

## 最后决定

- 直接复用现有 `AbaqusDocumentIndex.search()`，不重建语料、索引或新检索框架。
- 只有显式选择 `concept_retrieval_mode=tool` 并提供 workspace 外索引时，才向 Coder 注册
  `lookup_abaqus_concept`；默认继续为 `off`。
- 工具接收自然语言 query、显式 exact terms 和 product filters，先放置 metadata-filtered exact 命中，
  再用 dense semantic 结果补位，最多返回紧凑的 title、section、excerpt、source 和 match type。该路径
  不包含 BM25、全文倒排检索或稀疏/稠密分数融合。
- 每个 CodingRun 最多两次有效 Concept 查询；重复查询不计预算。
- 文档证据只用于 element、section、step、constraint、load、material 和 output 等建模概念，不能代替
  Exact API Catalog，也不能补造或覆盖缺失的用户物理输入。
- 查询写入 workspace 外 `concept_lookups.jsonl`，usage 单独记录 `concept_lookup_calls`。
- 接入完成只证明工具合同可运行；在新的常见固体力学 Gold 上完成 no-concept/concept 消融前，不声明任务
  收益，也不改成默认开启。

## 没有采用的方案

- 把概念规则继续堆进 Skill：会把跨文档知识变成难维护的手写答案。
- 将 Concept chunks 合入 Exact API Card：两者查询目的、粒度和评测不同。
- 自动把全部检索结果塞入每次 Prompt：会破坏 no-retrieval 基线并持续增加上下文。
- 增加 reranker、GraphRAG 或新向量服务：现有 exact-symbol-augmented dense baseline 已足以先验证工具价值。

## 带来的影响

- Coder 现在可以按需读取官方建模证据，但默认运行和既有 Gold 工具集合不变。
- 后续 Gold 可以独立比较 Skill、API notes、Concept tool 的贡献。
- 索引仍是本机不可变输入，不进入 Agent workspace 或 Git。
- 模型仍负责解释证据；Abaqus runtime、Solver diagnostics 和 host verifier 继续负责执行与裁决。

## 落实与确认

- 工具和预算：`src/abaqus_sim_agent/coding/agent.py`、`coding/contracts.py`。
- 紧凑 payload/trace：`src/abaqus_sim_agent/coding/concept_lookup.py`。
- 现有 dense index 与 exact metadata 增强：`src/abaqus_sim_agent/retrieval/index.py`。
- CLI 与 plate eval 已支持显式 index/mode；默认 `off`。
- 合同测试覆盖真实 retrieval fixture、两次预算、重复去重、trace 和缺失物理输入提示。
- 本机 `var/rag` smoke 以 `CPS4R`、`*SOLID SECTION`、`Abaqus/Standard` 查询，前两条 exact evidence
  分别为 `Two-Dimensional Solid Element Library / Plane Stress Elements` 和 `*SOLID SECTION`。
- 2026-08-24 的真实 DeepSeek + Abaqus 三维杆消融中，仅暴露工具的 3 次运行均未查询；加入
  from-scratch preflight policy 后 3/3 各查询一次，但 control 与 preflight 均为 2/3，未观察到闭环
  成功收益。详见 `benchmarks/coding/bar_tension_3d_concept_evaluation_20260824.json`。
- 2026-09-03 源码审计确认本 ADR 的原始工具路径没有 BM25/全文倒排、RRF、加权融合或 reranker；
  42条离线基准的排序与准确率重放完全一致。该结果只证明给定正确 `exact_terms` 后的检索增强。
  同日新增的独立 BM25/Dense/RRF 实现与负结果由 [ADR-0022](0022-evaluate-bm25-dense-rrf-before-agent-adoption.md)
  记录，尚未替换本工具路径。

## 与现有决定的关系

- 扩展 [ADR-0008](0008-pydantic-ai-harness-abaqus-coding-run.md) 的单 Coder 工具边界。
- 与 [ADR-0009](0009-exact-abaqus-api-catalog.md) 并列：Concept retrieval 处理建模语义，Exact Catalog 处理
  Python API。
- 继续遵循 [ADR-0004](0004-evidence-first-physics-verification.md) 的任务级消融和独立 verifier。
- [ADR-0022](0022-evaluate-bm25-dense-rrf-before-agent-adoption.md) 在不改变本工具合同的前提下增加公平的
  BM25/Dense/RRF 对照，并根据首轮负结果拒绝直接接入 Agent。
