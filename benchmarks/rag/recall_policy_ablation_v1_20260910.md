# 召回策略对照：Riks进入候选池，但最终证据仍未送达

日期：2026-09-10。范围：独立RAG候选，不调整Agent、默认索引、语料或重排模型。

## 结果

实现并验证了两个显式开关：只用问题正文，以及在全局候选基础上补充同页相关单元。四组各10项固定
检查全部完成；两组原配置的候选顺序、重排顺序/分数和返回证据与历史记录完全一致。

| 配置 | Riks必要段落 | warning问法1机制段 | warning问法2机制段 | 必要证据入池／送达 |
|---|---|---|---|---:|
| 原全文Dense | 未入前64 | 重排7，未送达 | 重排1，送达 | 2/3、1/3 |
| 原全文Hybrid | 未入候选池 | 最佳重排9，未送达 | 最佳重排3，送达 | 2/3、1/3 |
| 仅问题正文Dense | 仍未入前64 | 最佳重排1，送达 | 重排1，送达 | 2/3、2/3 |
| 全文Hybrid＋同页补充 | 入池位置45，重排34，未送达 | 最佳重排8，未送达 | 重排3，送达 | 3/3、1/3 |

这三项检查只有两个唯一源单元：Riks一项，以及同一warning的两个历史问法。不是三个独立任务，
更不是完整技术答案的正确率。四组的流程/预算/显式符号检查均10/10，也不能替代必要证据指标。

**当前没有解决Riks的最终证据交付。** 同页补充克服了这个已知问题的全局候选缺口，但把片段送入
候选池之后，它仍被重排到34位。仅问题正文在两个warning问法上有局部正向结果，但未改善Riks召回。

## 两项改动具体做什么

### 只用问题正文：`--query-text-mode question_only`

原默认保持 `question_context`，把 `question` 与 `observed_context` 合并作为检索/重排文本。
显式question_only只使用question字段；原请求及其上下文继续留在trace，显式product/source_release
约束不变。实际送入模型的query和是否省略context也进入trace。

本批五条历史自然语言输入原来将“问题”和“背景”用换行合在question字段中。本次只在回归数据里恢复
两个字段，重新拼接可逐字符复原原query；没有加入同义词、答案或新条件。产品实现不会按第一行截断
任意问题，测试也确认question字段内部的多行全部保留。

这个开关同时改变query embedding与reranker输入，因此本批不是二者的单独消融。warning问法1的
机制段从Dense第28变成第10、重排第7变成第1；不能把改善单独归于“召回更好”或“重排更好”。
也不能推导“上下文总是噪声”：当关键错误文本或模型条件只存在于context时，省略它可能丢失必要信息。

### 全局候选＋同页补充：`--pool-policy page_lexical`

仅支持显式Hybrid，保留原 `flat` 为默认。对固定64候选预算：

1. 正常执行Dense64、BM2564和原RRF，得到全局64条候选。
2. 保留前32条；从全局排名中取最先出现的两个不同document ID。
3. 采用原全语料BM25分数和IDF，在这两页内各补最多16个尚未由前32条表示的父单元片段。
4. 不足的槽位从原全局候选补齐，最多64条交给同一个重排器；最终仍最多交付4个完整源单元。

页面由原检索排名选出；没有Riks、Boundary Conditions、目标unit ID或固定文档ID规则。继续执行
相同的产品/release范围约束和来源验证，不把邻居正文拼接成新的“原文”。新增一次全量BM25匹配扫描，
所以同一重排预算不等于同一计算开销。

Riks这次选中的是 `simakey-r-static` 与 `simaanl-c-postbuckling`。实际补入23条片段，目标Boundary
Conditions出现在最终候选池第45位。重排后的前四单元仍是Proportional Loading、*STATIC参数以及
Loads下的内容；目标不是因为正文长度、产品过滤或交付预算而消失。

## 证据如何固定

- 复用 `var/rag-headerless-pairs-v1` 的既有1,480页、33,383个子片段及Dense/BM25绑定，没有重建文档
  embedding或改动原文。来源、向量/HNSW、BM25数据库和历史结果哈希均核对。
- 源请求保持原五条已暴露development查询及五条符号/版本/预算回归。新请求文件为
  `recall_policy_requests_v1.json`，旧请求文件不改动，必要unit ID不更换。
- 固定MiniLM encoder、`ms-marco-MiniLM-L6-v2` cross-encoder权重、原BM25/RRF参数；候选64、返回4，
  embedding256、rerank pair512、单元正文1200和整体证据4000的预算不变。交付计数是WordPiece代理，
  不是生成模型计费token。
- 四组配置和实现先冻结再执行；manifest SHA-256为
  `f9e10db1920fedab445550185756e5caae7e464b8e978f3d74fc46351705ac49`。本批没有追加组合策略、调权重、
  提高Top-k或按已知答案改query。原20项control的排序分数差均为0。
- 实际使用本地embedding和重排推理，0生成式模型请求、0Agent运行、0Abaqus/Solver。

## 实现与验证边界

改动仅在显式候选的 `unified_retrieval.py`、`unified_candidate_backend.py` 与其CLI；新增16项测试覆盖
query字段保留、范围一致、无隐式符号路由、页面选择、独立父单元/配额、补位、低预算、来源错误和后端
违反范围时的fail-closed。相关38项测试通过；全仓852 passed、27项持证测试按默认条件跳过。
修改模块BasedPyright为0 errors/0 warnings；Ruff通过。

在原候选CLI的同一命令中，可独立增加：

```text
--query-text-mode question_only
```

或使用：

```text
--retrieval-mode hybrid --pool-policy page_lexical
```

本批没有评测两者组合，不能把各自局部结果相加。两个开关都不注册到产品Agent，不改
`search_abaqus_docs` 的默认行为，也不改变API Catalog。本次没有新的独立查询泛化证据或任务收益证明。

## 保留结论

- Riks：已有不依赖答案规则的入池改善，但现在还必须解决重排对所需约束证据的选择；不能宣布已修好。
- warning：问题正文优先有值得继续验证的信号，不足以成为普遍丢弃context的理由。
- 后续应在固定候选池上分离检查问题表示与重排选择，再用未参与调试的问题验证；当前不切换默认配置。

[机器报告](recall_policy_ablation_v1_20260910.json)保存逐项指标、页面补充轨迹、选中证据metadata与哈希；
手册正文及完整排名仍保留在本地 `var/audits/recall-policy-ablation-v1/`。运行和只读汇总脚本分别为
`var/audits/run_recall_policy_ablation_v1.py`、`var/audits/summarize_recall_policy_ablation_v1.py`。
修改前的三个候选实现和两个既有测试另保存在 `var/audits/riks-recall-prechange-v1/`，没有覆盖历史结果。
