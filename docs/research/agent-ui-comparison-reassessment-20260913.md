# Agent UI 调研二次评估：同一目标下的比较

2026-09-13。针对用户“再次评估比较你的调研”，复核前期选型依据、比较公平性及实际发布包。结论是保留 FastAPI + assistant-ui 作为当前实施基线；没有证据证明应该立即换库，也没有证据证明该路线总成本最低。下一项需要用运行证据决定的是 assistant-ui 的两种接线方式，而不是再扩大候选名单。

后续用户授权加入官方UI实际比较，结果见[三路接线报告](ui-three-way-comparison-20260913.md)：三条在确定性夹具均通过。本文保留运行前的源码评估，不再作为“尚未实测”的最新状态说明。

## 本次新增证据

此前仅对官方 ai-chat-ui 2.1.0 做过本项目浏览器/真宿主假模型实验。其结果仍有效，但不能用于评价其他前端的运行稳定性。

本次读取npm官方registry发布元数据，下载两个发布归档并通过其dist integrity检查；只读取源码/类型声明，没有安装依赖、执行候选包、构建页面或调用模型/Abaqus。完整[版本与源码检查记录](evidence/agent-ui-recheck-20260913.json)可复核。

| 发布包 | 本次查询值 | 核查层级 |
|---|---|---|
| `@assistant-ui/react` | 0.15.19 | 发布元数据、React peer要求。 |
| `@assistant-ui/react-ag-ui` | 0.0.59 | 完整发布归档的实现、类型与测试源码。 |
| `@ag-ui/client` | 0.0.59 | 发布元数据、依赖边界。 |
| `@copilotkit/react-core` | 1.71.1 | 发布归档的v2公开类型声明。 |
| `@copilotkit/runtime` | 1.71.1 | 发布元数据；未证明所有部署都需要这个服务包。 |
| `@ai-sdk/react` / `ai` | 4.0.102 / 7.0.99 | 发布元数据；不是本项目建议升级目标。 |

这些是查询时的版本快照，不是兼容组合锁，也不能用版本号大小判定成熟度。下载归档有源码测试文件，仅证明上游写了相关测试；本次没有运行它们。部分类型声明标为experimental的历史/线程接口，实施时要按确切使用范围判断，不能把整个包都描述成实验性。

## 对之前结论的纠正

1. **把后端框架与前端方案当作互斥选择，比较维度不对。** FastAPI提供HTTP入口；assistant-ui、CopilotKit等可以使用该入口。是否复用标准协议是第三个选择。
2. **从“官方UI能改”跳到“应该fork”，证据不够。** 实验只证明可接入和具体缺口，未比较维护成本；官方UI用于调试的定位不等于其他Agent前端都只能自行改源码。
3. **遗漏CopilotKit并低估标准中断。** 其1.71.1公开声明除useHumanInTheLoop之外，还包含useInterrupt，接收标准AG-UI中断和resolve(payload)，可以把输入答复汇成resume请求。它不要求为了显示这个中断而新增模型可见的前端工具。具体宿主映射仍需验证。
4. **没有充分区分assistant-ui的两条接入路径。** react-ag-ui建立在外部状态runtime上，负责事件解析、消息重建和输入中断；它和直接ExternalStore并不是两套不能组合的前端。
5. **将“已有恢复入口”当成“宿主恢复已接好”同样错误。** 发布实现的resumeInFlightRun要求history adapter提供resume；缺少时明确跳过，避免重新从头启动Agent。历史读取、流重接、持久问题答复、Provider续跑和外部Job协调是不同契约。
6. **工作量区间缺乏足够校准。** 9–15工程日只能保留为低置信度粗估；新增依赖数量、归档体积、源码行数都不是页面性能或开发工时。

## 固定比较目标

只评估当前已确认范围：本机单用户、一项主动任务、缺参后持久待答、已用预算延续、三个工具的可读展示、已有产物/图片、宿主验证范围、刷新/重连后读取已有状态。保留一个Pydantic AI/Harness Coder和当前服务，不新增前端模型工具或第二套执行循环。

先检查不可丢失的语义：

- 答案必须回到原run/question/revision，不能只作为新聊天文本交给模型。
- 任务与浏览器连接分离；重新打开页面不能重新执行已有工具。
- Agent结束不能提前替代宿主outcome；waiting不能冒充成功。
- 任意客户端history/state不能覆盖宿主真值；编辑/取消等未实现操作不得被默认接入。

通过这些语义后才比较复用比例、代码维护、部署和体验。未实测不是不支持；不因缺少本项目实验就给候选打低性能分，也不以几行官方例子推导零集成成本。

## 同一目标下的对照

| 路线 | 可以复用 | 本项目仍需适配 | 对当前范围的判断 |
|---|---|---|---|
| **assistant-ui ExternalStore + 任务API/轻SSE** | 聊天组件、消息/工具展示、外部状态接口、按能力开放操作。 | run映射、history读取、问题/宿主结果组件、快照同步；继承当前宿主而不是复制恢复。 | **保留为首选基线。** 与已有文件宿主直接对应；首版不逐token输出，只有两种通知。优点是依赖的运行语义少，代价是自己维护小型视图投影。不是已证明最低成本。 |
| **同一assistant-ui + AG-UI runtime** | 以上组件加事件解析、消息重建、state事件、input_required中断/答复接口及历史适配接口。 | 把CodingRun工作段/问题/验证映射成AG-UI；提供宿主history/必要的resume适配；限制默认编辑/取消行为。 | **直接对照路线。** 若同步或流式需求扩大，现成runtime价值增加；对目前只通知再GET的范围，不保证总代码更少。界面组件可保持相同。 |
| **CopilotKit + AG-UI + 当前宿主** | 完整聊天表面、具名工具渲染、标准interrupt、自定义面板与状态能力。 | 与上一行相同的宿主AG-UI边界；线程历史和部署选择；禁用不需要的前端工具/模型驱动功能。 | **有效备选，撤回前期偏窄评价。** 公开类型有selfManagedAgents及可选runtimeUrl，不能预设必须上云或必须常驻另一Node服务；实际本地接法尚未验证。尚无证据比上一行更少宿主工作。 |
| **Chainlit** | Python步骤、文本问答、图片/文件、聊天页面；高级定制可用JSX。 | 将有连接/超时的提问显示与持久question ledger分开；对接会话持久化/恢复与原run，不依赖等待协程活着。 | **Python优先的强备选。** 基础页面很可能省前端代码，但这个推断不等于总成本更低。其内置恢复要求认证/持久化，是否增加负担需结合具体接入，而非直接排除。 |
| **Open WebUI / LibreChat成品应用** | 会话列表、聊天、账户/历史及各自插件/扩展能力。 | 为整个CodingRun提供受控入口，映射平台会话和宿主state；处理按钮/回调和平台生命周期。 | 适合“把Agent接进已有聊天产品”的目标；当前独立仿真工作台仍需宿主桥接与领域面板，不进入本轮第一组运行对照。不能声称它们无法接入。 |
| **AI Elements / Ant Design X** | 专门组件及对应SDK的消息/请求能力。 | 较多任务/会话装配和宿主绑定仍由项目做。 | 可作为组件基础，但暂无本项目证据显示优于assistant-ui的外部状态组合；不再新增并行原型。 |

Gradio和NiceGUI仍是Python页面路线的有效参考。上一轮已覆盖，当前没有新的事实要求把它们晋升为第一组对照；这是范围收敛，不是成熟度否定。

## 发布源码中真正影响取舍的事实

### assistant-ui AG-UI 0.0.59

- 类型包含`input_required`，interrupt可带responseSchema，resume entry可带任意payload；它能表达文本补参，不局限于批准/拒绝。
- `resumeTranscript`支持full/appended，默认full；已明确考虑“宿主已有checkpoint”和“从请求重建历史”的差异。项目应让宿主history保持权威，不照抄默认整段history追加。
- `autoCancelPendingToolCalls`默认true，但标准pending interrupts有单独门禁。不能把普通未决工具与原生interrupt混为一谈。
- useAgUiRuntime内部注册onEdit/onReload/onCancel/setMessages等处理函数；相比直接ExternalStore可不提供对应回调，使用AG-UI路线需要主动限制界面动作与后端命令。可配置/可定制不等于不支持，也不等于默认符合本项目。
- resumeInFlightRun在history.resume缺失时报告错误并跳过。它提供可接入的流恢复机制，不为未知后端凭空恢复。

### CopilotKit 1.71.1

- useInterrupt的发布声明明确支持标准RUN_FINISHED interrupt及legacy自定义事件，提供resolve(payload)，可以在聊天中或自行布局渲染。
- useRenderTool用于展示已有调用，useHumanInTheLoop属于另一种前端工具入口。之前过于依赖后者的教程进行比较，范围偏窄。
- provider声明有selfManagedAgents、可选runtimeUrl和可选平台凭证字段。只能据此认定有自行管理Agent的API入口，不能当作无额外服务部署已实测。

上述源码证据加强了“通用Agent前端已有可复用基础”的判断，也明确了实际配置责任。某包依赖中出现cloud、MCP或模型SDK，不代表本项目必须启用对应服务或把权限交给模型；是否会被打包、执行或联网需要构建/网络验证。

## 宿主边界仍需保留，但应当只是适配

Pydantic AI 2.33.0安装源码的AG-UI resume入口生成审批结果；当前产品ask_user使用DeferredToolRequests.calls并由宿主submit_answer落盘。这是本项目与默认适配器的一个具体差异，不能扩大成“AG-UI没有用户输入协议”。正确的候选接法是把产品waiting投影为input_required，并将resume payload先交宿主验证/保存，再形成原生延期工具结果。

同理，协议工作段结束可以携带waiting/host verification状态，不需要为了保留verifier而放弃整个标准流。需要检查消息结束、标准成功状态与产品completed的不同层级；只是渲染卡片的库并不替宿主形成可信结论。

## 最终评估与下一步

**保留FastAPI、assistant-ui、当前CodingRun宿主；保持现有轻SSE方案为对照基线，不立即改ADR。** 理由是已确认范围只需要阶段更新与快照恢复，直接外部状态接口有明确职责匹配，而新的包证据尚未证明标准适配能减少总工作。

**第一组实际对照只保留两条：同一assistant-ui页面的ExternalStore路径与AG-UI路径。** 使用同一宿主、任务、消息、禁止操作和验收场景，控制界面不同带来的干扰。CopilotKit保留为标准AG-UI宿主可复用后的替代界面；若第一组暴露特定组件/交互缺口，再决定是否有必要实测。Chainlit保留为用户明确把少写React放在第一位时的可选路线，不静默改变当前已确认偏好。

对照必须交付以下清单，而不是只报能运行：

1. 库直接提供哪些界面、客户端和中断/历史行为。
2. 新增了哪些宿主适配函数、状态写入者、常驻服务和依赖。
3. 本项目六项场景是否通过：补参、刷新/新浏览器、断连、宿主拒绝完成声明、重复答案、禁止操作。
4. 哪些库默认行为被关闭或改写，是否保持模型工具schema和执行次数。
5. 消息/状态同步代码是否实际减少，调试和升级是否更复杂；不以下载体积或总行数替代结论。

如果AG-UI减少自定义投影/同步、没有扩大运行权限或引入不必要服务，并通过同一组合同，再提出协议替代；否则保留轻通知。两条都不合格时才重新打开前端库选择。当前足以决定实验顺序，还不足以给最终工时或稳定性排序。

## 一手来源

- [assistant-ui ExternalStoreRuntime](https://www.assistant-ui.com/docs/runtimes/custom/external-store)、[AG-UI runtime](https://www.assistant-ui.com/docs/runtimes/ag-ui/overview)。
- [CopilotKit Pydantic AI](https://docs.copilotkit.ai/pydantic-ai)、[人工交互教程](https://docs.copilotkit.ai/pydantic-ai/human-in-the-loop)、本次发布包公开声明见本地证据。
- [Chainlit AskUserMessage](https://docs.chainlit.io/api-reference/ask/ask-for-input)、[on_chat_resume](https://docs.chainlit.io/api-reference/lifecycle-hooks/on-chat-resume)。
- [Open WebUI事件生命周期](https://docs.openwebui.com/features/extensibility/plugin/development/events/)、[LibreChat自定义入口](https://www.librechat.ai/docs/quick_start/custom_endpoints)。
- npm元数据和发布包integrity信息保留在[版本证据](evidence/agent-ui-recheck-20260913.json)，不采用二手排名或营销描述作为本项目运行证据。
