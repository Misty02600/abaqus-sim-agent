# RAG 证据基线 v1：整合完成，质量分数单独报告

日期：2026-09-11。`rag-evidence-baseline-v1` 已提供同一组合对象与显式 CLI：加载固定候选后，执行原搜索、
公式关联交付及宿主质量评分。一次正式 16 请求回放全部复现既有结果，没有追加复测。**工程整合通过，不是
检索质量整体合格，也没有接入产品默认。**

## 本轮质量评分

固定的 16 个 development 请求中，7 题具有原先声明的 10 项必要证据条件。另有两个源段修复目标，单独计分；
其余无标签/不可由文档回答的用例不假装有质量分数。五个显式 guard 单独检查，不混入质量分母。

| 阶段 | 必要证据条件覆盖 | 覆盖全部已标注条件的题目 |
|---|---:|---:|
| 有界候选池 | 9/10 = **90%** | 6/7 = 85.7% |
| 重排后前 4 个不同父单元 | 6/10 = **60%** | 4/7 = 57.1% |
| 实际交付证据 | 6/10 = **60%** | 4/7 = **57.1%** |

必要证据 **MRR@64 = 0.635954**：按父单元去重后，取每题第一条已标注必要证据的排名倒数，再对这 7 题求均值；
未命中为 0。它不是完整相关性 MRR，也不能替代多证据条件覆盖。一个条件允许多个替代集合，集合之间 OR、
集合内部 AND；每个条件最多计一次，不因同一父单元有多个检索片段而重复得分。

两个独立修复目标仍为 **1/2 完整交付**：history 精度规则重排第 1 并完整交付；热膨胀说明段已入池，但子片段
重排第 63、仍未送达。history 题的旧精确单元标签未命中，不代表新增的精度规则不正确或没有送达；两套标注
分开保留，不事后扩大旧标签来提高得分。

这些分数与对历史结果的离线评分完全相同。本轮没有更换算法、修改 query、重新分块或计算文档向量，因此
不以分数上涨为目标。已有标注不是穷尽的语义相关性，也未证明覆盖问题的全部含义；57.1% 只表示全部
**已标注条件**覆盖，不是答案正确率、泛化成绩或产品准入线。

## 工程验收

- 16/16 原请求逐项复现有序 Dense/BM25/pool ID、重排 ID、实际证据正文及关键来源/版本/范围/交付字段。
  重排分数采用预定绝对误差 1e-5；延迟、运行时间和新增报告信封不作为相等字段。
- 五个显式符号/版本/查询长度 guard 为 **5/5**；执行错误 **0**。
- 新增 **25 项**确定性测试通过，相关聚焦测试合计 **66 passed**；全仓 **970 passed、27 skipped**。
  27 项持证测试未启用，本次没有调用 Abaqus。两个新源码模块 Ruff/BasedPyright 通过。
- 正式批次只运行一次，使用 **640 个本地重排 pair**。生成模型调用 **0**，Abaqus 调用 **0**，无远程评分器。
- 本机启动及加载验证约 **16.37 秒**；全批约 **79.79 秒**，包含启动和末尾复核。实际最大证据为 **2324 proxy tokens**。
  这些是本次串行运行观察，不是线上延迟 SLO 或受控性能比较。
- 旧语料、实现、模型与原始结果的内容绑定保持；新批次在查询前封存，并在结束时核对。

原 16 题没有任何公式关联说明段进入最终 Top4，所以新增受控排序的组合/CLI 测试来验证该分支：检查实际
关联、正文范围及完整元数据预算。这个测试是接线证据，不算一次真实检索命中或质量提升。

## 当前可用入口

组合对象：`evaluation.rag_evidence_baseline.RagEvidenceBaseline`。
显式 CLI：`python -m abaqus_sim_agent.evaluation.rag_evidence_baseline_cli`。
CLI 与可复用对象走同一 `search` 方法，标签只传给宿主 scorer，不能改变召回或重排。

在仓库根目录使用已有授权语料与已获取的模型；下面是使用示例，输出目录必须尚不存在：

```powershell
uv run python -m abaqus_sim_agent.evaluation.rag_evidence_baseline_cli `
  --candidate-directory var/rag-ingestion-fixes-v2 `
  --symbol-index-directory var/rag `
  --scope-rules benchmarks/rag/unified_candidate_scope_rules_v1.json `
  --reranker-directory var/models/ms-marco-MiniLM-L6-v2/233902d25c440f23af6f7d6e94d2946bac0bee0a `
  --requests benchmarks/rag/rag_evidence_baseline_v1.json `
  --output var/audits/my-rag-baseline-run
```

入口不下载模型、不重建索引，固定使用 `hybrid / rerank_question_only / page_lexical`，以及
64 候选、4 交付单元、1200 正文 token、4000 序列化证据 token 上限。计数仍是 MiniLM WordPiece 代理。
目录可以显式指定，但内容须匹配该 profile；名称相同而 hash 不同不会被接受。语料为 2026 FD02，不能证明
2024 兼容性；只有显式 `source_release` 作硬版本限制，unknown 适用范围不能被解释为适用。

可用单题请求文件，不提供标注时仅返回证据，质量字段为未评测：

```json
{"cases":[{"id":"question-1","request":{"kind":"question","question":"Explain the documented unit convention.","product":"Abaqus/Standard"}}]}
```

输出包括 `manifest.json`、`ready.json`、逐题 `.result.json` 和 `.score.json`、`summary.json` 及
`completion.json`。`engineering_status` 与 `quality` 分开；没有总质量通过阈值，也不把多个指标任意加权。
历史 `unified_candidate_cli` 仍保留用于旧实验，不应把它描述为已接入最新公式关联交付的入口。

## 失败与限制

- 新候选的关联文件缺失、hash 漂移或关系非法时，在查询前拒绝；不降级成无关联正文。
- 正文和关联元数据一起计入预算；不截断、不从第五位补位。整个单元被淘汰时记录原因。
- 有标注请求的运行失败计为失败与零覆盖；无标签不是满分，失败的部分证据不能挣得质量分。
- 输入、索引或模型漂移使整批 invalid，不能用其他题的成绩宣布通过。可捕获中断尽力写 incomplete；
  硬终止或写盘失败时，缺少有效 completion 就是不完整，不宣称 durable workflow。
- 仍是本地、显式、串行的固定候选。没有服务化负载承诺、独立新 held-out、生成答案评价或 Agent 收益证明。

## 证据与实现

- [固定请求与标签](rag_evidence_baseline_v1.json)
- [机器结果及逐题等价检查](rag_evidence_baseline_v1_20260911.json)
- [组合与评分实现](../../src/abaqus_sim_agent/evaluation/rag_evidence_baseline.py)
- [CLI](../../src/abaqus_sim_agent/evaluation/rag_evidence_baseline_cli.py)
- [合同测试](../../tests/test_rag_evidence_baseline.py)
- [整合前的修复结果](ingestion_fixes_v2_20260911.md)

本机原始证据位于 `var/audits/rag-evidence-baseline-v1`。封存 SHA-256 为
`2f477704fb9dab3a0cdae3a8415964312697298aaaa1b168b3a0fdf284825abc`；机器结果 SHA-256 为
`1a8437882b93e76f4dd83e5e79434617753e15684a6ef3bb4282e5a84864f95e`。
