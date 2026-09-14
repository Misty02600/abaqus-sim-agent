# PLAN-0007：用 ask_user 完成提问、待答与继续

| 状态 | 完成时间 |
|---|---|
| 已完成：工程实现与离线验证；未进行真实模型/持证验证 | 2026-09-13 |

## 问题与最终结果

用户确认整体迁移后，新默认普通CLI和新simple-modeling spec v2使用单参数ask_user(question)，不再向模型暴露needs_input输出。最终ready_for_verification/failed仍是typed，完成由宿主verifier决定；没有增加物理参数识别器、强制问题清单或新Agent循环。

模型提问会形成原生DeferredToolRequests。宿主在展示前保存user_questions.json、按调用ID摘要命名的history及其hash，再写waiting_for_user并释放运行锁。Console输入在事件循环和active wall之外，空输入/EOF/Ctrl+C保留待答并返回CLI退出码2。resume先处理原问题，不请求模型；可用--answer-file提交question_id/answer。

已保存答复及取消使用interrupted作为待继续边界，按原工具ID回填DeferredToolResults。相同ID同答复幂等，不同答复不覆盖，撤销不是用户答案。问答记录损坏、错误ID/revision、未授权工作区漂移均拒绝；发现未决Abaqus effect时先标记needs_reconcile，不能靠回答或取消绕过。

## 关键实现

| 位置 | 职责 |
|---|---|
| coding/user_questions.py | 单参数提问、整轮混发拒绝、严格问题/答复记录、问题history绑定、答复/取消、恢复事务 |
| coding/service.py | run_step返回完成结果或WaitingForUser；保存主动执行段，恢复deferred结果，防止重复答复 |
| coding/cli.py | 新默认协议、事件循环/锁外Console交互、answer-file、cancel-question；保留旧run入口 |
| coding/launch.py、resume.py | launch v4/resume v5；新协议run内固定，旧v1–v3 launch与v3/v4 resume保持原语义 |
| coding/amendments.py | 在同一state锁下复用revision提交；未答先禁止修订，取消后可显式改goal |
| evaluation/task_baseline*.py、simple_modeling_batch.py | 新spec v2显式启用，旧v1不升级；固定答复不再要求模型field_id，不匹配则待答/adapter_gap |

ask_user与任何其他工具或final混发时，after_model_request整轮拒绝，连排在提问前的写入也不执行；不是单靠sequential=True。提问预算在工具接受前检查，所有主动预算累计，已接受问题恢复不重复计数。

当前revision已有Abaqus执行时，新的工程答复触发一个task revision，保存旧结果、划定新的snapshot和execution offset；旧产物会被判为stale，不能验收新要求。答复先保存，随后按稳定问题ID关联revision；恢复核对goal、revision、offset及workspace摘要，提交中断不重复追加。取消问题后显式的新goal可以使用其新baseline内的工作区，普通恢复仍拒绝无修订的变化。

## 兼容与边界

- 旧run、Gold及显式Catalog/Concept/ACI/document/probe、pure-edit、旧--answers路径保留legacy_decision。新默认只暴露一个提问入口；未测试的显式组合不静默启用ask_user。
- CodingRunService.run保留旧settled结果契约；新协议调用run_step，由调用方接收答复后按当前effective request恢复。
- 问题等待不计active wall，单列等待时间；waiting不是完成也不是物理通过。匹配不到脚本答复的评测保持待答/未知，不伪造用户输入或自动重跑。
- 仍是文件后端的本地会话能力，不是数据库事务、外部Job exactly-once、进程重新附着或完整durable workflow。
- 状态协议保证已提问后的安全交接，不能保证模型识别缺参；未运行新的真实模型或Abaqus样本。旧批次不回填。

## 验证结果

- 新增20项问答测试，覆盖同轮混发三种顺序、持久待答、无答复恢复、Console锁/事件循环边界、answer-file、空输入/EOF/Ctrl+C、重复与冲突答复、Provider中断后的答复重放、revision提交中断、取消后新任务、未决runtime优先、坏记录拒绝及版本兼容；另以全新Python子进程实际完成原问题的接答与恢复，避免只用同进程重建对象证明恢复。
- 新增1项双任务待答测试，确认一条没有匹配答复时另一条仍可完成；更新新CLI与新候选的实际HTTP工具schema parity，旧T1/Gold路径继续回归。
- 最终全仓离线：1060 passed，27持证测试未运行。JUnit位于本机var/audits/ask-user-implementation-20260913/full-tests.xml。
- 改动范围Ruff和BasedPyright通过。全源码显式检查另发现behavior_measurement.py/document_behavior_audit.py中的4项类型问题；两文件hash与原seal一致，本轮未修改，不声称全源码类型检查全绿。
- uv.lock保持原hash 6d8af39f4e6dfe7535f5610c0b85ed3ef023bc1ab2965ad320bc6ccc9713ca82；旧双任务trial-result分别保持f3ec7349edb47df027eb65d82caaa0411d08db8d97bddd378c2b839bae80ad5f、fcfd44f1c65a864a54d3ed12e78d269022758afaedfda50ae48371d24b2a56aa。
- 未提交Git，未修改引用仓库。

## 后续

实际模型主动提问率、接口使用稳定性和任务收益回到PLAN-0006独立验证；新协议需要新seal和预算批准。不得用工程测试证明自然模型不再猜参数。

## 相关文档

- [ADR-0024](../../adr/0024-host-owned-user-question-waiting.md)
- [当前测评路径](../todo/0006-domain-task-evaluation-baseline.md)
- [原生Deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)
