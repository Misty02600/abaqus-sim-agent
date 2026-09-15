# 查询意图测评扩展：独立会话回归、新题验收与重复检查

日期：2026-09-14。**扩展与首轮运行已完成，未据结果修改候选或默认策略。** 共43个独立CodingRun：15个旧集回归（完整12意图+3 guard）、20条新题、4个旧场景各追加2次。每题独立工作区、会话、状态和usage；并发2，0次Abaqus/Solver执行。

## 数据与验收边界

- 新题为16条API检索意图、4条缺参/冲突分流，见[intent_acceptance_cases_v1.json](intent_acceptance_cases_v1.json)。新题具体目标不与12个旧意图重复，但部分共享API家族、官方页面及既有建模知识；是新query验收，不是家族隔离held-out。
- 新题和目标在任何本批模型运行/排名前固定。整个候选（产品Python源码、锁、提示、私有Skill快照、索引manifest）绑定在本机seal.json。旧集完成后未根据结果调参，才运行新题；重复项在seal时已选定，不替换原失败。
- 为隔离查询准备，当前实验不允许生成原文没有的具体API方法名；这是实验表达协议，不等于产品永远不能使用模型已知的方法或主动Exact查询。违反该规则不自动代表API技术用法错误。
- 模型只读取一条中文case，没有gold、旧输出或检索结果。全部输出落盘后由主助手先做保真审查，随后宿主离线排名。主助手不是外部人类专家；有歧义的判断保留备注供用户抽查。
- 四条新clarify题都明确指出缺参/冲突及需确认，主要检查分流和不猜补，不能声称已经验证从模糊工程需求自主发现全部缺参。没有实际ask_user问答闭环或物理验收。

## 结果

全部43次文件任务都完成execution_evidence，未发生Provider中断；这不是43/43语义或仿真正确。本文“合格命中”要求路由正确、改写保真且目标在Top-5；不把错误改写恰好命中算成功。

### 完整旧集回归

15条均正确区分search/clarify，保真13/15。两条严格不通过分别是：截面赋值新增part-level Set表示；符号guard新增原题没有的findAt方法名。前者为保守的表示收窄判断，后者是明确的本次表达协议违例；不混称物理模型错误。

| 旧集范围 | 旧BM25合格命中 | Snowball合格命中 | E5合格命中 |
|---|---:|---:|---:|
| 原12个意图 | 8/12 | 10/12 | 8/12 |
| 加上2个search guard | 8/14 | 10/14 | 9/14 |

另1个缺E guard正确clarify，未检索或补值。原中文直接检索在14个search条目上，BM25两配置均0/14，E5为2/14；原文基线独立于模型改写质量。

### 20条新题首次验收

| 项目 | 结果 |
|---|---:|
| 输出有效、路由符合预期 | 20/20 |
| 主助手判定保真 | 19/20 |
| 应澄清条目正确询问缺失/冲突项 | 4/4 |
| 16个search条目的旧BM25原始/合格Hit@5 | 11/16、11/16 |
| 16个search条目的Snowball原始/合格Hit@5 | 11/16、11/16 |
| 16个search条目的E5原始/合格Hit@5 | 13/16、12/16 |

不合格改写：将未指明维度的几何区域写成cells，收窄为未给定的区域类型；E5虽然命中了目标，也不计合格收益。

新题的原中文直接检索三种配置均1/16。三条保真但各ranker均未命中的新意图是Part命名集合、均匀压力载荷和新建场输出请求，值得后续定位。新建材料对象由E5命中，两个BM25未命中。其余成功和退化全部保存在逐题文件中。

**旧小集Snowball较好，新题却与旧BM25同为11/16。** 因此不能把此前7/8解释成Snowball普遍更优，也不立即调整默认排序或增加混合ranker。

### 四个旧场景的三次独立观察

包含旧集首条与追加两条，不回填/挑选最佳结果。

| 场景 | 保真 | 旧BM25合格命中 | Snowball合格命中 | E5合格命中 |
|---|---:|---:|---:|---:|
| 二维草图 | 3/3 | 2/3 | 2/3 | 2/3 |
| 截面赋值 | 1/3 | 0/3 | 0/3 | 0/3 |
| 缺E分流 | 3/3且3/3 clarify | 不适用 | 不适用 | 不适用 |
| CPS4R及原禁止条件 | 3/3 | 0/3 | 1/3 | 3/3 |

截面赋值的Snowball/E5原始命中均2/3，但命中的两条都被严格Set表示判据扣除。**该判据需用户抽查，不能把0/3直接描述成模型无法使用截面API。** 草图三条语义均保真但检索仍随措辞波动；CPS4R在这个小样本中E5一致命中，不外推稳定通过率。

## 评分修正及证据保留

首个regression评分脚本错误地用“模型改写保真”门控原中文基线。随后修正为：原始search基线独立运行，不因模型失败或改写不保真扣分；只有operation分支需要改写资格。

- 原`regression-scores.json`保留，不能作为最终汇总。
- 修正后的各阶段均保存为`*-scores-v2.json`，携带`independent-source-baseline-v2`与脚本hash。
- 回放确认旧集所有原始排名不变；仅原中文E5的合格计数由错误的1修正为2。
- 修正在新题首次排名前完成；新增测试覆盖错误改写/模型失败不污染原文基线、clarify不被当作原始search，以及数据集结构/目标分离。

未重跑任何Agent或替换失败输出，不删除不利样本。这是评分边界修正，不是新候选成绩。

## 配置、成本与可复用性

Go/deepseek-v4-flash、enabled_high请求设置、Planning开、HTTP60秒/最多额外重试10；每题20次模型请求/900秒wall预算、运行与检索预算0，无自动resume。未增加单响应token上限，也不声明已证明provider实际高推理强度。

框架累计记录202次模型请求、输入1186555/output46041 tokens、196次工具调用、各run活动wall合计约834.6秒。活动wall之和不是整批实际历时，也不能当作并行提速证据；未另行记录HTTP传输数。

使用了当前包内6个Skill目录的12个文件快照，全部位于私有seal目录。该快照与更早只含较少Skill的批次可能不同，因此不把批次差异单因果归于“一题一会话”或任何单项策略。本批各阶段始终使用同一快照。

轻量套件由现有旧意图、[回归协议/guard](intent_regression_protocol_v1.json)、新题JSON及两个小脚本组成；没有新评测平台或自动语义裁判。运行后将此前存于本地历史目录的提示和guard整理到版本化JSON，历史文件未删除，并核对提示bytes和guard逐项相同；首次驱动原文另保存在本机run_driver_first.py并与seal hash一致。当前驱动还保证准备失败时先等待已启动子进程结束再报错；这些收尾修改没有触发新模型运行。

使用新的root（不得覆盖已运行目录）：

```powershell
uv run --no-sync python benchmarks/api_catalog/run_intent_suite.py prepare --root var/audits/<新批次>
uv run --no-sync python benchmarks/api_catalog/run_intent_suite.py run --root var/audits/<新批次> --phase regression
uv run --no-sync python benchmarks/api_catalog/score_intent_suite.py collect --root var/audits/<新批次> --phase regression
# 先审查输出，填写带outputs_sha256和逐case faithful/note的regression-review.json，再score。
uv run --no-sync python benchmarks/api_catalog/score_intent_suite.py score --root var/audits/<新批次> --phase regression
```

acceptance和repeat沿用相同流程；禁止把主助手审查写成外部专家签署。准备需要已有固定Catalog/E5索引、模型和当前Skill，运行需要真实Provider凭据。程序默认不替人生成faithful判定。

本批完整证据位于`var/audits/intent-suite-20260914`：seal、提示、Skill快照、逐case CLI输出/状态/history、三个outputs/review/scores-v2文件。候选源码/锁/原索引未修改，产品默认策略未变，0次Abaqus运行。新增3项回归测试及脚本Ruff检查通过；未因本次仅测评改动重新声称全仓或持证验收。

## 下一步判断

先让用户抽查Set表示等边界判据，并定位新题中几个稳定未命中的常见入口。当前数据已足以揭示问题，不建议马上继续扩题、增加检索组件或刷当前题目。后续调参将这些已消费题视为development，不能再次称未见过的新验收题。
