# Runtime Feedback Repair Paired Pilot v1

## 目的

比较同一 Coder 在静态分析 `off` 与 `on` 时，能否更快修复已有脚本，同时不因 abqpy/BasedPyright 的
动态类型噪声降低成功率。Python 3.10 执行前语法检查在两臂中始终开启，不是 treatment。

## 冻结 Case

Case 由 `runtime_feedback_repair_cases_v1.jsonl` 定义，包含：可由静态分析发现的普通名称错误、Abaqus
模块/repository 错误、静态分析未覆盖的关闭文件错误，以及动态 `Assembly.Set` 容器错误。所有 fixture
必须在正式 trial 前以 Abaqus 2024 证明失败且不产生目标结果；修复答案不进入 Agent 工作区。

## 配对合同

- `control`：`static_preflight_mode="off"`；
- `treatment`：`static_preflight_mode="on"`，绑定固定 abqpy wheel、BasedPyright 版本与 cache；
- 两臂的源码、模型/Provider、Prompt、Skill、Catalog（均 off）、runtime、预算、fixture 和 verifier 相同；
- 每个 Case 两次独立重复；第一次按 off→on，第二次按 on→off；无 task retry、补跑替换或 post-hoc 修复；
- Provider/transport 中断、缺失 trace 或 evaluator 故障记为 `unpaired_unknown`，不算 repair fail；
- 历史 API repair、旧预检批次及其成绩不回填。

## 结果和成本

每个有效配对按 win/tie/loss 记录 task oracle；同时记录模型请求、工具调用、输入/输出 token、Abaqus
调用、失败 CAE 调用、静态分析调用/诊断/不可用次数和 runtime 秒数。模型是否采用诊断由脚本变化与工具
轨迹解释，不使用思维链作为评分。

试点只提供开发期比较。值得进入完整 v6/v8 端到端配对的信号是：有效 win 多于 loss、没有 treatment
独有的正常路径破坏或严重错误，并且 repair 请求数或失败 vendor 调用有下降。样本小，不宣称稳定成功率。
