# Riks 单页：LLM 最终检索块试验

本次模型生成的是实际参与 embedding 和检索的块，不是固定排名后的父块扩展。

## 范围与方法

- 来源：已绑定 SHA-256 的 Abaqus 2026 FD02 Riks 页面。使用现有确定性正文、公式文本表示及源标题；原始 XML 留在来源库中。
- 16,275 字符，138 个约 50-token 的定位单元。定位单元不直接作为最终检索块。
- 参考 Chroma `LLMSemanticChunker` 的编号边界方法；使用已安装的 LangChain 递归切分器，没有复制上游代码或提示词。
- 模型为 OpenCode Go / DeepSeek V4 Flash，明确请求关闭 thinking，使用真实客户端 User-Agent 与稳定 session header。
- 共两次模型请求，耗时 2.73 / 2.46 秒，输出 143 / 125 token；响应中未报告思考 token。原始响应已在预算检查前保存。

## 结果

| 方法 | 最终块数 | 平均块正文 token | 完整取得标注证据 | 平均返回源 token |
|---|---:|---:|---:|---:|
| 普通递归切分 | 19 | 181.58 | 4/4 | 784.25 |
| LLM 分组＋长度保护 | 28 | 123.21 | 4/4 | 574.75 |

两组各自生成向量和索引，分别执行 Dense20＋BM2520、相同 cross-encoder、Top-4 最终块，不展开父块。没有编码器或重排输入截断；两组拼接均精确恢复同一份规范化文本。所有默认索引未改变。

这四个源知情开发问题中，LLM 候选保留相同标注证据时平均少返回约 26.7% 的源 token。但候选平均块更小、且语料仅一页，不能据此认定普遍更优，也不能当作答案正确率或 Agent 收益。

## 不能隐藏的失败与边界问题

1. 模型两次均给出覆盖完整的 27 组边界，但都违反了原始硬长度约束。第二次仍保留一个 403-token 的公式说明组，超过本试验的 238-token 正文上限。两次原始请求记录继续标为 failed。
2. 索引试验是显式后验工程候选：使用最后一次已返回的计划，只对超长组用同一递归规则拆为两块。保留全部 LLM 边界，新增一个长度保护边界，未追加模型调用。因此结果不是“纯 LLM 严格遵守长度上限”。
3. 内容检查发现：最终块 10 以 `Abaqus/CAE Usage` 标签结束，对应代码却位于块 11；块 11 还混入下一节的终止条件说明。这是模型原始边界问题，不能被 4/4 证据指标掩盖。
4. 长度保护把公式及后续变量说明分到最终块 8、9。该查询确实同时取回了相关片段，但单块并不独立完整。
5. 少数边界把句末标点留给下一块。规范化文本没有丢失，但边界可读性仍不理想。

结论：编号边界路线可以快速、低输出成本地产生实际检索块。当前更适合继续作为“小型 LLM 切分＋通用结构/长度检查”候选，而不是直接替换默认入库流程。不按页面手工修边界，也不把大小算术完全交给模型。

## 证据

- 机器报告：`abaqus_2026_llm_final_chunks_riks_length_guard_20260908.json`，SHA-256 `2319ff92ac2b0ba12679609931238ff0cc3a47fd8675342e3654806159315bc0`。
- 原始请求/响应：`var/rag-llm-final-chunks-riks-v1/`。
- 实际分块与索引：`var/rag-llm-final-chunks-riks-v1/length-guard-candidate-v2/`。
- 第一份本地索引准备因缺少 BM25 所需的索引 manifest 而停止，保留在 `length-guard-candidate/`；未执行检索。修正后的目录独立创建，模型输出未重跑。
- 新增边界合同测试后，全套测试 694 passed、27 skipped；新核心模块通过 Ruff / BasedPyright。

参考：[Chroma 技术报告](https://www.trychroma.com/research/evaluating-chunking)、[上游实现基线](https://github.com/brandonstarxel/chunking_evaluation/blob/e708410d1c61cb76a85cd9d433630ef89b9c6b85/chunking_evaluation/chunking/llm_semantic_chunker.py)、[DeepSeek thinking 配置](https://api-docs.deepseek.com/guides/thinking_mode/)。
