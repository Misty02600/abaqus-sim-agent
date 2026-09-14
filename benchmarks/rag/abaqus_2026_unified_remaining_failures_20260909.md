# 统一候选剩余两项失败：只读定位

本次只读取已冻结Dense/Hybrid结果、源单元库、子片段库与对应官方原文；没有重新检索、重排、改query、调整参数或修改代码。相关文件hash与上一批报告绑定一致。

## 定位结果

| 必要源单元 | Dense候选 | BM25候选（Hybrid） | 重排 | 实际失败阶段 |
|---|---|---|---|---|
| Riks / Boundary Conditions | 不在前64 | 不在前64 | 未参加 | 候选召回 |
| 精确输出时间 / Time Incrementation | 首个子片段第28 | 两个子片段第9、18 | Dense第7；Hybrid第9、17 | 重排后未进入前四单元 |

两项都没有预算拒绝。Riks目标完整正文63tokens、检索子片段86tokens；警告目标完整正文236tokens、两个检索子片段231/93tokens。它们在源库中均为可搜索text/content，原文hash匹配；Riks页官方Products含Standard，警告目标位于已审阅的Standard章节。因此不是文档缺失、结构无法表示、产品范围被排除或交付超长。

### Riks：相关主题取代了所需边界条件规则

两路最终返回相同四个单元：Proportional Loading、*STATIC的RIKS参数说明、可用力载荷列表，以及Loads下的幅值说明。

这些材料解释比例载荷和force-type loads，但没有返回被标注为必要证据的Boundary Conditions段落。原query强调“non-zero prescribed displacements”和“proportional loading”，没有直接使用“boundary conditions”术语；前列候选集中在Loads是观察事实，**术语错配是否是主要原因仍是待验证假设**，不能据此宣称embedding能力不足或需要更换模型。

目标段落说明可施加边界条件的自由度和Riks中不能用amplitude definitions改变其幅值；它本身也不完整回答所有“非零规定位移如何随比例因子变化”的细节。必要源单元指标不能替代完整答案验收，本次不改既有gold。

### 时间点警告：已召回，但直接机制解释排得不够靠前

Dense将TIME MARKS、TIME POINTS、消息文件说明、FREQUENCY四个单元排在前面；目标机制段落的首个子片段重排第7，未进入前四单元。

Hybrid额外将Direct Cyclic及其低周疲劳场景的“精确时间输出不受支持、会改成近似时间输出”排在前列，目标首片段降到第9。它仍在融合后的重排池中，**不是被RRF候选截断丢掉**。

特殊过程的限制和一般时间增量调整机制不是同一规则；同为Standard不能保证适用于同一分析过程。但当前问题没有给出确切procedure，不能因此擅自补造procedure过滤条件或把这些特殊材料全部硬删。这里需要检查重排对“直接回答、参数说明、特殊过程限制”的区分，而不是扩大完整单元预算。

## 当前结论

两项失败需要分开诊断：一项没有进入候选，一项已进入但排序不够靠前。统一流程、父单元展开和更宽交付预算不可能单独解决二者。

这定位的是**既定必要源单元未送达**的原因，不证明模型最终答案错误，也不是整个RAG的成功率。没有进行新的算法选择、阈值调参或产品默认变更。

证据入口：

- `var/audits/unified-candidate-v1-verified-dense/domain-procedure-applicability-2.json`
- `var/audits/unified-candidate-v1-verified-hybrid/domain-procedure-applicability-2.json`
- `var/audits/unified-candidate-v1-verified-dense/recorded-solver-diagnostic-1.json`
- `var/audits/unified-candidate-v1-verified-hybrid/recorded-solver-diagnostic-1.json`
- 源单元/子片段：`var/rag-headerless-pairs-v1/units.jsonl`、`children.jsonl`。
- 上一批绑定：`benchmarks/rag/abaqus_2026_unified_candidate_20260909.json`。
