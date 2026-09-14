# 显式离线交付模式：接口完成，尚无必要证据召回收益

已增加离线 `--delivery-mode passage|evidence_unit`：默认交付片段，显式模式恢复保存的完整源证据单元。复用已有 `scoring_document` / `expand_ranked_children`，没有再造切块或相邻拼接算法，也没有修改产品默认工具。

## 行为边界

- 保留片段排名，按可信父单元ID去重，不按整页去重；同页不同单元可以同时返回。
- 在两种模式中固定前四个去重单元槽位，核对子片段ID、精确整数偏移、正文、页、版本、标题/章节路径、anchor、XPath和source URL；原文件hash须由宿主验证。没有parent_id或映射不符时明确记录 `invalid_mapping`，不猜测旧索引映射。
- 单元上限384个不含特殊token的MiniLM WordPiece tokens；四条正文共享12,000字符上限。超限返回 `unit_budget_exceeded` / `total_budget_exceeded`，不截断、拼接、补位或退回片段冒充完整单元。
- 完整模式恢复的是已有原始body，可能包含结构标记，不把它自动改写成摘要。源单元完整不等于回答完整或语义相关。
- 交付函数原有的首尾CR/LF规范化仍保留；按实际交付正文计算指标和成本，内部正文顺序不改。失败槽位有独立状态，即使没有正文也不被报告成正常结果。

## 如何对照

直接回放 `var/rag-headerless-pairs-v1/replay` 中标为 `previously_observed` 的五条真实模型查询。每一路都使用完全相同的历史片段排名和前四个父单元，20个选中槽位的rank/child_id/unit_id逐项相等。

该历史候选profile是Dense20+BM2520加固定cross-encoder，不是默认 `var/rag` 的exact-prefix Dense，也不是上一轮单条警告的Dense64实验。候选单元及子片段库、历史排名hash均核对；71个涉及的源文件实际hash通过验证。本轮不重新检索、生成embedding、重排、调用LLM或运行Abaqus；它只比较交付模式，不能拿与默认索引的差异归因于展开。

## 结果

| 项目 | passage | evidence_unit |
|---|---:|---:|
| 查询 / 固定槽位 | 5 / 20 | 5 / 20 |
| 实际交付槽位 | 20 | 17 |
| 完整源单元数 | 10 | 17 |
| 正文tokens合计 | 1,946 | 2,575 |
| 每查询正文tokens均值 | 389.2 | 515.0 |
| 三项已有必要段落检查 | 1/3 | 1/3 |
| 明确的完整单元超限 | 0 | 3 |

完整单元数增加是交付范围指标，不是回答正确率。五条中只有三项预先声明的必要段落检查，不能声称任务通过率为1/5或其他比例。

- **第一条警告查询**：正确单元在候选池里，但没有进入前四个单元，两种交付模式都无法恢复它。
- **第二条警告查询**：正确单元已进入前四。passage已经完整包含所标注的“增量调整机制及例外”段落；evidence_unit另补齐性能影响段落，完整单元成立，但这项既有必要段落检查没有新增收益。
- **Riks边界条件查询**：正确单元不在候选池里，两路均失败。
- API与Riks主问题没有在此回放中补造新的gold标签。人工检查中，API候选仍偏向参数研究结果和节点集定义，不是所问的ODB访问成员；恢复更长原文不能据此声称API答案正确。

三次拒绝的完整body分别为444、537、438tokens；它们占用原选中槽位，没有低排名替补。平均正文tokens增加约32.3%，同时还少交付了三个槽位。序列化成功证据列表的token量有所下降，是失败槽位没有正文及metadata重复次数减少造成的，不能解释成上下文效率提升；该计数也不包含完整状态信封，不是模型计费token。

Riks边界条件查询的正文tokens从258增到662，却仍未取得所需边界条件段落。这说明完整交付也可能放大未回答当前问题的内容；“不新增父单元”不等于“不新增无关上下文”。

## 验证和状态

相关六个测试文件共 **63 passed**；新增模块BasedPyright为0 errors/0 warnings，Ruff检查通过。测试覆盖默认模式、重复父单元、同页不同单元、伪造来源/章节、非整数偏移、未知映射、原文件未验证、不支持结构、单元/累计预算失败，以及实际交付首尾换行的集成回归。

第一次接线检查因既有交付函数会去掉片段首尾换行而中止，没有修改正文或重跑模型。随后修正“实际交付”的核验边界、补回归测试，再收紧偏移类型检查；旧的部分输出和中间完整回放均保留。正式报告使用 `var/audits/evidence-delivery-modes-v1-verified/` 下的最终实现结果。

当前决定：**显式离线接口可以使用，但不切换产品默认，不宣称Agent收益。** 它解决可追溯、有界、不会悄悄截断的交付问题，不解决候选召回与排序问题。

## 使用和证据

在仓库环境运行，`--output`必须指定尚不存在的新目录：

```powershell
uv run python -m abaqus_sim_agent.evaluation.evidence_delivery_replay `
  --candidate-directory var/rag-headerless-pairs-v1 `
  --output var/audits/my-new-delivery-replay `
  --delivery-mode evidence_unit
```

省略 `--delivery-mode` 时为 `passage`。这个入口回放候选目录里既有的真实查询排名，不接受任意用户问题，也不是产品搜索工具。

- 实现：`src/abaqus_sim_agent/evaluation/evidence_delivery.py`、`evidence_delivery_replay.py`。
- 测试：`tests/test_evidence_delivery.py`、`test_evidence_delivery_replay.py`。
- 汇总机器报告：`benchmarks/rag/abaqus_2026_evidence_delivery_modes_20260908.json`。
- 两路manifest、逐槽位状态、实际交付正文与成本：`var/audits/evidence-delivery-modes-v1-verified/passage/` 和 `evidence_unit/`。
