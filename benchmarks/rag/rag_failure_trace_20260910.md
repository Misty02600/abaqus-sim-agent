# 三类 RAG 失败的证据链定位

## 结论

| 查询 | 原文与索引 | 候选池 | 重排与返回 | 已确认的丢失位置 |
|---|---|---|---|---|
| ODB 装配层节点集 API | 本地有 Scripting Reference，但两个对应页面均未纳入该语料，索引记录为 0 | 无对应直接 API 证据可召回 | 返回节点集概念等相关话题 | 语料选择范围，而不是先归因于排序 |
| Riks 边界条件 | 必要段落完整存在于原文、父单元和一个子片段 | Dense Top-20 未命中；BM25 排第 634，未进入 Top-20 | 未交给重排器 | 候选召回 |
| 精确输出时间警告，问法 1 | 机制、过程例外和性能影响的原单元完整存在 | 机制段由 BM25 第 10 名召回；Dense Top-20 未命中 | 子片段及去重父单元都排第 7，未进 Top-4 | 最终排序/截取 |
| 同一警告，历史问法 2（对照） | 同一源单元 | 机制段 BM25 第 1；Dense Top-20 未命中 | 重排第 3，进入返回结果 | 该必要段落已送达 |

本次主分析对象是已冻结的 `var/rag-headerless-pairs-v1` 候选实验：Dense20 + BM2520 的并集、固定
cross-encoder 和 Top-4 父单元，不是一次当前默认 `var/rag` 的实时运行。没有重新计算 Dense/重排分数，
没有模型、Abaqus 或索引写入。只在绑定的只读 SQLite 数据库上重算完整 BM25 排名，并逐条确认前 20
与历史记录完全相同。

## 1. API：文件在本机，但不在检索语料里

候选语料选择了 9 类指南、1,480 页，覆盖建模、分析、约束、单元、接触、关键字、材料、输出和规定条件。
没有选择 `SIMACAEKERRefMap`。这与当前 loader 的 `DEFAULT_GUIDES` 相符，是资料范围选择，不是
已选 API 页面在解析过程中丢失。

本机明确存在：

- `SIMACAEKERRefMap/simaker-c-odbassemblypyc.htm`，包含 `nodeSets` 成员。
- `SIMACAEKERRefMap/simaker-c-fieldoutputpyc.htm`，包含 getSubset 的 OdbSet 区域参数说明。

这两个页面均不在 source manifest，候选索引记录均为 0。扫描全部 36,897 个源单元的检索文本，
`nodeSets`/`OdbAssembly` 字面命中也为 0。因此，这个候选语料无法提供本题所要求的直接成员依据。
历史 context-v2 索引检查也记录 OdbAssembly 页面为 0 条，但那是另一 profile 的独立观察，不混用排名。

下一步的目标应是让 API 问题进入确实拥有 API 依据的入口：可以讨论复用现有 API Catalog 的分工，或
有边界地增加脚本文档来源；本次没有替用户决定扩充默认语料。这里的本地手册为 **2026 FD02**，不能把
补入它直接当作 **2024** API 兼容证据。此前固定代码题的 2024.10 stub 依据属于另一来源。

## 2. Riks：不是漏解析，而是检索没有优先找到所问的约束

目标位于 `simaanl-c-postbuckling` 的 `Boundary Conditions`。核对原 HTML hash 与 XPath 后，目标段落
完整保存在一个 322 字符的检索单元中：包含可施加边界条件的自由度，以及不能通过 amplitude 定义改变
Riks 过程中规定位移幅值的限制。它是历史标注的必要段落，不被提升成对整个提问的完整物理答案 oracle。

子片段带有文档标题与章节路径，未缺正文、未被拆成缺少关键句的碎片。BM25 全量得分中的位置为
**634，25.4942 分**；前 20 与历史候选一致。Dense 的历史前 20 也不包含它，所以后续重排、去重、展开
与交付都没有机会恢复它。

BM25 前列集中于 `*STATIC/RIKS` 参数、比例加载定义、LPF 与 Riks 方法介绍。同页其他单元可以被召回，
因此不能说整篇 Riks 文档没有进入检索范围。已确认的现象是：检索返回了方法主题，却没有优先找到本题
所需的边界条件限制。原问题反复包含 Riks、static、proportional loading 等词，目标正文侧重 boundary
conditions、degrees of freedom、amplitude 等；这是可检查的匹配差异，但未做消融，不能把低排名
单因果归于某个词或断言换 embedding 就能解决。

本次没有取得该候选索引的 Dense 全库排名，故只报告“不在前 20”，不编造它在 Dense 中的具体位置。

## 3. 警告：关键证据已进入候选池，Top-4 没选中

目标是 `simaout-c-dboutput-frequency` 中
`Time Domain Analysis: Time Incrementation` 的同一 h3 下两段：机制/例外，以及额外增量的性能影响。
源单元完整，子片段分别保存这两部分。

问法 1 的机制段 BM25 第 10，性能段第 20，均进入并集；机制段重排第 7，性能段第 19。首个目标父单元
也是第 7，说明不是去重将它挤掉。机制段的模型输入 pair 为 354 tokens，历史记录明确 `truncated=false`，
所以这次失败不是重排输入被 512-token 上限截掉。

前四条分别是 TIME MARKS 参数、Direct Cyclic 的精确输出例外、TIME POINTS 参数，以及 message file
的一般说明。它们都涉及查询词，但没有覆盖本题所需的完整增量调整机制。尤其 Direct Cyclic 是特定过程
条件，不能在用户只提供 Standard/自动增量的信息时直接拿它解释当前 warning。

问法 2 将同一个机制段排到 BM25 第 1、重排第 3，正常返回。两条都是先前实际出现的问法，不是本次为
调优新造的 query。它说明结果对问法敏感；不能据两条记录推导通用 query 改写规则。

进一步追到实际重排输入：`scoring_document` 虽先构造 `search_context + 片段正文`，但下游
`passage_text` 会从保留的 metadata 重新加上 `Title` 和 `Section`。因此不能只看到中间正文就判断
“重排丢了标题/章节”，也没有依据把“补回标题”当成这次修复方案。已知的是关键机制段得分不够进入前四；
学习模型为何给出这些分数仍未被单因果解释。

## 处理顺序与非目标

1. **先明确 API 证据入口与资料覆盖范围**：这条证据缺口最确定，调整 Top-k 或重排无法凭空补出未收录页面。
2. **对 Riks 定位候选召回**：关注问题所问约束是否被召回，不继续用扩大已选片段的办法处理池外证据。
3. **对警告检查排序的条件适用性与回答覆盖**：保留原问法 2 作为对照，避免只让一条已知查询变好。

这不是已经授权的实施计划。本次只定位问题，不新增路由、索引、query rewrite、embedding、重排策略或
Agent 提示词；也不重开编辑 guard/知识冲突支线。下一项修复应单独确定目标与验收证据。

## 可复核证据

- [完整机器记录](rag_failure_trace_20260910.json)：源节点、单元/子片段、候选和排名、前列竞争片段、
  交付内容、资料范围及 hash。
- 原始诊断目录：`var/audits/rag-failure-trace-20260910/`；只读诊断脚本：
  `var/audits/trace_rag_failures_20260910.py`。
- 来源绑定：候选 `prepared.json` 的 units/children/manifest hash、历史 replay 全部文件 hash、
  index-binding 与 BM25 DB hash均核对。数据库前后 SHA-256 不变，五条 BM25 Top-20 逐项复现。
- 此前的[交付模式对照](abaqus_2026_evidence_delivery_modes_review_20260908.md)仍有效：展开完整单元
  能改善交付完整性，但不会自动解决上述语料、召回和排序缺口。
