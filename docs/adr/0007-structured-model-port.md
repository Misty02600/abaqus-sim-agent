# ADR-0007：通过 StructuredModelPort 隔离模型提供方

## 状态

已替代

## 日期

2026-08-19

## 当时遇到了什么

项目已经用 ADR-0002 确定 LangGraph 是唯一编排与恢复运行时，并用 ADR-0006 确定端口适配器边界。
在开始实现专业 Agent 前，还需要决定怎样接入不同模型：直接让角色依赖 `ChatOpenAI` 会泄漏 provider
消息、异常和重试语义；把 PydanticAI Agent 放进 LangGraph 节点又会引入第二套工具循环、输出重试、
usage 和运行上下文。

模型切换还会影响成本、结果复现和错误语义。不能根据机器上碰巧存在的 API key 自动选择 provider，
也不能把一个声称 OpenAI-compatible 的 endpoint 直接当作已经支持 structured output、tool calling、
usage 和相同超时行为。

## 最后决定

专业 Agent 只依赖项目自有的窄 `StructuredModelPort`：

- 每个端口实例绑定一个非秘密 `ModelProfile`；一次请求只携带消息和 Pydantic 输出类型，不能选择任意
  provider、model 或 endpoint。
- Profile 记录 provider、API family、model、endpoint 标识、连接/设置 revision 和 structured-output
  策略。真实 URL 从受控 connection config 解析，client 和 API key 由 Composition Root 组装，再通过
  Runtime Context 注入；它们不进入 Graph State，API key 也不进入持久配置或 trace。
- 首个 adapter 使用 LangChain `BaseChatModel` 接口，首个具体实现是 `langchain-openai` 的
  `ChatOpenAI`。连接显式使用 Chat Completions、显式且不含凭据的 HTTP(S) `base_url`，并关闭 SDK 自动
  重试；具体模型名不是架构常量。
- Port 一次只做一个结构化模型请求，返回 typed output、Profile、可用的 usage、provider response ID 和
  服务端报告的 model；失败统一为带类别和 retryable 属性的异常。拒绝/content filter 不作为格式错误重试，
  输出截断保留为独立失败类型；缺失 usage 保持 `None`，不伪造成零。
- LangGraph 继续独占 checkpoint、interrupt、全局预算和业务恢复；输出修正与角色预算由专业 Agent
  子图显式实现。Adapter 不自动 fallback、不执行 Agent 工具，也不能触达 Abaqus 副作用。
- 首版所有角色共用一个由 Composition Root 明确选择的默认 Profile。只有固定合同测试和角色 benchmark
  证明有收益后，才增加按角色选择模型；OpenAI-compatible endpoint 必须逐个验证。
- CI 使用 FIFO scripted fake，不能发起真实模型调用。凭据、原始 provider exception 和原始 CoT 不进入
  可序列化业务状态。

## 为什么这样选

这个边界保留 LangChain 与 LangGraph 的直接衔接，同时把 provider 类型隔离在 adapter 外侧。专业 Agent
可以用同一合同接 fake 或真实模型，错误、usage 和模型身份也不会因 provider 改变而污染业务契约。
重试和 fallback 的所有者保持可见，节点重放时不会在第二套隐藏 Agent loop 中丢失预算语义。

## 没有采用的方案

- 在每个 LangGraph 节点内运行 PydanticAI Agent：会重复拥有工具循环、输出重试、依赖上下文和 usage。
- 让专业 Agent 直接依赖 `ChatOpenAI` 或其他 provider SDK：难以稳定替换、离线测试和归一化失败。
- 只为 provider 抽象引入 PydanticAI Direct Model：仍需为本项目端口实现消息转换、typed-output 校验与
  修正预算、统一错误适配，不能减少当前边界复杂度。
- 根据环境变量中存在的 key 自动选择 provider/endpoint，或遇错静默 fallback：会破坏成本控制和可复现性。
- 首版同时安装多家模型 SDK：当前没有合同测试或 benchmark 证明需要。

## 带来的影响

- 有利：Agent 合同不泄漏 LangChain/provider 类型；CI 可以完整离线运行；模型身份、usage 和失败语义可追踪。
- 有利：以后替换 provider 只需实现同一端口并通过合同测试，不改变父图和专业 Agent 输入输出。
- 代价：项目需要维护一层很薄的消息转换、结构化结果收窄和错误分类。
- 风险：Profile 只描述配置和声称能力，不等于 endpoint 已通过任务评测；必须保留合同测试与 benchmark
  的独立资格结论。

## 落实与确认

- 实施情况：已建立 `StructuredModelPort`、`ModelProfile`、scripted fake、LangChain adapter 和
  `ChatOpenAI` factory；真实专业 Agent 调用链、真实 endpoint 合同测试和模型 benchmark 尚未实现。
- 代码或测试：`src/abaqus_sim_agent/inference/`、`tests/test_inference_fake.py`、
  `tests/test_langchain_inference.py`。

## 替代关系

- 被替代：[ADR-0008：首轮采用 Pydantic AI Harness 单 Coder 的原生 Abaqus CodingRun](0008-pydantic-ai-harness-abaqus-coding-run.md)。
- `StructuredModelPort` 和 LangChain adapter 保留为已测试的研究资产；首轮 Coder 直接使用 Pydantic AI
  model/provider，不经过该端口。

## 相关文档

- [项目架构](../architecture/overview.md)
- [完整系统框架](../architecture/system-framework.md)
- [技术栈与工程讲解点](../architecture/technology-stack.md)
- [Agent 与首个完整实现范围](../architecture/agent-implementation-scope.md)
- [一次仿真 Run 的工作流](../architecture/flows/simulation-run.md)
- [ADR-0002：使用 LangGraph 构建分层多 Agent 系统](0002-langgraph-hierarchical-multi-agent.md)
- [ADR-0006：使用模块化单体和端口适配器组织控制平面](0006-modular-monolith-control-plane.md)
