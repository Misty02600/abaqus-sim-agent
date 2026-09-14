# 统一检索候选 v1：实现与约束通过，必要证据指标尚未提升

统一的显式离线流程已实现：类型化查询 → 共同适用范围 → 片段召回 → 重排 → 源单元归并 → 独立上下文预算交付。复用现有Chroma、BM25、cross-encoder和源证据单元，不替换默认Agent检索，不引入新的搜索服务或生成式查询改写。

## 实际改变

| 环节 | 候选流程行为 |
|---|---|
| 查询入口 | `kind=question`与`kind=symbol`严格分开；自然语言路径不扫描大写词来生成exact命中。真正的AT、*STATIC通过显式symbol字段查询 |
| 适用范围 | 从已核对原文件读取官方Products；五条审阅声明按源hash、唯一DOM ID和完整标题约束章节。既有`section_product_mentions`不作为硬过滤证据 |
| 未知范围 | 保留并标为unknown，不因为缺标签就认定不适用。文档release不是runtime兼容性证明 |
| 共同过滤 | Dense在Chroma查询前用父单元排除集过滤；BM25在最终Top-K截取前使用同一集合，没有“先取浅Top-K再过滤”的漏召回。BM25保持原全语料IDF |
| 候选排序 | 默认Dense64；显式Hybrid按两路各64片段、固定RRF合并后取64，再进行同一cross-encoder重排。候选与最终返回数量分开 |
| 证据归并 | 重排的是片段，之后按父单元ID归并；同页不同单元不互相排斥。恢复已有完整body，无邻居猜测拼接 |
| 独立预算 | query embedding上限256、重排pair上限512；完整证据body独立上限1200；序列化证据列表总上限4000，含来源/标题等metadata。超过预算记录原因并跳过，不截断，继续扫描有界候选，最多交付4单元 |
| 来源完整性 | 校验子片段正文、ID、精确整数偏移、版本、标题/章节、anchor、XPath、URL和源hash；符号卡另外通过既有源解析器重建并比较正文与release |

这里的1200/4000是显式候选预算，不是最佳参数结论。交付计数器与embedding计数器独立注入；当前配置用已声明的MiniLM WordPiece代理计数，不是DeepSeek计费token。4000约束序列化证据列表，不包含仅供宿主保存的完整trace或未来Agent工具信封。

共36,897个源单元中，31,294个具有上述官方/审阅范围，5,603个为unknown；五个章节声明覆盖68个单元。对本批Standard查询排除2,919个确认不匹配单元。该统计包含单元库中的非搜索单元，不等于检索覆盖率；页级共享Products也不能保证每个片段都在物理上适用。

## 固定回归结果

固定批次由五条此前真实Go/Flash发出的development查询，加五个显式符号/版本/预算检查组成。请求及范围声明在查询前固定；未使用新held-out，也没有重跑生成式Agent或Abaqus。

| 项目 | Dense候选 | 显式Hybrid候选 |
|---|---:|---:|
| 类型、路由、来源、范围和预算流程检查 | 10/10 | 10/10 |
| 三项预先声明的必要源单元交付检查 | 1/3 | 1/3 |
| 自然语言查询自动exact置顶 | 无 | 无 |
| 显式AT / *STATIC查询 | 均可返回 | 均可返回 |
| 不存在符号 | not_found | not_found |
| 指定无数据的2024文档release | not_found，不回退 | not_found，不回退 |
| 超出encoder长度的查询 | 推理前拒绝 | 推理前拒绝 |

两种候选都找到了第二条warning所需的完整时间增量单元；第一条warning与Riks边界条件检查仍未取得要求的源单元。API查询仍偏向参数研究结果、节点集定义，并未解决脚本成员覆盖问题。**流程10/10不是任务成功率，`found`也不代表答案已验证。**

五条自然语言请求的序列化证据token量依次为：

- Dense：2020、2075、2036、3010、2582。
- Hybrid：2020、2075、2036、2344、1989。

本批都在独立交付预算内，零正文截断。与此前384-token完整单元实验不同，500-token完整父单元现在有明确的正向合同测试；不能再把embedding长度当作完整证据的拒绝依据。

最终Dense五次自然语言查询约2.57–3.07秒，Hybrid约4.35–5.38秒；这两个进程曾同时运行，数字只是本地观察，不是受控延迟比较。来源/索引核验和模型加载的启动开销分别约14.4与17.0秒，另列，不混入单次查询。

## 验证与保留事项

- 22项新增测试覆盖显式路由、AT回归、真正符号查询、相同前置过滤、unknown保留、忽略不可信产品提示、同页多个父单元、完整500-token单元、输入/重排/输出预算、来源伪造、章节声明漂移/冲突、BM25不能浅Top-K后过滤等。
- 相关10个测试文件合计 **102 passed**；三个新模块BasedPyright为0 errors/0 warnings，Ruff与格式检查通过。
- 复用已有1,480页、33,383个检索子片段的候选索引；不重新生成文档embedding或重建索引。本批仅执行本地query embedding、检索和重排，没有生成式模型或Abaqus调用。
- 候选Dense向量/HNSW及符号索引前后核验；BM25、原单元/子片段文件的事后hash仍匹配既有绑定。所有源文件重新核对。
- 首轮Dense记录保留在`unified-candidate-v1-dense`；之后增加符号卡原文重建校验、重复ID和父单元可检索性检查，最终两路均使用`verified`目录。没有根据检索失败改题、调排序权重或追加gold。

默认产品CLI及其历史大写符号提取逻辑**没有改变**。这些改进只在本显式候选中生效；没有宣称已经修好默认Agent的AT问题。Hybrid仍不默认启用，范围规则也不外推为全部Abaqus章节的适用性判定。

## 使用

请求文件示例：

```json
{"cases":[
  {"id":"warning","request":{"kind":"question","question":"What does this exact-output-time warning mean?","observed_context":"OUTPUT AT EXACT, PREDEFINED TIME POINTS WAS REQUESTED IN THIS STEP.","product":"Abaqus/Standard"}},
  {"id":"at","request":{"kind":"symbol","symbol":"AT","symbol_kind":"output_variable"}}
]}
```

在仓库环境运行，output必须是新目录：

```powershell
uv run python -m abaqus_sim_agent.evaluation.unified_candidate_cli `
  --candidate-directory var/rag-headerless-pairs-v1 `
  --symbol-index-directory var/rag `
  --scope-rules benchmarks/rag/unified_candidate_scope_rules_v1.json `
  --requests benchmarks/rag/unified_candidate_requests_v1.json `
  --output var/audits/my-unified-candidate-run `
  --reranker-directory var/models/ms-marco-MiniLM-L6-v2/233902d25c440f23af6f7d6e94d2946bac0bee0a
```

默认`--retrieval-mode dense`，显式`--retrieval-mode hybrid`才使用BM25/RRF。自然语言question中的runtime版本不会自动成为文档release硬过滤；只有显式`source_release`才作精确文档版本约束。

## 证据与当前结论

- 实现：`evaluation/unified_retrieval.py`、`unified_candidate_backend.py`、`unified_candidate_cli.py`。
- 固定输入：`benchmarks/rag/unified_candidate_requests_v1.json`、`unified_candidate_scope_rules_v1.json`。
- 汇总：`benchmarks/rag/abaqus_2026_unified_candidate_20260909.json`。
- 全部候选、排名、适用范围、拒绝记录、计数和实际正文：`var/audits/unified-candidate-v1-verified-dense/`、`unified-candidate-v1-verified-hybrid/`。

当前可称为：已有统一、可复现、来源可追溯且预算分离的显式RAG候选流程。**工程缺陷可以持续修复，但目前仍没有证据将它描述为检索质量提升或Agent任务收益。** 保留该候选供后续使用，不切换产品默认。
