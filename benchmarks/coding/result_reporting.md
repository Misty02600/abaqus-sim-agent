# 基础线性评测结果与自定义规则

新批次协议为`public-mechanics-e2e-v25-native-result-grading`。Pydantic Evals 2.47.0承载单题结果和整批汇总；
现有CodingRunService、外部执行、物理verifier和恢复策略继续拥有原始运行事实。

## 结果文件

| 文件 | 用途 |
|---|---|
| `report.json` / `report.md` | 批次运行状态、计分范围、任务结论、现有usage和元数据导航 |
| `pydantic-evals-report.json` | 原生`EvaluationReport`，含assertions、scores、labels、metrics、理由和analyses |
| `pydantic-evals-report.txt` | 原生报告的可读视图；关闭默认断言平均值和投影耗时，展示自定义汇总 |

报告重建只读已存证据。`Dataset.evaluate`的task仅投影保存的Observation，`repeat=1`且不启用task/evaluator重试，
不会重新调用Agent、独立ODB reader或Solver。原生`task_duration/total_duration`是报告投影/评分耗时，
实际Agent活动时间使用`metrics.persisted_active_wall_seconds`，不能用毫秒级投影时间冒充仿真耗时。
框架`report.failures`在这里表示投影任务异常；真实Provider中断来自宿主`run_status/failure_kind`，不能混淆。

## 单题规则

[TaskPassed](../../src/abaqus_sim_agent/evaluation/mechanics_grading.py)实现`Evaluator`：

- `execution_ok`来自已有verifier的执行/交付阶段结果；缺少验收记录时未知。
- `physics_ok`是已有自动oracle接受结果与冻结数值目标的检查，不宣称覆盖全部建模语义。
- `requirements_ok`来自下述绑定证据的需求审查；自动结果通过或advisories为空不能替代它。
- 只有原run为completed、三个检查均通过、已有自动验收通过且仍在正式合同内，才产生`task_pass=True`。
- 明确的任务失败产生`task_pass=False`。未运行、中断、缺失判定及待审不伪填为物理失败；没有布尔判定时用label保留状态。
- 有amendment/task revision或超过冻结显式resume次数的结果标为posthoc，不用它回填正式成绩；正常的预算内resume仍可保持正式资格。
- 绑定改变、记录损坏、过期审查、scope/policy不符标为invalid_evidence。单题评分器异常由原生evaluator_failures记录。

宿主运行分类原先把completed命名为formal_pass，新报告改为`completed`；`task_verdict`另行表达真正的评测结论。
原始outcome、验收事件、工作区、raw日志和旧报告不会因此被改写。

## 需求审查记录

可在批次工作区之外的`<ROOT>/reviews/<case_id>.json`提供审查记录：

```json
{
  "case_id": "cantilever-uniform-load",
  "reviewer": "实际审查者或审查流程的标识",
  "reviewed_at": "2026-09-27T00:00:00Z",
  "passed": true,
  "reason": "结合题设、实际模型与输入、建模/提取脚本及运行证据写明判断依据",
  "outcome_sha256": "复制本批原生报告output.evidence_sha256中的对应64位摘要",
  "verification_event_sha256": "复制同一output.evidence_sha256中的对应64位摘要"
}
```

摘要占位文字必须替换为实际值。`RequirementsReview`校验身份、时间、非空理由、bool结论和摘要格式，
并检查记录是否仍绑定当前outcome及同revision/amendment下最后一次verification事件。
verification摘要针对事件原始行字节，不含换行符；不要重新序列化事件后猜测摘要。
报告保留有效审查的身份、时间、理由与文件hash。系统不生成通过意见，不自动签署，也不把AI判断声称为真人验收。
审查者需检查对应版本的实际证据；hash只能绑定版本，不能证明审查意见本身正确。

## 整批规则

`SuiteSummary`实现`ReportEvaluator`，用`ScalarResult`和`TableResult`返回统计。

- 计划分母为冻结`manifest.case_ids`与`manifest.scoring_case_ids`的交集，一题一票；VM184三工况合计一道题。
- 通过率为已确认通过数/计划题数，未运行、中断、待审、证据异常、评分异常和posthoc记录不从分母消失。
- 同时显示各状态数量；例如0通过但15待审不等于15个已证实的任务错误。
- 历史/未入选题可以显示诊断结果，但不计入主统计；没有计分题时不生成通过率，避免除零或虚构0%/100%。
- 自定义子集仍用`full_set_selected=false`标注，不能冒充完整15题结果。
- 资源表只合计已记录数值，并报告有值的题数；未知费用不填0，活动时间之和不是并发批次总耗时。
- 不把`report.averages().assertions`作为任务通过率，也不按断言数量或标签计算权重。

使用该批次原worker的`report --root <ROOT>`命令可重建视图。新worker拒绝覆盖v24或更早报告，
新判定语义需新批次，不能对旧成绩自动升级。离线接口测试通过不代表15题真实Agent/物理验收通过。
