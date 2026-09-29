# ADR-0043：模型偏好只初始化新会话，恢复沿用会话合同

## 状态

已采纳。用户于 2026-09-27 明确要求借鉴 Pi 的逐级选择与独立持久化边界，并要求同类配置按职责判断。

后续：用户/项目偏好文件及其来源覆盖规则已由 [ADR-0045](0045-use-explicit-run-options-without-preference-files.md)
部分替代；共享输入、会话与凭据边界继续有效。以下保留当时的背景、决定与取舍。

后续 [ADR-0046](0046-use-native-providers-and-available-model-selection.md) 在上述来源边界内独立明确原生模型接入、
可用列表与新会话选择规则，继续保留会话恢复及凭据分离原则。

提交范围说明（2026-09-28）：偏好文件读取的试作仍在未提交工作树中，本次只保存决定，不将该试作合入 main。

## 背景

CLI/Web 已保存会话的 request/launch，但没有用户级和项目级模型偏好。Web 又把完整模型执行配置放在
服务设置中，恢复要求会话网关与服务默认网关一致。这混合了“以后新会话默认用什么”和“已有会话实际用什么”，
也使调整默认网关影响旧会话的可用性。

## 决定

- 用户偏好、项目偏好、显式输入与会话合同分别拥有独立职责。新会话逐字段按显式输入 > 项目 > 用户 >
  内置默认值选择；Web 的创建请求优先于服务启动参数，项目由选定的 workspace 确定。
- 首次接入模型 provider/name、HTTP retries/timeout、reasoning policy。最终组合由现有模型配置验证；
  运行权限、固定知识绑定、任务输入、进度及凭据不自动进入同一偏好文件或覆盖链。
- CLI/Web 读取偏好，临时选择只进入会话快照，不回写偏好。持久修改偏好由用户显式编辑文件完成；不增加
  自动保存、兼容别名、旧文件迁移或旧成绩回填。
- 恢复使用原始合同及已有 typed amendment，不重新解析新会话偏好；不支持的会话内修改仍明确拒绝。
  不因模型缺失或凭据不可用自动切换模型。累计 usage 与证据基线继续遵守已有恢复合同。
- Web 凭据按网关单独注入，按会话保存的网关取用；服务启动默认值不再作为旧会话的网关身份约束。

## 取舍

保留默认模型，避免每次新建都必须选择；同时避免把会话内切换自动保存为全局偏好。与统一合并全部配置
相比，限定偏好范围需要按字段判断，但不会让项目文件顺带授予 Solver 权限或重绑已有会话的知识来源。
与恢复时重读默认值相比，保存合同需要额外记录，但能明确解释每个会话实际使用的条件。

新增可选文件读取和严格校验；无文件时保持内置默认，坏文件阻止新建并报告位置。已有会话恢复不依赖
这些文件的可读性。当前没有偏好写入 API，不引入配置热重载去修改活动会话。

## 来源与落实

语义借鉴 Pi 对 startup defaults 与 session model 的区分，没有复制其代码、提示词或资产。
核对基线为本地 Pi `ceea48f5d5d12fd7915dfefba2835ccd55f23bb9` 的
`packages/coding-agent/docs/settings.md`、`src/core/sdk.ts`、`src/core/agent-session.ts`；
上游[模型选择说明](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/models.md#select-a-model)
也明确区分切换与保存默认。Pi 的模型不可用回退没有采用。

实现由 `coding/preferences.py`、CLI 与 Web 创建入口承担；启动快照及 amendment 格式保持原合同。
验证入口为 `tests/test_model_preferences.py`、`tests/test_web_model_preferences.py`，覆盖优先级、严格读取、
不回写、恢复不重读偏好及按会话网关选择凭据。当前路径与字段说明见
[配置输入与恢复记录](../architecture/system-framework.md#配置输入运行合同与恢复记录)。

沿用 [ADR-0021](0021-use-amendments-and-task-revisions-for-continuation.md) 的会话修订边界和
[ADR-0030](0030-drop-pre-release-coding-run-state-compatibility.md) 的开发期不迁移原则。
