# 官方正文补齐与发现视图v2

日期：2026-09-14。结论：**正文补齐、v2实现与首次开发回放完成；关键中文操作仍2/3，未通过可用性门槛。** 实施范围是在已有Catalog上补官方对象用途和当前方法Table data，复用lookup读取，构建一个独立v2视图；没有增加Coder发现工具、Guide、生成模型、运行权限或检索组件。

## 实际改动与内容验证

- 新候选`var/api-catalog/abaqus-2024-reference-context-v2`：5224方法卡、150成员、47冲突、16条runtime notes。与docstrings-v1相比，仅新增官方record的context；所有原方法字段（含abqpy说明）、成员及源绑定相同。
- 采入760份唯一对象用途、122份唯一Table data，逐份核对源文件hash（760个源文件）。对象用途排除页面目录；Table data按所属article取，不混邻近方法。
- 对象用途与方法表格分别标注kind、source/release/anchor、源文件SHA-256；同时保存有序文本及选定片段的XML序列化。它不是原文件byte-for-byte副本，原文件由source hash绑定。
- 普通方法lookup增加`object_descriptions`；`Material.Elastic.table`参数lookup附加对应官方方法的`table_data`，不替用户挑选type。非table参数不无差别附加长表；离线`read`返回完整内容，`.__doc__`仍只读abqpy方法原文。
- 确认Elastic中ISOTROPIC、Young's modulus、Poisson's ratio及后续SHEAR分支的顺序，SectionAssignment的装配层connector限制可读。15份正文含较复杂MathML，保留XML并返回警告。源文档的ν是U+03BD且已保留；早先终端显示异常不等于源文件字符损坏，未作猜测修复。
- 原有61项核心检查通过；legacy基准26/27，按1A已声明openOdb来源修正视图为27/27。原始基准没有被改写。

## v2视图与兼容

仍然一Card一视图、同一BM25算法和multilingual-e5-base固定checkpoint、原始查询、Top-5。没有换模型、翻译、RRF、reranker、GUI硬过滤或新的拒答阈值。

统一选择顺序：对象用途→按官方/Stub变体标记的摘要与公开路径→表格摘录→参数说明。官方参数跨变体交错，避免只给第一个重载留参数空间。只去重明确相同的显示文本，不推导factory与`__init__`的runtime等价。

视图继续最多1000字符；完整句/行摘录上限分别为对象用途350、方法摘要280、表格450、参数220字符，再按总预算逐段装入。这些是在本批排名前确定的定位视图规则，不是回答长度限制；未收录的正文留在Card。1798条视图标记shortened，不能把搜索候选当完整合同。内容构建阶段的token检查没有超过E5的512-token窗口。

投影由manifest区分`api-discovery-view-v1/v2`，CLI `index --projection api-discovery-view-v2`显式选择；默认仍v1。旧docstrings-v1及其5374条索引已用当前代码成功重验，原views hash仍为`bbf9ac753a8b0c925885186c512183f768ae9bb7c0f3fff16b7141629c069b0c`。不覆盖final15、docstrings-v1、原索引或原报告。

## 原开发集首次回放

原36题原样执行一次；6项Exact均通过，所有返回候选均可按ID读取并匹配内容hash。

| 配置 | 中文Hit@5 | 英文Hit@5 | 混合Hit@5 | 总Hit@5 | 总MRR@5 |
|---|---:|---:|---:|---:|---:|
| v1 BM25 | 0/12 | 5/12 | 7/12 | 12/36 | 0.242 |
| v2 BM25 | 0/12 | 9/12 | 8/12 | 17/36 | 0.296 |
| v1 E5 | 2/12 | 6/12 | 7/12 | 15/36 | 0.356 |
| v2 E5 | 2/12 | 10/12 | 7/12 | 19/36 | 0.324 |

关键中文操作：截面赋值未命中→第3名；单元类型第1→第3名；写INP第1→未进入Top-5。因此仍为2/3，并非修好截面后全部通过。

BM25新增5次命中、没有丢失原命中。E5新增7次（英文草图、材料、静力步、位移边界；混合草图、截面；中文截面），丢失3次（混合新建模型、混合拉伸实体、中文写INP）。E5总命中增加，但MRR下降，不能笼统声称排序全面改善。

Elastic英文现为第1名，但中文仍未命中；中文草图、静力步等仍有相邻功能或GUI候选。两条无关/缺必需事实观察题，BM25仍无候选，E5仍返回候选，未获得可靠拒答能力。源码/正文补齐证明内容确实可访问，不能代替跨语言检索或模型正确使用的证据。

v2独立views SHA-256：`60c6521e34980590038113becd7e639736be1aeeb0668245137461fade92589a`；vectors SHA-256：`62a3799c648ea227c0b3070fbd3a0275cb7d2b417621858171b46dc2fcb4854b`。5374条编码完成，E5绑定与v1相同。首轮后未改题、投影、Top-K或重跑追分。

## 证据与复核

本地证据：`var/audits/api-reference-context-v2-20260914/content.json`、`core.json`；首次排名另存`discovery-first.json`。沿用[原开发问题](discovery_development_v1.json)，SHA-256为`c9c9f99630d380b62c78f9361997b7d2e4d80caa339ae64ecb37f34510164490`。旧v1结果见[首轮报告](docstrings_discovery_v1_20260914.md)，没有回填。

新methods SHA-256：`a826238b3b358d275f65c40a831ad2294db3f51b17e9d451e4a211c8e6120279`；members仍为`7f2b811dff9dcc5faf841fb2c7382c409c58ea29af142ce4989d13b779f441d0`。官方2026 FD02、abqpy 2024.10及2024目标runtime的身份不混合；全文存在不等于2024兼容性认证。

复核命令使用新输出名，避免覆盖本批首轮：

```powershell
uv run --no-sync python benchmarks/api_catalog/audit_reference_discovery.py `
  --base var/api-catalog/abaqus-2024-docstrings-v1 `
  --catalog-dir var/api-catalog/abaqus-2024-reference-context-v2 `
  --docs-root D:/SIMULIA/Documentation/2026LE/English `
  --index-dir var/api-catalog/discovery-reference-context-v2-e5 `
  --encoder-dir var/models/multilingual-e5-base-d128750 `
  --output var/audits/api-reference-context-v2-20260914/discovery-replay.json
```

验证：全仓离线1093通过、27持证未运行；之后增加重载参数区分和显式v2 CLI用例，相关四份测试合计13通过。改动产品范围Ruff/BasedPyright通过。未调用生成模型/Abaqus；使用本地E5编码，不称“零模型调用”。产品默认值、旧物理评测和uv.lock未改。

本批同时修改正文与投影，所得结果只说明组合在已知开发题上的表现，不分别归因新增字段，不作为新held-out或Agent建模收益。三个中文关键操作没有全部满足原定义，保持未通过可用性门槛，不接入Coder。后续如继续，应先核对跨语言编码/查询链路的基准一致性，再决定新实验，不在本批继续加规则。当前内部向量自洽检查并不是与官方参考实现的输出对齐验证。
