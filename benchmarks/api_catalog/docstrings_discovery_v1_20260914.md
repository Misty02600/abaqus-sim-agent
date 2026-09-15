# 1B / 2A：说明可读与离线 API 发现

日期：2026-09-14。结论：**1B完成；2A工程实现和首次开发验证完成，但关键中文操作3项仅2项命中，尚未达到选定范围的可用门槛。** 不推进Coder发现工具接入，不宣称建模或任务收益。

## 范围与实现

- 1B静态解析固定abqpy wheel，不导入/执行它。每个签名对应自己的原始docstring、定义行范围、源文件/hash、摘要和参数说明。未知格式保留原文；Returns/Raises/Notes/Examples不另建结构模型。
- 原有lookup工具签名不变。普通方法查询返回新增摘要，`Method.parameter`返回独立来源的说明，`Method.__doc__`按需取原文，沿用查询预算/去重/trace。旧Catalog缺字段表示“未采集”，新记录的`absent`表示该定义确实无说明。没有新增生成模型工具。
- 2A离线CLI提供`index / discover / read`。共享Exact定位纯符号，保留歧义和错误owner的not_found；自然语言返回候选而非正确答案。原始问题不翻译，另列已识别符号。
- 同一5374条视图比较BM25与一个multilingual-e5-base；不加RRF、reranker、词表或查询生成器。视图包含规范名称、去重用途摘要、参数名（成员为描述及类型）；明确限制1000字符，15条标记shortened。完整Card没有截掉，候选带原card_id/hash、来源版本、release_alignment和lookup名称。
- E5使用现有ONNX Runtime/tokenizers，无新增依赖、无锁文件变更。固定模型本地校验后CPU运行，使用query:/passage:、attention-mask mean pooling、L2归一化；超过512 tokens明确失败，不静默裁切。全量编码5374条成功。分数不是置信概率，不设未经校准的“已找到答案”阈值。

这不是2024兼容性认证：官方参考为2026 FD02，stub为2024.10。原有runtime notes及签名/默认值不被说明文本替换。类级简介、Guide、依赖展开、Coder发现接入、真实任务运行均未进入本批。

## 1B检查

新候选：`var/api-catalog/abaqus-2024-docstrings-v1`，5224方法卡、150成员卡、47冲突、16条runtime notes。

| 定义关联状态 | 数量 |
|---|---:|
| 有参数段且完成所支持的解析 | 3026 |
| 仅摘要/未识别参数格式 | 1358 |
| 部分解析 | 467 |
| 源定义无docstring | 77 |
| 总定义关联数 | 4928 |

逐条对照wheel AST核验raw docstring、源码hash、定义起止行：4928/4928。此数不是公共API数量，也不是每段文字都已人工语义审核。467条partial的抽查主要是AFX/FX GUI文档只有参数名/类型、没有解释；未扩展解析器或补造描述。

与1A候选v3相比，去除新documentation字段后全部方法数据相同，成员相同；61项核心结构检查通过。29个核心方法均有原文/摘要和已抽出的参数说明。抽查SectionAssignment、setElementType、writeInput的官方/abqpy摘要一致，关键region/elemTypes/consistencyChecking说明可读，但这不代表全文语义一致。例如stub的setElementType.regions叙述包含ConstrainedSketchGeometry，仍保留其来源，不借抽取过程“纠正”成已验证运行合同。

legacy基准仍是原始26/27；使用1A已经说明的openOdb Python来源修正视图为27/27。没有修改原始基准，也没有把该历史差异归因于1B。

## 2A首次结果

排名前编写并固定[开发问题](discovery_development_v1.json)：12意图族×中文/英文/混合各一次，36条；另有6个Exact检查和2个不应获得可直接采用答案的观察题。问题和答案公开、选题来自已知核心范围，**不是held-out**。Top-5内出现预先声明的目标规范API才计命中；相邻构造器不事后放宽为成功。

| 基线 | 中文Hit@5 | 英文Hit@5 | 混合Hit@5 | 总Hit@5 | 总MRR@5 |
|---|---:|---:|---:|---:|---:|
| BM25 | 0/12 | 5/12 | 7/12 | 12/36 | 0.242 |
| E5 | 2/12 | 6/12 | 7/12 | 15/36 | 0.356 |

六个Exact检查通过；返回候选均可按绑定ID读回原卡并重验内容hash。预先选定的中文关键操作：setElementType与writeInput被E5列为第1名；SectionAssignment未进入Top-5，因此关键检查2/3，**不能宣称当前中文发现范围可用**。

失败观察与边界：

- 中文截面赋值的第3名是`SectionAssignment.__init__`，未返回目标`Part.SectionAssignment`；英文目标第1名。其他任务也出现factory/相邻构造器竞争。
- 中文网格播种、新建草图等出现GUI类候选；中文材料定义也未找到目标。当前全目录含GUI，未根据模型自述上下文增加硬过滤。
- 中文无词面重合时BM25为零；E5虽多语，但这一短视图并没有提供足够可靠的中文入口。摘要信息少、参数说明未参与索引以及跨owner竞争是后续候选原因，**尚未经消融证明**。
- 两个无关/缺必需事实观察题，BM25均无候选，E5均返回不适当候选。Dense本身不会可靠拒答；`candidates`不表示可以据此补造材料常数或操作。
- 抽查3个文档向量重编码与已存向量余弦均1.0，同一中文查询单条/混合长度批次余弦1.0，暂未发现编码顺序或padding错位；不是全面模型质量认证。

查询循环计时中位数约BM25 23ms、E5 35ms，包含候选回读hash检查，不含加载目录/校验模型文件/启动ONNX的冷启动成本，不能外推CLI端到端时延。E5总命中多3条仅是本开发集观察；不宣称普遍优于BM25，也不把差异归因于新增docstring（没有做增量消融）。未改题、Top-K或重复调参追求通过。

## 绑定与复核

| 对象 | SHA-256 / 固定版本 |
|---|---|
| 新methods.jsonl | `a315591c71cddc96ed17c5d3b94be4be66b94786b9f577d3bb48d42be16d879c` |
| members.jsonl | `7f2b811dff9dcc5faf841fb2c7382c409c58ea29af142ce4989d13b779f441d0` |
| 发现views.jsonl | `bbf9ac753a8b0c925885186c512183f768ae9bb7c0f3fff16b7141629c069b0c` |
| vectors.npy | `e4127e8e4b8a78b0536ec0c54373d031f04a924161ea5c4fa404481b7303d605` |
| 固定开发问题 | `c9c9f99630d380b62c78f9361997b7d2e4d80caa339ae64ecb37f34510164490` |
| E5 checkpoint | `intfloat/multilingual-e5-base` @ `d128750597153bb5987e10b1c3493a34e5a4502a` |
| 模型文件 | `84a4d426f7e87a6bf5bf195f0bae2c4a7d15f675b23ca96f42fab8326d7a77aa` |
| tokenizer.json | `62c24cdc13d4c9952d63718d6c9fa4c287974249e16b7ade6d5a85e7bbb75626` |

模型目录`var/models/multilingual-e5-base-d128750`的binding.json记录原始来源及文件hash；使用[官方模型卡](https://huggingface.co/intfloat/multilingual-e5-base)给出的前缀/池化方式，ONNX Runtime 1.29.0、tokenizers 0.23.1。没有重写默认final15、1A候选或历史报告。

完整本地证据：`var/audits/api-docstrings-v1-20260914/core.json`、`docstrings.json`、`discovery-first.json`。后者含逐题有序候选、来源、查询hash和实现文件hash。早先单独docstrings审计保留当时driver hash；此后driver只补离线发现检查，未覆盖早先证据。

复核（新输出文件名，避免覆盖首轮）：

```powershell
uv run --no-sync python benchmarks/api_catalog/audit_docstring_discovery.py `
  --catalog-dir var/api-catalog/abaqus-2024-docstrings-v1 `
  --base var/api-catalog/abaqus-2024-modeling-candidate-v3 `
  --wheel var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl `
  --index-dir var/api-catalog/discovery-docstrings-v1-e5 `
  --encoder-dir var/models/multilingual-e5-base-d128750 `
  --output var/audits/api-docstrings-v1-20260914/discovery-replay.json
```

离线全仓测试1088通过、27持证测试未运行；之后新增E5前缀/池化与CLI回归，三份新增测试文件合计8通过。改动范围Ruff/BasedPyright通过。没有生成模型或Abaqus运行；使用了本地E5编码，不称“零模型调用”。

## 下一步边界

先审查失败API的索引视图与原文，决定是否做一个有明确依据的视图修订；保留首轮与原题作为development，不把修订后结果包装成新held-out。当前不静默增加词表、扩大Top-K或接入Coder。2B/3/4仍待授权与各自验收。
