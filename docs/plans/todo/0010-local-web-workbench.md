# PLAN-0010：本地仿真工作台集成与验收

| 状态 | 最后更新 |
|---|---|
| 进行中：工作台工程实现继续补齐现有目录、删除会话和响应式体验；两条新真实主线仍待执行 | 2026-09-23 |

## 背景与依赖

将 [ADR-0025](../../adr/0025-local-web-workbench-over-coding-run.md) 的接口与 assistant-ui 组件组合成用户可用的本地工作台。前置是已完成的 [PLAN-0008](../done/0008-assistant-ui-integration-qualification.md) 确切版本接线资格，以及已完成的 [PLAN-0009](../done/0009-coding-run-web-api.md) API/生命周期验收；本计划已进入实施。

独立交付物是包含页面、静态构建、部署入口、浏览器行为证据和两条新真实产品主线记录的首版工作台。后端已可用不等于本计划完成。

## 当前设计与缺口

实施前产品尚无 `webui/`，官方 ai-chat-ui 的历史实验不是该页面的雏形依赖。已观察到的待答显示错误、浏览器历史不等于宿主历史、文件/宿主数据缺渲染等问题，是新页面必须主动防止的行为。

现有运行和物理成绩存在独立限制：PLAN-0006 当前仍以简单建模与澄清为重点，并保留真实脚本/模型/Provider失败。UI可以正确显示这些失败，但不能据此声称简单建模已经稳定交付。现有 runtime 未实现外部Job重附着和人工reconcile解锁，页面也不提供这样的按钮。

## 技术路线

- `webui/src/api/` 保存OpenAPI生成类型与client；`runtime/CodingRunRuntime.tsx` 映射宿主快照和稳定消息ID至 ExternalStoreRuntime。服务端 run ID 为任务身份，缓存按 run/revision 隔离，浏览器不存权威状态。
- `features/tasks` 是任务列表与新建表单；`conversation` 复用通用聊天；`execution` 展示真实三类Abaqus工具卡片；`artifacts` 查看脚本/JSON/已有PNG和下载；`verification` 展示宿主scope、失败项和freshness。中间问题表单携带question ID/revision，答案成功以后按服务器状态更新，不本地伪造工具成功。
- 页面采用左任务、中问答/执行、右结果布局。内部版本/hash放详情；缺图片显示“尚无模型预览”。运行状态、连接状态、模型回复、宿主验收分别呈现。
- 所有写操作走REST；SSE只是使快照失效的提示。刷新、回到前台、重新订阅时读取宿主；多标签切换丢弃已过期请求响应，late-answer revision后重取当前产物，不沿用旧绿标。
- 模型或Abaqus执行中不接受普通消息；waiting使用问题表单，interrupted使用后端允许的继续，terminal会话可追加下一条消息。界面不提供暂停、编辑/分叉、重新生成、任意模型配置或运行中自由追加；Web Solver另有当前宿主控制范围内的状态、耗时与取消。
- 接入FastAPI同源静态构建。Node仅用于开发/构建；验证干净安装的CLI与Web两种入口，防止本机node_modules/var引用变成运行依赖。UI改动使用自身锁文件和来源/许可证记录，不复制官方原型资产。

首版边界维持本地单用户、单模型工作段、ask_user_v1和execution_evidence。新会话可使用托管空workspace
或显式选择已有ASCII目录，创建后固定绑定；后续按用户授权增加了公开回复流、会话内OBJ几何预览、已有PNG
独立展示、Web Solver submit/wait和永久删除会话记录。上传、结果场/云图、任意`.cae`导入、数据库/队列和多用户仍不纳入。

## 评估

| 项目 | 判断与依据 |
|---|---|
| 价值 | 高；把目前需要终端/日志才能理解的操作连成用户可操作的流程。 |
| 技术难度 | 中高；聊天组件可复用，难点是准确映射宿主状态及跨刷新/修订/断连的交互。 |
| 最可能的返工 | 把组件默认自动发送/消息合并当正确行为；按浏览器缓存重建任务；旧产物仍显示为当前；开发环境能跑而打包缺资源。 |
| 工作量估计 | 4–6个专注工程日，含页面、集成、浏览器回归和新E2E准备/执行记录；Provider等待、许可证故障、额外学习和仿真修复不计入此估计。 |
| 不确定性 | 前置组件接线资格已通过，正式API/页面已通过离线工程验收；真实模型仍可能漏问/写错脚本或超时。UI不会消除这些失败。 |

三份计划合计规划区间为9–15个专注工程日，前提为单人熟悉Python且具备基础React/TS、按固定范围顺序推进。它是量级估计，不是自动化执行耗时、自然日承诺或完整CAE能力承诺；PLAN-0008结束后依据实际接口差异重新估计，不能用预算耗尽提前标完。

后续[复用完整性审查](../../research/agent-ui-reuse-audit-20260913.md)发现前期未充分覆盖成熟协议和接入库；上述区间标为低置信度历史粗估，不用于证明当前路线最省或做可靠排期。待PLAN-0008明确实际复用/新增范围后重估，不能把成熟组件已经提供的功能重复计为自研。

## 完成标准与验证

| 场景 | 明确预期与验证方式 |
|---|---|
| 基础流程 | 通过页面新建→待答→回答→继续→宿主结果，模型历史与问题ID可追溯；使用真实API和确定性FunctionModel E2E。 |
| 正负结果 | 模型自述完成而verifier失败时，醒目显示失败；execution_evidence成功也不显示物理通过。 |
| 页面刷新/新浏览器 | 从同一宿主取得列表、原问题和结果；不依赖旧浏览器IndexedDB，不产生新run或模型调用。 |
| 多标签/答复重传 | 任务间不串答案；重复同答复不重复执行，冲突可解释；答复已保存但未调度显示可继续。 |
| SSE与修订 | 重连、迟到响应、慢订阅和revision切换后最终显示正确状态；无重复执行，旧产物不冒充新结果。 |
| 页面可用性 | loading/等待/执行/失败/连接断开/无图片/超大文件均有可读状态；桌面布局、键盘答题和禁用操作实际检查，浏览器控制台无未解释错误。 |
| 安装与构建 | 锁定依赖typecheck/build通过，打包后同源页面/API/图片可运行，无隐式CDN或研究目录依赖；CLI回归仍通过。 |
| 新真实建模主线 | 缺厚度板、完整三维杆各一次独立Web观察，需求与答案明确，启动前冻结新profile/预算，核对原始证据。板实际先问厚度再运行，杆使用已给输入；完整交付及宿主verification可由页面查看。 |

真实运行按届时明确的任务与预算授权执行，本次“计入计划，评估”不启动新批次，也不复用PLAN-0006已消费的授权。每题失败、中断或未完成都原样保留，不自动补跑/换样本；数值/物理审查独立于产品UI和execution_evidence，不向Agent暗中提供oracle。

工程gate和真实主线分别记录。只有代码/安装/浏览器验收及两条真实主线要求都达到，才将本计划标为已完成；若只有工程gate通过而真实任务未交付，明确记录工程就绪、主线未完成，保持计划进行中。自然模型失败不是UI回归证据，也不能据此放宽产品状态、验收范围或冻结Case合同。

## 与其他工作的关系

[PLAN-0006](0006-domain-task-evaluation-baseline.md) 保持其既定优先级、需求和历史，不因加入UI改写。新增Web真实记录是独立观察，不能回填其旧trial/gate。PLAN-0008/0009可在现有模型行为未改善时完成；本计划的最终真实主线结果会受模型和Abaqus实际表现影响。

## 实施进度

React/TS + assistant-ui ExternalStore页面、OpenAPI生成类型、REST命令、SSE同步、工具记录、问答与
显式继续、文件/PNG/OBJ几何预览和打包入口已落地。实际结构采用少量功能文件，不为初版创建空的features层级；
职责见`webui/README.md`及架构总览。没有引入官方UI源码或AG-UI执行桥。

工程结果：1080项离线测试通过（含21项Web），20项本机专用夹具缺失跳过、27项持证未运行；类型检查、
Ruff、前端typecheck/build和独立wheel安装检查通过。Edge真实浏览器验证了新建/待答/回答/通过、
模型自述完成但宿主失败、答复后中断与全新浏览器显式继续、刷新与文本预览。注入服务重启时的连接失败与
过期会话401均保留，重新使用会话链接可恢复；负例与恢复浏览器没有未解释的控制台错误。修复了对话答复
顺序、同页fragment重新认证、迟到响应以及空文件/超大文件显示边界。截图位于`output/playwright/`，原始
记录位于`var/audits/web-implementation/browser-*`；模型为FunctionModel，Abaqus调用为0。

两条真实任务尚未启动。拟沿用已明确的缺厚度板/完整三维杆需求内容，使用独立托管空工作区和完整内联goal；
这是独立Web观察，不继承旧pair spec授权或成绩。每题30个模型请求、9次Abaqus（含最多3次Solver）、
2次提问、2700秒活动时间；Go/Flash enabled_high、Planning开、HTTP60秒/额外重试10。每题只启动一次，
失败保留、不自动补跑或续跑；实际提问由用户确认答案。本计划保持进行中，直到真实主线验收要求达到。

2026-09-14用户纠正输入自由度：完整板/杆仅是验收案例，入口允许任何问题和局部步骤。已新增launch v5的文字交付路径并修正首页文案，见ADR-0026。旧web-acceptance-proposal的源码绑定因此失效，不能按旧seal启动；本轮先验证范围修正，不自动启动持证批次。

按用户后续要求，基础多轮对话先于真实仿真验收实施：首条直接发送、同会话追加、持久历史、草稿/键盘交互已接入。新增7项消息测试，完整离线1091通过、20项本机夹具缺失跳过、27项持证未运行；确定性Edge三轮与全新浏览器续聊通过。类型和构建通过，真实Provider/Abaqus批次仍未启动。记录在var/audits/web-implementation/conversation-validation.json。

用户标注要求的三项已实施：复制/重命名、公开回复流式显示、停止模型回复。新增8项测试，完整离线1099通过、20项本机夹具缺失跳过、27项持证未运行；Go适配器的模拟HTTP流验证了stream、thinking/high、503重试和连接关闭。浏览器完成复制内容核对、改名刷新、停止/刷新/复制部分回复/续聊检查，0控制台错误。原真实Provider/Abaqus批次未启动；详见var/audits/web-implementation/stream-validation.json。

用户要求优先复用库原生基础组件后，Composer与会话列表已替换为assistant-ui primitives，运行状态统一接入WorkbenchRuntime。TypeScript/build/wheel通过；浏览器覆盖换行/IME、首次与追加发送、草稿刷新/隔离、失败重试、回执丢失、原生列表键盘切换、改名、澄清、停止与复制。后端未变，本轮未重复全仓Python测试、未调用真实Provider/Abaqus。详细记录var/audits/web-implementation/native-components-validation.json；受控threadList API的上游弃用提示已记录在webui/README。

2026-09-15完成Pi体验参考的首批1–6项：统一暗色token、正文宽度和交互密度；整理宿主阶段/连接/模型/推理/用量/wall time；工具卡片改为结构化摘要优先；文件、日志和verification改为按需侧栏；增加会话搜索、`Ctrl/Cmd+K`、`N`、`R`；所有可用操作继续由`can_answer/can_start/can_continue/can_stop`投影。`read_model.ToolSummary`从未截断的typed工具结果提取目标、阶段、耗时、超时、警告、错误和文件变更，前端不解析日志或exit code猜成功。复用了Pi的展示组件/样式与Catppuccin资产，未引入其session/runtime；来源、改编和MIT许可证见`webui/THIRD_PARTY_REUSE.md`。

当时完整离线结果为1100通过、20项本机夹具缺失跳过、27项持证未运行；Ruff、格式、BasedPyright、TypeScript、production build和wheel通过。浏览器覆盖待答、已完成execution_evidence、宿主失败、文件预览、搜索、命令面板、快捷键、响应式侧栏和图标静态路由，最终控制台无未解释错误。记录见`var/audits/web-implementation/ui-six-validation.json`及`output/playwright/ui-six-*.png`。该批真实Provider与Abaqus调用均为0。

2026-09-15继续完成会话内模型预览：PNG与OBJ独立展示，Online 3D Viewer只读取当前CAE工具卡fresh artifact。真实Abaqus2024Runner→artifact API→真实会话卡链路已分别验证简单实体、基础装配、壳面和梁中心线；Provider步骤由确定性FunctionModel控制，不把几何预览外推为结果云图或任意Abaqus对象支持。完整边界见`docs/research/session-model-preview-reuse-20260915.md`。

2026-09-16把Web Solver从模型工作段中分离为宿主持久`ToolExecution`：状态、耗时、PID和当前宿主取消能力进入快照与页面；完成后以原tool call恢复同一AgentInvocation，不创建用户continuation或重复Solver。成功、Solver早于模型段释放完成的竞态、取消、重启待核查均有端到端覆盖。最终全仓离线1112通过、47项按许可证或本机固定资源条件跳过；Ruff、格式、BasedPyright、TypeScript production build和wheel通过。真实浏览器验证了求解状态、递增耗时、取消及详情；本轮没有执行新的真实Provider或持证长时Solver取消。

2026-09-23按实际使用反馈完成侧栏折叠、900px断点、窗口缩放、文件弹窗和首次启动错误显示修复；输入框按
Pi单行起步并自动增长。新增已有工作区选择和永久删除会话：前者原地读写工程并保持state在外，后者先完成
本宿主模型/工具/Solver收尾再永久清理记录，工程文件保留；registration升级为v3，旧v2明确拒绝。工作区与
删除语义见ADR-0034/0035，Pi及Harness改编来源和MIT许可证已进入包内记录。真实Chromium覆盖原生窗口缩放、
路径负例、已有文件baseline、问答/交付、删除确认/失败重试/多标签和窄屏；FunctionModel与假进程不构成真实
Provider/Abaqus验收。当前质量门：默认smoke 96通过；component 653通过、8项因固定abqpy/持证资源缺失跳过；
evaluation 436通过；Ruff、格式、BasedPyright、前端production build和wheel检查通过。

本轮实现拆为可回滚提交：`59e3725`保存工作区绑定、删除生命周期、受控取消与后端回归，`e6616ae`保存
响应式布局、Pi风格输入、目录/删除交互、生成API与构建许可证接线；架构、ADR和计划由随后文档提交收口。

2026-09-23继续补齐会话内 runtime 权限：等待用户、空闲中断和终态会话可修改未来 Abaqus/Solver permission，
待答问题和累计usage保持；活动、待消费Solver与`needs_reconcile`拒绝修改。命令使用operation ID和sequence
核对，HTTP重试不重复追加；修改不自动继续Agent或取消执行。确定性宿主测试覆盖同run“先澄清、后批准
Solver、再继续”，浏览器覆盖桌面/窄屏、终态不自动请求和活动禁用；未执行真实Provider或Abaqus。
实现提交为`1aca0ef`（amendment、Web宿主与回归）和`1fe05de`（权限编辑器、生成API与交互）。

本工作树已完成差异审查并拆为可回滚提交：`d3577f0`保存共享CodingRun/FastAPI/deferred Solver后端与测试，`c259596`保存assistant-ui前端、模型预览、第三方资产与构建接线；架构、ADR落实和本计划由随后独立文档提交收口。Abaqus replay、浏览器截图、node_modules、构建静态目录和wheel均按生成物忽略。

## 后续剩余工作

以下三组不应混为同一个“UI未完成”清单。

### PLAN-0010关闭前必须完成

1. 冻结当次Web profile、需求、答案和预算后，分别执行一次缺厚度板与完整三维杆真实主线；失败、中断、Provider timeout和Abaqus错误均原样保留，不自动补跑。
2. 核对页面展示、宿主state、原始日志、artifact freshness和verification一致，并另做数值/物理审查；`execution_evidence`不能替代物理判断。
3. 后续集成继续保留原工作区已有未提交内容，不能用合并覆盖或夹带它们。

### 可继续做、但不阻塞当前计划关闭

| 项目 | 难度/兼容度 | 当前缺口 |
|---|---|---|
| Timeline四类条目 | 中；可复用现有timeline API和assistant-ui消息投影 | 当前聊天、工具和顶部宿主状态已分开，但尚无统一的assistant/user/tool/host event时间线及稳定筛选。 |
| 失败与恢复专用卡片 | 中低；主要读取现有状态和错误码 | 已区分failed/interrupted/needs_reconcile/verifier，但Provider中断、状态冲突、runtime失败和刷新动作还可做成更具体的卡片。 |
| 文件查看器增强 | 低到中；纯前端部分兼容良好 | 当前支持文本/PNG/下载/hash/freshness；尚无语法高亮、文件内搜索、差异视图和更完整的Pi FileViewer体验。 |
| 会话列表便利操作 | 低；沿用现有registry | 可增加状态筛选、排序和键盘焦点细化；不引入Pi的project/session数据模型。 |
| 可访问性和窄屏细修 | 低；纯前端 | 继续检查焦点返回、屏幕阅读器文案、对比度和手机尺寸；不改变产品能力。 |

### 必须先扩展后端合同，当前不实施

| 能力 | 难度 | 所需前置 |
|---|---|---|
| 历史revision浏览与切换 | 中高 | 后端提供历史快照、artifact身份和只读切换合同，旧绿标不得沿用。 |
| 更细运行事件与统一时间线 | 中高 | 当前已有typed phase、Solver ToolExecution和页面轮询；若继续扩展须保持事件不是恢复账本，也不从stdout推断。 |
| structured `ask_user` | 中 | 扩展问题ledger与答案schema，同时保留旧字符串问题兼容。 |
| 运行中steer/follow-up队列 | 高 | 明确排队、预算、持久化、丢失回执、恢复和执行顺序语义。 |
| 跨进程Abaqus cancel/reconcile | 很高 | 当前只能终止本宿主仍控制的Solver进程树；仍需外部Job身份、部分artifact裁决、PID/Job重附着和审计恢复。 |
| `needs_reconcile`人工解除/重附着 | 很高 | 外部Job身份、PID/Job核对、审计和幂等恢复机制。 |
| 上传、自动Abaqus截图和3D结果场 | 中高到高 | 基础OBJ几何与已有PNG已展示；上传、CAE/ODB受控读取、结果变量/帧/平均规则和格式/大小合同仍缺。 |
| 数据库、并发队列和多用户 | 很高 | 持久Job Service、权限、Coordinator及跨进程许可证协调；不属于当前本地单用户首版。 |

除真实主线和Git交付外，后两组只有在实际使用或评测暴露需求后再进入独立计划；不因Pi具备这些功能而自动扩展。
