# ADR-0024：用 ask_user 与宿主待答记录交接用户输入

## 状态

已采纳。用户于2026-09-13确认PLAN-0007的整体迁移方案。

## 问题与决定

旧needs_input是模型的结构化输出，不是宿主已经实现的持久等待状态。它给模型增加field_id/kind/summary等交接字段，却不能保证模型识别缺失参数；原CLI在主动执行预算与运行锁内同步读取答案，未答即失败，且任意Abaqus调用后都不能再问。

新默认会话改用单参数`ask_user(question)`，通过Pydantic AI原生CallDeferred/DeferredToolResults及工具调用ID交接。最终ready_for_verification/failed仍保持typed，完成仍由宿主verifier决定。问题与其他工具或最终输出混发时，在after_model_request整轮拒绝，不能仅依赖sequential屏障放行答复前已经生成的动作。

宿主写入user_questions.json及按ID摘要命名的history快照，随后保存waiting_for_user并释放运行锁。Console取答复在事件循环与active wall之外；空输入/EOF/Ctrl+C保留待答，CLI返回2。恢复先验证原工作区、问题/快照关联与未决runtime，再提示原问题或读取answer-file。问题等待时间单独记录，所有主动预算继续累计。

答复在同一OS锁内先持久化，再交给模型；同ID同答复幂等、不同答复拒绝覆盖。答复落盘与revision/Harness记录之间中断时，由保存的答复恢复，不再次索求。取消问题不生成用户答案；未决Abaqus effect仍先进入needs_reconcile。坏记录不猜测修复。

允许后续发现新工程信息时提问；若当前revision已有Abaqus执行，答复触发一个新的task revision和验收基线，旧结果保留但不得验收新要求。修订以问题ID摘要关联，重入检查目标goal、revision、执行offset与workspace摘要，避免重复追加。首次建模前的答复不增加revision。

## 兼容边界

- 新默认CLI：launch v4记录ask_user_v1，resume v5记录待答关联；旧launch v1–v3、resume v3/v4继续按旧协议读取，不改写旧文件。
- 旧Gold与显式Catalog/Concept/ACI/document/probe、pure-edit及旧`--answers`适配继续legacy；新协议未支持的组合明确拒绝。协议在run内固定，不能用amendment改变工具列表。
- `CodingRunService.run_step`返回完成结果或WaitingForUser；`run`保留旧协议的settled-result契约。
- 新simple-modeling-pair spec v2显式启用ask_user；旧v1保留legacy。评测脚本只回答预声明且匹配的问题，不确定则待答/adapter_gap，不能把用户后续补答回填成旧trial通过。

## 取舍与边界

复用[Deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)及当前文件后端，不增加数据库、后台轮询或第二套Agent循环。代价是必须维护问答记录和版本兼容；这部分属于宿主交接职责，不能靠重命名输出或同步阻塞input省略。

不选择让CLI解析任意自然语言输出是否为问题，也不选择模型每次都填写物理参数清单。此机制保证“已提问后安全等待”，不保证模型一定识别缺参；不构成OS沙箱、外部Job exactly-once或完整durable workflow。自然模型行为与持证收益须独立验证。

## 落实与确认

后续接线扩展：2026-09-13用户批准处理Catalog接入缺口后，新CLI显式Catalog可组合ask_user_v1，覆盖full与两个automatic模式；原launch v1–v3及其他未迁移profile不换协议。新增验证覆盖提问前查询、回答后恢复与去重/累计预算、混发零lookup副作用和Catalog版本漂移拒绝。这是既有问答协议的组合扩展，不表示其他Concept/ACI/document/probe组合已获支持；任务收益仍待真实新profile测量。

实现位于coding/user_questions.py、agent.py、service.py、cli.py、launch.py、resume.py及amendments.py。tests/test_user_questions.py覆盖混发、持久待答、CLI取答、幂等答复、恢复、取消、晚答revision及旧证据拒绝；simple-modeling与CLI schema parity沿用现有测试。原始历史批次不回填。

## 替代关系

部分替代[ADR-0008](0008-pydantic-ai-harness-abaqus-coding-run.md)的新默认提问输出/澄清循环形式，以及[ADR-0020](0020-resume-coding-runs-with-harness-step-persistence.md)不含待答的状态集合。沿用[ADR-0019](0019-separate-product-execution-evidence-from-evaluation-oracles.md)的验收范围、[ADR-0021](0021-use-amendments-and-task-revisions-for-continuation.md)的修订与旧证据隔离；未变更其他权限与旧profile。
