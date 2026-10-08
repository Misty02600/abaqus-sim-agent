# ADR-0052：按名称选择工具，固定保留Catalog报错提示

## 状态

已采纳（2026-10-08）。用户确认新会话的API工具先沿用2026-10-06的v31批次`061ee67b72`：精确查询开启、目录发现暂不开启；
保留自动报错提示，不增加提示策略配置。工具开关参考Pi的按名称选择方式。

部分替代[ADR-0040](0040-require-api-catalog-for-product-runs.md)中的普通产品模式枚举和默认不开放查询工具。
Catalog必选、数据来源与摘要绑定、执行权限及冻结评测保留的决定继续有效。
沿用[ADR-0045](0045-use-explicit-run-options-without-preference-files.md)的本次输入与会话记录边界。

## 背景与取舍

`catalog_mode`把工具可见性和报错提示内容捆绑成多种组合。普通产品、CLI/Web装配、恢复和runtime需要解释同一
枚举，新增工具时还要继续扩展组合。仅增加两个布尔开关仍不能统一管理其他工具，并会让自动提示继续依附于工具开关。

采用一份实际工具集合，减少组合语义；代价是需要明确工具依赖、协议工具与普通功能工具的边界，并把集合保存为
会话合同。没有采用Pi的全局/项目偏好文件、插件安装机制或运行中动态切换；这些不是本次产品需求。

## 决定

- CLI/Web共用`tools`白名单和`exclude_tools`排除列表。本次显式输入覆盖启动输入；序列整项替换，排除优先。
  未提供白名单时按已绑定能力准备默认工具，`discover_abaqus_api`默认关闭；`lookup_abaqus_api`默认开启。
- 工具名称必须已知、唯一且满足当前资源与运行协议。目录发现依赖精确查询。工具筛选使用Pydantic AI原生
  `prepare_tools`，同时限制模型可见schema和实际可执行映射；相关API与Shell提示按选中的工具生成。
- `ask_user`、`load_capability`和`read_tool_result`属于问答、能力加载和结果交付协议，不作为普通功能开关。
  输出决定也继续由原typed-output协议管理。`--tools ""`关闭普通功能工具，不关闭这些协议能力。
- 工具可见性不授予Abaqus/Solver执行权限，不改变runtime allowance、输入校验、状态锁或verifier职责。
- 报错后的Catalog提示独立于主动查询工具是否开启，固定保留最新评测使用的行为：在能确定API上下文的失败后
  附加相关卡片、匹配的已验证运行说明或受限的替代名称提示，继续去重。不新增产品提示模式。
- 新普通会话使用launch v11，保存解析后的完整`tools`集合和Catalog身份；不保存`catalog_mode`或未解析的开关。
  恢复直接使用保存集合，不套用新默认，也不接受本次CLI工具覆盖。旧产品v10显式拒绝，不猜测迁移。
- 显式评测内部的旧模式和v9任务基线记录继续解释其原合同；不能交给普通产品恢复。已有批次的worker、源码快照、
  seal、输入及成绩不修改。新的源码条件只能用于新批次。

## 参考与实现

参考本地Pi 0.85.1快照`ceea48f5d5d12fd7915dfefba2835ccd55f23bb9`的
[按名称启用工具](https://github.com/earendil-works/pi/blob/ceea48f5d5d12fd7915dfefba2835ccd55f23bb9/packages/coding-agent/src/core/agent-session.ts)
及CLI白名单/排除语义；未复制其TypeScript实现。本项目复用已安装Pydantic AI 2.51.0的工具准备接口。

当前字段与恢复规则见[配置合同](../architecture/configuration-reference.md)，知识边界见
[知识接入](../architecture/knowledge-access.md)，实现位于[tool_selection.py](../../src/abaqus_sim_agent/coding/tool_selection.py)。
离线验证应覆盖默认工具、显式选择、错误名称、禁用后误调用无副作用、工具关闭后自动提示、问答恢复和CLI/Web一致性。
这项改动不构成新真实模型或Abaqus成绩。
