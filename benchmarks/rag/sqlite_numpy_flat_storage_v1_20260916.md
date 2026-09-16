# SQLite + NumPy Flat 文档索引迁移 v1

2026-09-16。目标是移除产品运行路径中的 LangChain/Chroma 依赖，同时保持官方文档、metadata exact、MiniLM Dense、自研 SQLite BM25 与 RRF 的既有职责。旧索引、旧 seal 和旧报告不改写；本次只验证存储与适配层替换，不声称新检索算法收益。

## 实现边界

- 项目内 `Document` 数据类和 embedding protocol 替代 LangChain 类型；未使用的 `BaseRetriever` 消失。
- 本地 lxml 标题切分与确定性长度切分替代 LangChain splitter。新建索引使用新 schema/profile，不宣称与旧 chunk bytes 相同。
- Dense 使用 `documents.sqlite3` 保存正文/metadata，`vectors.npy` 保存按 row 对齐、L2 归一化的 `float32` 矩阵；查询使用 NumPy exact cosine 和 `argpartition`，不实现 ANN。
- 固定本地 all-MiniLM-L6-v2 ONNX/tokenizer 由项目 encoder 直接执行；不下载。与 Chroma `DefaultEmbeddingFunction` 对两条样本文本逐 float 完全一致，最大绝对差为0。
- MiniLM 的 pooling、padding/truncation 与归一化语义适配自 Chroma 1.5.9 `onnx_mini_lm_l6_v2.py`（Apache-2.0）；项目实现移除下载、retry与Chroma接口，模型文件继续绑定其来源与SHA-256。
- 自研 `abaqus-lexical-v1` / Okapi BM25 SQLite 和 RRF 保留，不改成 FTS5，也不改变产品 Concept 默认模式。
- `numpy`、`onnxruntime`、`tokenizers` 成为产品直接依赖。当前源码与依赖不再保留 Chroma/LangChain compatibility；普通产品依赖树不含 Chroma、LangChain、Kubernetes或Chroma telemetry。历史索引由旧提交和冻结报告解释。

## 本机规模与性能

当前 context-v2 为39,019条、384维。向量矩阵57.16 MiB；矩阵已加载时，12次全量 cosine Top-20 扫描中位数2.08 ms、最大33.05 ms。该结果只证明本机低QPS exact Flat可行；不包含query ONNX编码、SQLite候选读取或冷启动。

从 `var/rag-context-v2` 原样导出文档与已存向量后：

| 存储 | 大小 |
|---|---:|
| 原 Chroma SQLite + HNSW + BM25 | 约434.69 MiB |
| 新 SQLite documents/metadata索引 + NumPy vectors + 同一 BM25 | 约240.38 MiB |

减少约44.7%。新 dense manifest绑定原manifest与HNSW快照、encoder文件hash、documents SQLite和vectors NPY；迁移不重新编码语料。常用标量metadata为显式列，`products`与`symbols`为带索引的关联表，Dense在NumPy评分前由SQLite筛选候选row。

## 排名回放

使用相同 context-v2 语料运行31条开发查询：自然语言主切片的BM25、Dense、Hybrid Recall@5与MRR均与旧Chroma对照一致。BM25的31条排名完全一致；exact Flat替代HNSW后，Dense有4条、Hybrid有6条内部排序变化，主要位于identifier查询。

另将 seal 原先绑定的38,913条 `var/rag` 语料原样迁移，并对已消费 held-out v3 作 post-hoc 存储回归：

| Retriever | Complete Hit@5 | Requirement Recall@5 | MRR |
|---|---:|---:|---:|
| BM25 | 0.767 | 0.839 | 0.596 |
| Dense | 0.800 | 0.878 | 0.656 |
| Hybrid | 0.833 | 0.906 | 0.702 |

三组汇总与历史报告逐项一致。BM25 30/30 排名相同；Dense有1条、Hybrid有3条内部排序变化，不改变指标。该 replay 已见过标签，只支持存储迁移回归，不能作为新的 held-out 调参或质量提升证据。

## 产物与采用边界

本机当前候选位于 `var/rag-sqlite-flat-v3-minilm-final-sql`；seal语料回归候选位于 `var/rag-sqlite-flat-v2-minilm-compat`，均为忽略目录。固定encoder副本位于 `var/models/all-MiniLM-L6-v2-chroma-913d730`，保存来源、archive SHA与逐文件hash。旧 Chroma 索引继续服务历史证据，不由当前代码原地升级。产品切换必须绑定 schema v2 manifest，旧 run仍按保存合同和冻结源码解释。
