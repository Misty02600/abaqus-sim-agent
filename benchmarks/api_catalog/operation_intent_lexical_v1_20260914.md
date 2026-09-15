# 操作意图与英文词法处理：实现和一次开发验证

2026-09-14。结论：**显式离线接口和词法配置已实现；选定8条旧开发意图有局部改善，但保真与复杂约束查询仍有失败，未启用产品默认策略或Coder发现工具。** 文档视图及E5模型固定为v2，未同时重构视图。

## 实现边界

- `DiscoveryIntent(original_request, operation)`由宿主把原请求和模型生成的操作表达分别装配。`CatalogDiscovery.search_intent`只对operation排名；结果原样返回original_request、其SHA-256及可识别符号，附上采用前核对对象归属、当前/目标状态和原约束的提示。不让模型重写原始请求，也不建设自动物理约束抽取/语义判定器。
- CLI支持`discover --intent-file <JSON>`，与原位置参数question互斥。旧question调用保持原行为。此接口不自动翻译，也未注册成Coder新工具。
- `--lexical-profile english-snowball-v1`显式用于BM25，默认仍legacy。对同一批已绑定视图重新生成内存词频，不修改原views/vectors/manifest；返回结果携带实际词法配置与停用词hash。Dense不经过该词干处理。
- 普通英文词做NFKC、casefold、停用词处理、Snowball词干化；完整符号另保留大小写敏感的字面项，同时生成用于意图发现的拆词。Exact定位完全不经过词干变换。符号识别是语法形态启发，不声称能识别全部Abaqus符号；原请求始终完整保留。
- 保留no/nor/not/cannot/without/only和n't否定形式，不把它们当普通停用词抹掉。词法处理不解释逻辑或证明适用性；stemming也不保证所有近义词等价，例如不同英语形容词/名词仍可能分到不同词干。
- Snowball实例按线程隔离，避免共享可变stemmer；词干结果有界缓存。不增加中文术语表、翻译Agent、reranker、数据库或新的持久状态协议。

## 上游依据与依赖

词干实现复用[snowballstemmer 3.0.1](https://pypi.org/project/snowballstemmer/3.0.1/)，明确固定版本，不声称它是最新版。实现按官方说明先规范化和转小写，并隔离非线程安全的stemmer实例。

停用词来自[Snowball英文列表](https://github.com/snowballstem/snowball-website/blob/a5c23fcf6cb8c34f450d5080d888e65bc4681c1b/algorithms/english/stop.txt)，共174词，未按本开发集补词；本地去掉源注释、合并空白，运行时保留上述否定形式。BSD-3-Clause版权、完整许可证、来源revision及修改说明已随包内data资源保存，wheel检查确认两份资源均包含。

pyproject.toml增加一个约103KB wheel的小依赖；uv.lock仅增加其依赖声明和固定包记录，未升级其他包。新锁SHA-256为`6e6018b6dea509b19c05858ed96eca2e214fb7ce0a14c70d46bca6129fc9d1ff`；停用词资源SHA为`8a144ece3b2d1f7890faf49b9fb62efddff75fb1613c14cfec1138c974a627ff`。旧源码/锁绑定实验如需重跑必须新seal，不能把“旧索引仍可读”当作旧实验环境未变。

## 真实Coder验证

沿用前一批完全相同的11条输入（8条旧意图+3条保真guard）。一个新的普通产品Coder批量文件任务，明确要求简短操作短语而非完整英文问句；原始约束由宿主持有。模型没有gold、旧英文输出、索引或排名。输出后先由主助手审查，再进行宿主离线检索，检索结果不回流模型。

配置：Go/deepseek-v4-flash、enabled_high请求设置、Planning开、HTTP60秒、额外重试10次；任务模型预算20、wall900秒、运行/检索预算0。一次运行，无自动恢复、无替换尝试、无单响应token上限。

实际usage为4次模型请求、4次工具调用、输入24625/output1797 tokens、约15.33秒、0检索/0Abaqus。ready只通过execution_evidence文件验收，不代表物理或语义通过。未单独观测HTTP传输次数或provider内部实际推理强度。

保真仍为10/11（主助手审查，非外部人类专家签署）：CPS4R条目不再把目标写成已经赋值；但草图条目新增原文没有的“on a datum plane”，按严格标准不通过。没有删除这几个词后重跑。缺E条目仍正确标clarify且未补值，这只是分类与问题文本，不是实际ask_user交接闭环。

## 分开比较查询与词法配置

主比较仅计8条旧开发意图；新操作列同时要求改写保真，草图本身也未命中，所以原始命中数与合格命中数相同。

| 查询来源 | 旧BM25 | Snowball BM25 | E5（未改） |
|---|---:|---:|---:|
| 原中文 | 0/8 | 0/8 | 2/8 |
| 前批完整英文问句 | 3/8 | 6/8 | 2/8 |
| 本批简短操作表达 | 5/8 | 7/8 | 6/8 |

同一表达在两种词法配置间比较，可观察新词法配置的局部效果；旧英文和新操作来自不同时间的两个Coder批次，不把差异严格单因果归为一条提示规则。新配置同时包含停用词、词干和符号通道，不分别归因某一项。没有调Top-K、改gold或补跑追分。

本批三个原关键操作（截面、单元类型、写INP）在Snowball BM25和E5中均命中。但不能回填原中文的2/3 gate：此次使用的是Coder生成operation及另一套调用路径。

剩余失败：

- 草图：引入datum plane且未命中，保真和检索两项都不合格。
- 装配实例：新Snowball BM25第5名，E5仍未命中；新词法相对旧BM25的第2名也有退化，不能称所有排名改善。
- 两条应search的复杂guard（Part.faces/坐标/禁止generateMesh，CPS4R/不求解/不改厚度）在各配置中仍未命中目标。操作短语生成仍把部分约束写进排名表达，不能声称稳定实现了语义解耦。
- `original_request`逐字回传、hash一致只证明数据保留，不证明模型遵守或候选满足约束；没有新增自动适用性验收。

因此只交付显式实验能力，保持默认legacy、原v2视图/E5、已有索引和原报告。没有在线自主检索/候选采用或真实建模收益，也没有新的held-out或稳定性重复证据。

## 使用与证据

由宿主准备intent JSON，例如：

```json
{
  "original_request": "给已有零件指定单元类型，不提交求解、不改变材料。",
  "operation": "Assign an element type to a geometric region of a part"
}
```

```powershell
uv run --no-sync python -m abaqus_sim_agent.api_catalog discover `
  --catalog-dir var/api-catalog/abaqus-2024-reference-context-v2 `
  --index-dir var/api-catalog/discovery-reference-context-v2-e5 `
  --intent-file <宿主准备的intent.json> `
  --strategy bm25 --lexical-profile english-snowball-v1
```

使用Dense时保留`--strategy dense --encoder-dir <固定模型目录>`；它仅编码operation，不把保留的原请求再拼回去。若是纯精确符号查询，仍交给公共Exact，不用词干结果充当API名。

本地完整证据：`var/audits/api-intent-20260914/manifest.json`、`state/`、排名前的`fidelity.json`和逐条`retrieval.json`；原始模型输出在`D:/abaqus-runs/api-intent-20260914/workspace/operations.json`，SHA为`838f5d4b3c185053248abe17241a6e10ba1999e23caaf73dd67759195092d953`。

全仓离线1098通过、27持证未运行；改动范围Ruff/BasedPyright通过；wheel构建并核验词表、许可证、模块及依赖声明。没有提交Git或改动其他任务文件。
