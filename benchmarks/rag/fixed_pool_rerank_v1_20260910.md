# 固定候选池重排：warning排序问题得到隔离，Riks仍未解决

日期：2026-09-10。先执行固定池单变量对照，再独立实现并验证仅重排使用问题正文的显式模式。

## 固定池结果

上一批四种配置各有五条自然语言查询，共20个已保存的64片段候选列表。本轮保持每个列表的内容和
顺序不变，同一cross-encoder分别接收“问题+背景”和“仅问题正文”，共40次重排。20个原条件的
重排顺序、分数、pair长度及最终证据全部精确复现。

下表为必要证据的最佳子片段排名，左侧是完整问题，右侧是仅问题正文：

| 固定池来源 | Riks边界条件 | warning问法1 | warning问法2 |
|---|---|---|---|
| full-dense | 两种输入均无目标片段 | 7→1，开始送达 | 1→1，保持送达 |
| full-hybrid | 两种输入均无目标片段 | 9→1，开始送达 | 3→4，保持送达 |
| question-dense | 两种输入均无目标片段 | 7→1，开始送达 | 1→1，保持送达 |
| page-hybrid | 34→21，仍未送达 | 8→1，开始送达 | 3→4，保持送达 |

这次排除了候选池变化这一混杂因素：warning问法1的局部改善不要求改变召回，只改变重排输入就可以
复现。不能进一步归因于某一个词、长度效应或模型内部机制，也不能推广为任意问题都应省略context。
问法2在两个池中的排名反而从3降到4，负向变化原样保留。

Riks只在page-hybrid池里拥有必要段落；其排名虽改善到21，却仍在最终4单元之外。因此删除背景不是
Riks的充分解法。其余三个池里根本没有目标片段，重排当然不能补出缺失证据。

## 已实现的显式模式

候选CLI新增：

```text
--query-text-mode rerank_question_only
```

它与已有模式的区别是：

| 模式 | Dense/BM25召回文本 | 重排文本 |
|---|---|---|
| `question_context`，默认 | 问题+背景 | 问题+背景 |
| `question_only` | 问题 | 问题 |
| `rerank_question_only`，新增 | 问题+背景 | 问题 |

新模式不改product/source_release范围、候选数量、RRF或重排权重。trace分别保存真实的
`retrieval_query`、`rerank_query`及两阶段是否省略context；encoder预算按实际召回文本检查，pair预算
按实际重排文本检查。空的重排问题直接拒绝，不能借短重排问题绕过完整召回文本的encoder长度限制。
默认仍为question_context，产品Agent的 `search_abaqus_docs` 没有切换到这个候选流程。

## 完整流程验证

固定池对照结束并封存代码后，再以新模式运行Dense、Hybrid与page-hybrid三种完整流程，每种重放原10项
回归，共30项检查。15条自然语言配置的实际召回query、Dense/BM25列表及最终候选池均与原配置一致；
其重排分数、顺序和交付证据均与固定池question-only对照一致。其余15项符号、版本和预算检查也符合
原结果，合计30/30一致。

三个新模式profile的三项必要证据交付均为2/3：两个warning问法送达，Riks仍未送达。三项检查只有
两个唯一目标源单元，不能把不同池/同题回放当作独立任务样本；API问题与Riks总览没有必要单元标注，
对它们只检查流程一致性，不能据此声称答案质量通过。

新增3项合同测试覆盖独立输入、正确预算和空问题拒绝；相关60项测试通过。全仓855 passed、27项持证
测试按默认条件跳过；修改模块BasedPyright为0 errors/0 warnings，Ruff通过。

## 可复核边界

- 固定池阶段不调用Dense/BM25搜索或query embedding，只做本地重排。回放适配器为复用现有单列表
  服务接口而实现名为dense的方法；原始trace的mode=dense只是该服务分支，不是实际向量检索。trace
  已另标明 `backend_kind=frozen_pool_replay_no_retrieval`，不能将回放耗时当作向量检索延迟。
- 固定池manifest：`1c25b63aa2cb8f3675e23c22bb4f43ffd68b3426682a3649dbef284df510c51f`。
  输入候选ID、实际评分片段文本hash、原始query、模型及源码在推理前绑定，没有新增答案词或调权重。
- 完整流程阶段manifest：`8cf88e5dc8b2ab1501362154c7d54afaa274ce60c0753ea30549e42c9eeeaddb`。
  原索引和历史结果保持不变，使用本地query embedding与重排，0生成式模型请求、0Agent、0Abaqus。
- 模式实现前的13份源码/输入在 `var/audits/rerank-query-split-prechange-v1/` 中保存原bytes，快照hash
  与既有manifest一致。旧实验记录不修改；复现旧版本需要使用对应源码，而不是绕过hash检查使用新代码。

本轮明确的进展是隔离并实现了warning问法1的局部排序修正。没有新问题泛化、完整答案或下游Agent收益
证据，不能据此切换默认。Riks后续需要验证问题的语义表达或多约束证据选择，不能继续把提高Top-k或
简单删除背景当作已证明的修复。

[机器报告](fixed_pool_rerank_v1_20260910.json)保存两个阶段的manifest、逐项指标、代码快照映射与原始
结果hash，不复制手册正文。完整结果分别在 `var/audits/fixed-pool-rerank-v1/` 和
`var/audits/rerank-text-profile-v1/`；驱动为 `var/audits/run_fixed_pool_rerank_v1.py`、
`var/audits/run_rerank_text_profile_v1.py`，汇总为 `var/audits/summarize_rerank_text_v1.py`。
