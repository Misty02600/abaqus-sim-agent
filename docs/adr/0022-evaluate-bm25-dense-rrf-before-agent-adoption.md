# ADR-0022：先独立评测 BM25、Dense 与 RRF，再决定是否接入 Agent

## 状态

已采纳

## 日期

2026-09-03

## 遇到了什么

原 retrieval benchmark 把查询类型和算法混在一个 `mode` 字段中。所谓 `augmented` 由 benchmark
直接提供正确的 `exact_terms`，然后把 metadata 精确命中放在 Dense 结果前面；它证明了已知标识符的
路由价值，但不是稀疏倒排检索，也不能说明 BM25 与 Dense 是否互补。

Concept tool 已进入真实 Coder 调用链，但仅暴露工具时 3/3 均未查询；prompt-only preflight 让 3/3
各查询一次，任务结果仍与 control 同为 2/3。因此不能因为工具可调用，就继续把未经对照的 Hybrid
检索接入 Agent。

## 决定

- 保留原 exact-symbol-augmented Dense 路径和冻结报告，作为独立的精确符号路由证据。
- 使用标准库 SQLite 实现持久化 BM25 倒排索引，由本项目显式保存 `documents / terms / postings`，
  不引入内存型 BM25 包或外部搜索服务。
- 冻结 `abaqus-lexical-v1` tokenizer、Okapi BM25 `k1=1.2 / b=0.75` 和正值 IDF 公式，不在首轮
  benchmark 结果出来后调参。
- BM25 与 Dense 只读取相同的 `retrieval_role=semantic` records，先按父 `document_id` 去重；
  `lookup_symbol` 使用的 exact records 不参加三臂比较。
- Hybrid 使用 RRF，以两路 top-20 父文档排名和固定 `rrf_k=60` 计算，不归一化或直接相加不可通约的
  BM25 score 与向量 distance。
- 新 benchmark 以 31 个唯一 query 为中心，schema 禁止 `mode / pair_id / exact_terms / products`；
  每个 query 的 BM25、Dense、Hybrid 结果来自同一次两路检索。
- 主要结论使用 21 条自然语言 query；10 条 identifier lookup 只作为诊断 slice，不能抬高总体结果。
- 只有 Hybrid 在冻结或 held-out retrieval benchmark 上出现可信收益，并在任务级消融中表现出下游
  改善后，才考虑替换当前 Coder Concept tool 的检索路径。

## 实现边界

- `bm25.py` 创建内容寻址的 SQLite 文件，最后原子替换 `lexical/manifest.json` 指针；旧数据库不在
  构建时删除，避免 Windows 打开文件替换问题。
- manifest 绑定 Dense manifest、semantic corpus 和 SQLite 文件的 SHA-256，以及 collection、release、
  文档数、词表、posting、平均文档长度和算法参数。
- 加载时校验 manifest、数据库 hash/size、SQLite `quick_check`、统计量和语料 hash；组合索引还会从
  当前Chroma重新计算semantic corpus hash并与BM25对照，Hybrid查询和整批comparison均在执行前后
  复核。缺失或不匹配时fail closed，不静默回退为Dense。
- `compare` 在计时前预热一次，然后分别记录 BM25、Dense、fusion 和串行 Hybrid 延迟；报告保留逐
  Case 排名、slice 指标、paired delta 与 BM25/Dense 命中互补性。
- 语料仍为 Abaqus 2026 FD02，而产品 runtime 是 Abaqus 2024；该检索不能替代 2024 API Catalog、
  runtime evidence 或 task oracle。

## 首轮结果

本机从已有 Chroma 回填 32,466 个 semantic records，约 21 秒生成 16,690 个 term、1,298,647 条
posting 和 72.98 MiB SQLite 文件，无需重算 embedding。

21 条自然语言 query 的结果为：

| Retriever | Recall@5 | MRR |
|---|---:|---:|
| BM25 | 0.714 | 0.482 |
| Dense | 0.857 | 0.736 |
| RRF Hybrid | 0.762 | 0.643 |

BM25-only 命中 1 条，Dense-only 命中 4 条，两者共同命中 14 条、共同遗漏 2 条。Hybrid 相对 Dense
的 Recall@5 为 1 胜、17 平、3 负，平均下降 0.095。第二次运行的 93 份排名与全部准确率指标完全一致。

因此首轮结果证明两路存在少量互补，但固定 RRF 没有把互补转化为总体收益，反而降低 Recall/MRR 并
增加串行延迟。当前决定是保留 BM25/RRF 为真实、可复现的研究能力，不接入 Agent 产品路径，也不根据
这 31 条 development query 事后调整 tokenizer、权重或 RRF 常数。

## Held-out 后续证据

同日另按query-first流程构建gold-page/declared-family-disjoint held-out。v1在直接HTML标注时发现`DC3D8`和
`AC3D8`只能严格落到development gold；v2又发现C3D8P的替代element index仍属于同一continuum-element
family。两版均在查看任何held-out排名前撤回，并保留query hash与原因。

v3在排名前封存30条query、40个AND requirement、38个唯一gold、人工family registry、Dense/BM25
语料与数据库hash、五个实现文件hash，以及`top_k=5 / fetch_k=20 / rrf_k=60`。同一requirement内的
acceptable pages按OR计分，多requirement按AND计`Complete Hit@5`。全部HTML hash、identifier、body id、
section anchor和semantic-corpus存在性通过；审查者是独立Codex子任务，不是第二名人工标注者，因此
不声明inter-annotator agreement，family检查也只称declared-family disjoint。

首次且已消费的结果为：

| Retriever | Complete Hit@5 | Requirement Recall@5 | MRR |
|---|---:|---:|---:|
| BM25 | 0.767 (23/30) | 0.839 | 0.596 |
| Dense | 0.800 (24/30) | 0.878 | 0.656 |
| RRF Hybrid | 0.833 (25/30) | 0.906 | 0.702 |

Hybrid相对Dense为3胜、25平、2负，净增1题。它补回imperfection、geostatic-consolidation和
Explicit-to-Standard import，但丢失sequential thermal-stress与frequency/random-response的完整覆盖。
事后描述性paired bootstrap的Complete Hit delta 95%区间为`[-0.100, 0.167]`，requirement recall delta
为`[-0.050, 0.122]`，exact sign-test `p=1.0`。点估计与development结果方向不同，且不确定区间跨零；
因此该结果证明固定RRF在新页面上可能利用互补性，但仍不是一般优于Dense或应接入Agent的可信证据。
v3已标记consumed，不再用于调参。

v3的首次seal绑定了Dense manifest、semantic corpus、embedding名称和BM25数据库，但没有预先绑定Chroma
HNSW向量文件本身。消费后生成的vector-segment snapshot显示五个文件mtime均早于评测，当前规范digest为
`6a4d1d016d4371ddf0016ef6812853369a0225c3ce656fd8ab6c9c2af8c42896`；由于它是事后记录，不能
密码学证明首次排名使用的就是这些bytes。该限制不表明本次发生了向量漂移，但意味着v3不是完整的Dense
索引内容封存。后续held-out必须在首次排名前绑定vector-segment digest。

## 2026-09-04：受限 diagnostic advisory 后续

本决定没有把RRF升级为默认Concept路径。后续只增加了一个显式、默认关闭的Solver diagnostic advisory：
非超时的结构化warning/error触发独立预算的固定Top-4 RRF，结果附在同一Solver tool return；查询由任务
已有上下文与诊断词面机械生成，检索失败不覆盖原运行证据，mode与索引内容绑定并随resume保持不变。

RF3 development Case在Agent batch前冻结了不含答案的query formulation，并把正确Plane Stress section
排到rank 1。相同seed、Prompt、工具、预算、模型和host oracle的3×2对照中，Control与Advisory均3/3；
Advisory三次都实际送达rank-1证据，模型请求总数为38对42，但两臂tool/Abaqus/Solver均为50/15/6，且逐对
模型请求差为`-4/-1/+1`。因此该后续只确认受限链路可运行，没有观察到成功率或runtime调用收益，不触发
默认采用，也不能把RF3→RF1的单次修复称为RAG因果adoption。

## 没有采用的方案

- 将 exact records 只加入 BM25：这会让两臂语料不同，并把已有人工符号层优势算成 BM25 收益。
- 看到首轮结果后调整权重或 tokenizer：当前集合是 failure-driven development smoke，会产生调参泄漏。
- 直接把 RRF 设为 Concept tool 默认：retrieval aggregate 和既有 Agent 消融都没有支持这一改变。
- 引入 Elasticsearch、CrossEncoder、GraphRAG 或 LLM reranker：当前规模和证据不支持额外运行复杂度。

## 落实与确认

- BM25 与持久化：`src/abaqus_sim_agent/retrieval/bm25.py`。
- Dense、BM25、RRF API：`src/abaqus_sim_agent/retrieval/index.py`。
- query-centric scorer：`src/abaqus_sim_agent/retrieval/comparison.py`。
- CLI：`build-bm25`、`search --mode bm25|hybrid`、`compare`、`audit-heldout`、`evaluate-heldout`。
- benchmark：`benchmarks/rag/abaqus_2026_bm25_dense_rrf.jsonl`。
- 冻结报告：`benchmarks/rag/abaqus_2026_bm25_dense_rrf_evaluation_20260903.json`。
- sealed held-out、attempt ledger、consumption、首份报告与post-hoc统计：`benchmarks/rag/`中的
  `*heldout*v3*` artifacts。
- 确定性测试覆盖 tokenizer、手算 BM25、SQLite 重开/漂移、semantic-only 装配、RRF tie-break、
  query schema、同 query 单次两路检索和指标汇总。

## 与现有决定的关系

- 补充 [ADR-0010](0010-budgeted-official-concept-retrieval.md)：不改变其默认关闭和受预算工具边界，
  但纠正原 `hybrid` 术语并增加独立算法对照。
- 继续遵循 [ADR-0004](0004-evidence-first-physics-verification.md)：实现存在、检索命中、模型调用和
  任务收益分别留证，不能互相替代。
