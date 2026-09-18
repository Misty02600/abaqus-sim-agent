# ADR-0030：开发期不保留 CodingRun state 向后兼容

## 状态

已采纳。用户于 2026-09-17 确认项目尚在开发且没有需要继续恢复的旧产品 run，不实施旧版本兼容。

## 日期

2026-09-17

## 当时遇到了什么

当前 `LaunchManifest`、resume state 和 amendment schema 同时接受多代开发期格式，ADR-0024 还要求旧
launch/profile 继续按 legacy 问答协议恢复。PLAN-0012 将预算、runtime 权限、无限时长和 compaction 状态
重新分层后，继续维护这些旧解析分支会迫使新状态模型保留已经不再成立的字段语义。

项目尚未发布稳定 state 格式，也没有用户要求保留的旧产品 run。真正需要永久保存的是冻结 benchmark、
trial、seal、报告及其原始合同，而不是让当前产品 resume 入口继续解释每个开发期 JSON schema。

## 最后决定

- 不承诺 pre-release `request.json`、`launch.json`、`resume_state.json`、amendment ledger 或 StepPersistence
  snapshot 的跨 schema 恢复与 migration。
- PLAN-0012 的新状态模型实施后，产品入口只接受当前 schema；旧或缺字段 state 显式拒绝，不猜测补值、
  不原地升级，也不把旧字段按新语义解释。
- 冻结 Gold、benchmark profile、trial、seal 和报告继续保留其原始合同与结果，不能回填新默认值。确需重跑
  历史 Case 时建立新批次，并用当前 schema 显式表达该批次采用的评测合同。
- 同一当前-schema run 内的 resume、continuation、pending question、累计 usage、runtime effect、freshness、
  task revision 和知识绑定仍必须完整恢复；本决定不削弱 ADR-0020/0021/0024 的运行内恢复不变量。

## 为什么这样选

没有真实消费者的数据兼容层只会扩大测试矩阵，并使产品 schema 被开发期偶然结构锁定。显式拒绝旧 state
比静默迁移更安全，也符合损坏或不完整 ledger 不猜测修复的既有原则。历史评测证据通过不可回填与新批次
隔离保存，不依赖产品继续运行旧序列化格式。

## 没有采用的方案

- 继续读取所有旧 launch/resume 版本：当前没有旧产品 run 消费者，却会持续约束新预算和权限模型。
- 自动把旧 state 升级到新 schema：缺少唯一、可证明的语义映射，可能改变权限、预算或问答协议。
- 删除或改写历史 benchmark：会破坏评测可追溯性，与 state 兼容是不同问题。

## 带来的影响

- 有利：新运行合同可以直接表达 unlimited、runtime permission 和 compaction binding，不背负开发期兼容
  分支。
- 有利：旧 state 失败方式明确，不会静默套用新协议。
- 代价：升级代码后，旧本地开发 run 不能 resume；需要重新建立 run。
- 不变：已开始且使用当前 schema 的 run 仍按完整恢复合同处理；历史评测文件和结论保持原样。

## 替代关系

- 部分替代：[ADR-0024：用 ask_user 与宿主持久待答记录交接用户输入](0024-host-owned-user-question-waiting.md)
  中“旧 launch/resume/profile 必须继续由产品读取”的兼容边界。
- 沿用：[ADR-0020：使用 Harness StepPersistence 续跑中断的 CodingRun](0020-resume-coding-runs-with-harness-step-persistence.md)
  的当前-schema 恢复、累计状态和 `needs_reconcile` 不变量。

## 相关文档

- [PLAN-0012：统一 Agent 运行预算、超时、上下文压缩与收尾策略](../plans/done/0012-agent-runtime-budget-and-context-policy.md)
- [验证证据入口](../architecture/verification-evidence.md)
