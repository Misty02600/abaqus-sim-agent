# Agent 前端生态与复用完整性审查

2026-09-13。起因：用户担心前期调研覆盖不足，通用 Agent 前端能力可能已经成熟，不应重复设计。

结论：担心成立。此前的官方 UI 实测证明了一个接入方式可行，没有完成主流路线的比较，尤其遗漏 CopilotKit 的 Pydantic AI 集成、assistant-ui 的 AG-UI runtime 和成品应用的插件入口。FastAPI + assistant-ui 仍是合理的已采纳基线，但“应自行设计所有消息/会话同步”和“定制SSE一定更省”并未得到证明。

本次为公开一手文档和本机已安装源码审查，没有安装或运行新的候选，没有改动产品代码、锁文件或 [ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md) 的已采纳决定。当前已实测的前端仍只有[官方 ai-chat-ui 实验](official-ui-compatibility-20260913.md)。不能把文档支持写成本项目验证通过，也不能声称已穷尽所有库。

后续[二次评估](agent-ui-comparison-reassessment-20260913.md)进一步核对了实际发布包：assistant-ui AG-UI已有input_required/历史接入，CopilotKit除前端工具教程外还有标准useInterrupt。下面的生态地图保留为第一轮覆盖；最终比较与实验顺序以二次评估为准，不把其较少的组件选项当成生态缺乏能力。

## 如何判断“成熟”

不以项目名称、官方身份、star 数或营销中的 production-ready 单独判断。分别检查：

1. 是否有可复用的聊天、工具、附件和人工交互组件；是否有自定义入口而非只能改应用源码。
2. 是否有文档化的消息/事件协议、客户端、历史恢复、线程/任务标识与例子。
3. 会话、状态、工具执行由谁拥有，能否使用已有宿主而不接管其 Agent loop。
4. 本地部署需要哪些服务，历史持久化是否需要额外认证、数据库或可选平台服务。
5. 页面断连、流重接、答案提交和后台执行之间的边界是否明确。
6. 实际发布版本是否支持所需接口，升级、迁移和许可证成本是否可控。

通用组件和交互模式已有充分的可复用基础。协议与库仍在演进，具体版本兼容必须验证。域内副作用与物理验收由本项目拥有，是应用合同的适配工作，不是 Agent 前端生态不成熟的证明。

## 覆盖范围与候选地图

以下为有代表性的路线覆盖，不是排名；“项目判断”均为基于文档/源码的工程推断。

| 类别与方案 | 官方已有能力及来源 | 项目判断与尚需核查的边界 |
|---|---|---|
| Agent UI 组件：assistant-ui | [外部状态runtime](https://www.assistant-ui.com/docs/runtimes/custom/external-store)、[AG-UI runtime](https://www.assistant-ui.com/docs/runtimes/ag-ui/overview)、[工具展示](https://www.assistant-ui.com/docs/tools/tool-ui)。 | 已采纳基线有依据，但只看ExternalStore遗漏了标准协议路线。应比较“已有runtime + 宿主投影”与“自己维护同步代码”的真实成本。 |
| Agent 应用接入：CopilotKit | 有明确的[Pydantic AI入口](https://docs.copilotkit.ai/pydantic-ai)、[工具渲染](https://docs.copilotkit.ai/pydantic-ai/generative-ui/tool-rendering)、[人工交互](https://docs.copilotkit.ai/pydantic-ai/human-in-the-loop)和共享状态。 | 前期的重要遗漏。它的HITL示例可接前端工具，不表示本项目要把工具权限移到浏览器；可只复用展示与输入。需要核对宿主调用链和当前版本本地部署。 |
| 聊天组件与流客户端：AI Elements + AI SDK UI | [Tool](https://elements.ai-sdk.dev/components/tool)、[Confirmation](https://elements.ai-sdk.dev/components/confirmation)已有组件，AI SDK提供聊天状态与流机制。 | 不能把工具状态卡片、批准组件或消息流解析写成必须从零实现。Confirmation偏批准/拒绝，不自动代表带question ID的文本答复。宿主结果仍需显式映射。 |
| 通用AI组件/SDK：Ant Design X | [X SDK](https://x.ant.design/x-sdks/introduce-cn/)及[Chat Provider](https://x.ant.design/x-sdks/chat-provider-cn/)提供会话数据、请求和自定义provider能力。 | 是实际候选，尤其适合表单/工作台布局；未核对本项目待答与执行恢复接线，不能据文档认定比assistant-ui更省。 |
| Python 聊天应用：Chainlit | [Step](https://docs.chainlit.io/api-reference/step-class)、[AskUserMessage](https://docs.chainlit.io/api-reference/ask/ask-for-input)、[自定义元素](https://docs.chainlit.io/api-reference/elements/custom)。 | 通用步骤、问答、图片可以少写前端。其[on_chat_resume](https://docs.chainlit.io/api-reference/lifecycle-hooks/on-chat-resume)要求持久化与认证；需明确如何与现有文件状态协作，不应仅因多一套会话就先排除。 |
| Python UI：Gradio | [ChatInterface](https://gradio.app/docs/gradio/chatinterface)提供聊天交互、附加输入/输出、浏览器历史。 | 能迅速验证用户流程；save_history的浏览器缓存不能直接替代宿主任务列表。复杂状态卡片需要组合组件或扩展。 |
| Python 工作台：NiceGUI | [官方介绍](https://nicegui.io/)及[chat_message](https://nicegui.io/documentation/chat_message)提供Python编写页面和聊天控件的路线。 | 适合任务表格、参数表单与结果区；Agent专用消息/问答仍需组织。部分文档页面本次web工具不能完整读取，核查深度低于主要候选。 |
| 成品应用：Open WebUI | [Pipe/Action等插件](https://docs.openwebui.com/features/extensibility/plugin/functions/)可连接自有逻辑；[事件系统](https://docs.openwebui.com/features/extensibility/plugin/development/events/)已有状态、附件、输入与确认入口。 | 完整应用是有效路线，不能只把它看成模型聊天外壳。需要接整个CodingRun而非把三个Abaqus工具交给另一套loop；插件回调的连接/持久化约束必须验证。 |
| 成品应用：LibreChat | [产品文档](https://www.librechat.ai/docs)覆盖Agent/工具/附件能力，[Custom Endpoints](https://www.librechat.ai/docs/quick_start/custom_endpoints)主要接兼容的模型服务。 | “能连模型接口”不等于已经能保留自有CodingRun宿主；需要验证外部Agent入口或应用插件的具体接法，不能只凭OpenAI-compatible就判为零适配。 |
| 调试应用/可fork源码：Pydantic官方UI | [官方定位](https://pydantic.dev/docs/ai/guides/web/)是本地开发调试；有构建产物与自定义HTML入口。 | 已在本项目做过确定性接入实验，属于证据最充分的一条；但不是所有Agent前端方案的代表，也没有证明它的定制成本最低。 |

CopilotKit的[当前quickstart](https://docs.copilotkit.ai/pydantic-ai/quickstart)区分普通Pydantic AI搭建和可选Intelligence；后者涉及持久线程/检查器。不能因为文档引导注册就断言全部功能必须上云，也不能把平台能力默认算成本地开源部署自带。

Open WebUI事件文档区分持久化事件和必须有活跃浏览器的input/confirmation回调。这里的事实足以说明“有现成输入对话框”；尚不足以证明关页、进程重启后能够直接接回本项目问题ledger。文档也指出部分事件写入与最终消息保存有不同职责，需要用实际接口方式验证，不能只看一个演示截图。

## 协议层也是现成能力

| 层 | 可以复用 | 仍应由项目定义 |
|---|---|---|
| SSE/HTTP传输 | 标准编码、连接读取和客户端实现，不应自行写完整解析器。 | 连接断开是否影响工作段、访问权限及错误边界。 |
| AG-UI | [生命周期、消息、工具及状态事件](https://docs.ag-ui.com/concepts/events)，以及[人工中断协议](https://docs.ag-ui.com/concepts/interrupts)；Pydantic AI与多个前端已有适配。 | AG-UI run/interrupt如何对应CodingRun工作段、question、revision与宿主验收。 |
| Vercel AI消息流 | Pydantic AI已带[协议适配](https://pydantic.dev/docs/ai/integrations/ui/vercel-ai/)，通用UI客户端可以处理相应消息part。 | 宿主待答和结果投影、服务器历史归属、客户端动作的授权。 |
| A2UI | [与AG-UI的关系](https://docs.ag-ui.com/introduction)：描述生成式UI的结构。 | 当前固定任务面板不要求模型生成任意页面，因此不是本轮必需依赖。 |

另有TanStack AI等流与客户端方案；本次只查看了其[可恢复流文档](https://tanstack.com/ai/latest/docs/resumable-streams/advanced)，没有将其当作已验证候选或迁移模型运行时的依据。

“流恢复”已有实现，但不是无成本地恢复所有后台副作用。例如AI SDK[流恢复说明](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-resume-streams)及[abort兼容性说明](https://ai-sdk.dev/docs/troubleshooting/abort-breaks-resumable-streams)说明需要区分重接与取消；相关页面/模板存在版本差异，本次不把历史模板的依赖和API套用到当前包。这些能力应先评估复用，不能因本项目有外部进程就整体否定。

## 当前源码揭示的真实适配边界

核查本机Pydantic AI 2.33.0源码和本项目service，未安装AG-UI额外依赖或执行其端到端路径。

1. `coding/service.py` 当前自己调用 `agent.iter`，保存history/usage，并在DeferredToolRequests后落盘问题或在ready后调用独立verifier。一个直接接Agent的Web示例不会自动执行这些后续宿主步骤；接标准协议时需要保留该顺序。
2. `pydantic_ai/ui/ag_ui/_adapter.py::deferred_tool_results` 的已检查路径将AG-UI resume映射为Pydantic审批结果；`_event_stream.py::_build_outcome` 的中断构造检查 `DeferredToolRequests.approvals`。
3. 本项目新协议明确要求单个 `ask_user` 位于 `DeferredToolRequests.calls`，不接审批列表。因而不能把“库支持HITL”直接记为该问题协议已接通；可以评估正式interrupt/state/custom扩展的宿主投影，不需要重新发明一套聊天协议。
4. Agent流结束不等于CodingRun验证通过。标准协议仍可用于表示工作段结束，把waiting/verification scope/outcome放在明确的宿主状态中；不能让默认success外观替代物理/执行验收。以上为映射问题，不是AG-UI协议不能表达本项目。

本机源码SHA-256：

- `pydantic_ai/ui/ag_ui/_adapter.py`：`5495d9027524acfeb7bbb5bab3e300acc924e4c2fe024b04d2ad4ca67ffcd224`
- `pydantic_ai/ui/ag_ui/_event_stream.py`：`7e27cf6bda6654eed0d1a71724ecb187c92bfa0092e48533a1599e33864f8867`
- `coding/service.py`：`adad6ba3c3158fddd46e01cc347bb3d1bc84a48f267cc7fe88028143c5086c91`

这些观察不构成CopilotKit/AG-UI失败结论，具体客户端、协商版本和恢复行为尚需锁版验证。

## 对已有方案的审查结论

### 应当保留

- 单Coder、现有问答ledger、独立verifier、预算与唯一Abaqus运行边界。
- 宿主所有的run/question/revision标识与只读结果；它们可投影到任何成熟前端。
- API暴露真实用户操作，不复制宿主规则；窗口或连接重开不能自动重跑副作用。

### 前期论证不足

- 未比较CopilotKit等直接相关方案，就认定自定义接线是首选。
- 只讨论assistant-ui ExternalStoreRuntime，没有核查其AG-UI路径。
- 将“后端要适配”延伸成“消息状态同步应该由我们全部编写”。
- 先固定轻量SSE和取消token流，没有把它明确评估为体验/复杂度取舍；成熟客户端本来就能处理token流。
- 9–15个工程日是粗估，尚未用实际可复用清单和接线结果校准；不能当作库间成本比较或可靠排期。

同时应准确评价原方案：ADR-0025的SSE只有sync/run.changed两种失效通知，消息由GET快照读取，并不是要从零实现整个聊天流协议。AG-UI等标准可能减少映射，也可能在这个受限需求中增加额外生命周期和依赖；不能为纠正调研遗漏就先断言必须替换。首版不逐token输出是已确认的范围取舍，补查证明有现成技术路线，不自动改变该范围。

### 下一步应当验证什么

在PLAN-0008先交付“复用/适配/确需新增”责任清单，再验证已选assistant-ui的最小接线。标准AG-UI路径和已采纳的ExternalStore + REST/SSE路径，用同一组宿主场景检查：新run、原问题答复、刷新/独立浏览器、模型完成但宿主失败、页面断连、禁止操作、不增加模型工具和执行次数。

比较关注实际改动位置、依赖/常驻服务、状态写入者和故障行为，不以接口数量或样例行数直接当成本。CopilotKit作为遗漏后的文档对照，只有其特定能力能减少已识别缺口时才建议追加有界实测；不同时搭建十个系统。

这不自动把已采纳架构换成AG-UI或CopilotKit。若标准路线明显减少自定义同步并满足边界，应先用具体证据提出ADR修订/替代；在结论形成前，不推进依赖该取舍的大范围同步实现。通用工具卡片、消息组件、批准/答复控件、历史列表与传输客户端优先复用；本项目新增应集中在CodingRun合同投影和领域产物展示。
