# 系统框架：部署与状态所有权

## 两个解释器和一个外部运行边界

宿主 Python 3.12 运行 CLI/Web、Pydantic AI/Harness、CodingRunService 和评测；Abaqus 2024 的 Python 3.10
运行 CAE/odbAccess 脚本。宿主依赖不能直接当 vendor Python 依赖，宿主也不直接导入 Abaqus API。
两边通过工作区文件、固定 launcher 参数和 execution result 协作。

产品入口是本地 Windows CLI 和只监听回环地址的 Web 工作台。二者通过共享 application/composition 装配
通用执行 verifier，固定 evaluator 装配对应物理 verifier。宿主调用一个 Coder，Coder 使用文件工具和三个
typed runtime tools；组件图见 [总览](overview.md)。

Web CLI 在服务 started 后默认打开普通 `127.0.0.1` URL。浏览器首次 GET session 时无感取得进程内 cookie
和 CSRF；用户不管理启动 token。非回环地址、远程密码登录和代理部署不在当前入口合同内。

## 谁拥有哪些状态

| 状态或资源 | 初始化 / 持续维护者 | 其他组件如何使用 |
|---|---|---|
| workspace 与 initial snapshot | workspace resolver、宿主首次启动；新 task revision 建立新的验收基线 | Coder 读写文件，runner/verifier 核对输入和变化 |
| immutable request / base launch | CLI、Web 或 evaluator composition | 恢复核对原合同，amendment 形成 effective overlay，不改写原实验请求 |
| turn、revision、usage、active wall | CodingRunService | 普通产品只观察 usage；固定评测可有限，模型不能重置或直接写 outcome |
| 模型 history、settled snapshot、tool effect | Pydantic AI/Harness 与宿主持久化适配 | resume 选择可继续边界，未决 vendor effect 触发 `needs_reconcile` |
| 问题、history 关联、用户答复 | 宿主问答组件 | Console 在执行锁外取答；锁内按 ID 持久化和消费 |
| Web Thread、invocation 与命令回执 | Web registry/controller | 记录用户消息、执行关联与传输幂等；不拥有预算、verification 或 outcome |
| Web Solver `ToolExecution` | Web controller 与独立 Solver 事件循环 | submit 记录 execution ID、进程观察、取消和终态结果；wait 读取同一结果，不使用 deferred frontier |
| 子进程与原始执行观察 | Abaqus runner | 工具保存 execution journal，模型收到诊断，verifier 独立判断 scope |
| 查询、计数、实际已交付证据 | run-scoped dependencies 与查询 journal | resume 合并记录；候选、读卡和参数分别恢复 |
| outcome 与 verification | Service 聚合宿主 verifier 结果 | 当前 outcome 是投影，历史 revision 结果保留；Agent 不能宣告成功 |
| 固定 Case 的物理真值 | evaluator / trusted oracle | 不注册为模型工具；只反馈当前 scope 允许的验证失败证据 |

## 配置输入、运行合同与恢复记录

每个字段的具体规则见[配置逐字段合同](configuration-reference.md)；本节说明跨组件的生命周期与所有权。

模型 `max_tokens` 是用户于 2026-10-06 明确允许的单项持久设置：应用数据目录中的
`model-output-settings.json` 按提供商/模型保存，仅新会话解析时读取；本次显式输入优先。
解析后保存到 launch，已有会话不重读；不恢复通用用户/项目 settings.json 或默认模型选择。

**第一阶段已实施：** 已撤回用户/项目偏好读取，改用程序默认值和本次显式输入；内置 Skill、静态 wheel/cache
由程序管理，runner 以 abq2024 命令启动，文档模式由 spec 推导，Web 管理根提供默认。
Catalog 已随应用包安装并自动定位，原生 provider 与动态模型窗口已接入。见
[ADR-0045](../adr/0045-use-explicit-run-options-without-preference-files.md)、
[ADR-0046](../adr/0046-use-native-providers-and-available-model-selection.md)与[逐字段状态](configuration-reference.md)。

配置按生命周期区分：进程入口取得本次宿主设置和凭据；新会话把默认值与显式输入解析为运行合同；
运行中保存修订和进度；恢复重新加载合同及状态。用户通过 CLI/Web 操作，state 中的 JSON 由程序维护。
`launch.json` 是其中的启动快照，首次执行也会写入，单独持有它不能恢复会话。

| 对象 / 载体 | 保存什么 | 生命周期与所有者 |
|---|---|---|
| CLI 参数、Web 创建请求、`ServerConfig` | 本次输入与非模型选项默认值 | 新会话模型必须由 CLI/创建请求明确指定；ServerConfig 不持有模型默认值；其余选项由入口合并 |
| 宿主进程设置与凭据 | Web 端口、是否打开浏览器、遥测启用、模型 API key 等 | 每次启动由宿主取得；凭据单独注入，不写入 request/launch |
| 用户应用数据目录 `auth.json` | 本应用交互保存的 API Key / OAuth 凭据 | CLI/Web 共用，更新/删除与令牌刷新写回；独立于会话，无路径配置，环境密钥不自动落盘，见 [ADR-0047](../adr/0047-persist-host-credentials-and-bundle-model-catalog.md) |
| 用户缓存目录 `models/models.dev.json` | 最近一次有效的公开模型目录，不含凭据 | 程序首次需要及显式刷新时下载、校验并原子替换；失败使用缓存，旧会话不重新选择或推导窗口，见 [ADR-0048](../adr/0048-fetch-models-dev-with-local-cache.md) |
| `request.json` | 初始任务、workspace/state、runtime、limits、权限、文件范围和知识证据绑定 | 由装配准备，Service 首次执行时持锁保存，之后作为 immutable base |
| `launch.json` | 初始模型及 HTTP 参数、工具/知识模式、Skill 与静态源位置、协议与策略绑定 | 由装配准备，Service 首次执行时持锁保存，之后作为 immutable base |
| `amendments.json` | 允许的配置修订、任务修订及对应证据边界 | 宿主在同一 state 锁下校验候选并提交；按序应用，不覆盖初始合同 |
| 内存中的有效 request/launch | 初始合同应用全部已提交修订后的结果 | 每次准备执行时重建；不增加另一份可独立修改的有效配置文件 |
| `resume_state.json`、Harness snapshot/journal 等 | 已运行到哪一步、累计 usage/active wall、待答问题、history、工具执行状态 | Service/Harness 持续维护；恢复时与有效合同和执行证据共同核对 |

`request` 描述任务与执行约束，`launch` 描述如何装配 Agent 和外部资源；它们共同构成原始运行合同。
资源路径用于找到内容，hash 用于确认内容身份，二者保留不同语义。协议版本和 Skill digest 是宿主记录，
不因此成为用户可调开关。已有部分绑定在两个载体中重复保存时，应核对一致性，不能各自形成修改入口。

### 新建、修订与恢复的来源规则

- **公共新会话输入**：共用 `RunOptions`、`option_arguments` 和 `options.resolve_run_config`。
  CLI 显式参数覆盖程序默认；Web 创建请求 `options` 覆盖本次服务 `overrides`，再使用程序默认。省略表示未指定，
  null/未知字段/错误类型拒绝，列表整体替换，空列表清空。原用户/项目设置文件不读取、不回写、不迁移或删除。
  CLI 路径相对 cwd，Web 请求路径相对 workspace。Skill 和静态来源由宿主定位/准备，普通用户不指定内部路径。
- **新建**：使用入口默认值和用户显式输入。Web 创建请求中的 Abaqus/Solver permission 可覆盖该次创建的
  服务默认权限；`ServerConfig` 的 `limits`、`runtime_policy`、`evaluation_tool_limits` 随后进入 request。
  服务不接受旧式 `budget` 输入，评测/测试调用方先通过 `CodingRunBudget.as_request_controls()` 转换。
- **修订**：只接受 typed amendment 允许的字段，校验完整候选后写 ledger。省略字段表示保持原值；显式
  `null` 不表示删除或恢复默认值，当前 patch 会拒绝它。不限时长使用 duration 的 `unlimited` 模式，
  不限次数的执行许可使用 permission 的 `allow` 模式，不能混用。
- **恢复**：`有效配置 = 原始 request/launch + 按序应用的 amendments`。当前 CLI/Web 的新会话默认值不参与
  这次计算；凭据和进程设置仍由本次宿主提供，并须满足运行合同的网关等装配要求。
  Web 凭据按 provider 独立捕获并移除继承环境中的密钥；恢复按保存的 provider 选取凭据，缺失则拒绝。
  修改新会话默认网关不会改变旧会话，也不要求旧会话等于本次服务默认网关。
  项目 settings.json 自身仍是 workspace 文件；外部修改它可能触发 workspace hash 检查而拒绝恢复。
  不重读偏好只保证不会重绑配置，不保证任意编辑项目文件后仍可继续。
- **执行进度**：配置修订不重置 usage、active wall 或执行记录。task revision 通过宿主另建验收基线，
  普通 continuation 依其独立 evidence baseline 处理；两者都不靠改写基础 JSON 实现。

例如，初始 `launch.static_preflight_mode="off"` 的会话恢复后仍采用 `off`；改变新会话的默认值不影响它。
若原 runtime policy 拒绝 Solver，用户通过允许的 amendment 改为 allow，原 request 保留 deny，ledger
记录修改，有效 request 采用 allow，累计执行用量继续保留。

当前可修改范围以实现中的 patch 为准：

| 内容 | 当前会话内的修改方式 |
|---|---|
| Abaqus/Solver permission、三个 runtime 阶段 timeout | `RuntimeExecutionPolicyPatch`；Web 当前仅开放 permission，CLI 开放相应权限与超时参数 |
| model provider、model name、HTTP retries | `CodingRunConfigPatch`；CLI 支持，仍受原推理策略等兼容约束 |
| goal / 显式任务合同 | task revision；归档旧 outcome 并建立新验收基线 |
| workspace、launcher、Skill/知识绑定、静态检查模式、HTTP timeout、推理策略、limits | 当前无 operational patch；通过新会话选择，恢复不重绑 |
| 累计 usage、执行结果、待答状态 | 由宿主按真实进度维护，不能作为配置覆盖 |

Web 创建但尚未首次执行的会话，先在 `registration.json` 保存 prepared request/launch 和 initial snapshot。
首次执行交给 Service 写 state；已有 `request.json` 后走 state 恢复路径，并核对 Web 登记的初始 request。
registration 不能覆盖恢复得到的有效合同。创建登记和运行 state 分属两个文件域，不承诺跨文件事务。
普通 CLI 会话没有 Web 登记；Web 从原 `request.json`、`launch.json` 和 `initial_snapshot.json` 构造入口视图，
不补写 registration，不替换请求或启动配置。后续读取、回答和追加消息沿用同一 state 与原恢复校验。

恢复还要检查 schema、资源绑定、workspace、修订序号和未决工具副作用；合法配置不等于可以安全继续。
不支持的开发期 state 显式拒绝，损坏记录不猜测补全；并发和 `needs_reconcile` 规则见下文。上述合同沿用
[ADR-0020](../adr/0020-resume-coding-runs-with-harness-step-persistence.md)、
[ADR-0021](../adr/0021-use-amendments-and-task-revisions-for-continuation.md) 与
[ADR-0030](../adr/0030-drop-pre-release-coding-run-state-compatibility.md)。
偏好文件的原始决定见 [ADR-0043](../adr/0043-separate-model-preferences-from-session-contracts.md)，其两级文件来源
已由 [ADR-0045](../adr/0045-use-explicit-run-options-without-preference-files.md) 撤回并停止读取。模型接线已支持原生 provider，Catalog 已接入包内分发。

### 实现位置与尚未收敛的边界

公共新会话参数定义、来源合并、可用模型筛选/稳定首项选择和静态源准备已共用。模型选择只决定新会话，恢复不因凭据或列表变化自动切换。CLI 对任务白名单、纯编辑、Concept/diagnostic
及 bounded document 等显式 profile 仍有自己的输入编排，Web 只创建普通 ask_user 会话，不把这些实验合同伪装为
已经开放的 Web 配置。CLI 自行构造 request/launch，Web 经 `application` 构造；二者共用 `composition`。
配置公共入口共用不等于全部产品/实验流程相同，后续演进继续保留 profile 合同差异。

Web 的 Catalog 与静态来源按各个会话的 launch 加载并核对 request binding；不再从服务的单一 Catalog 实例
恢复所有会话。本次服务的资源选择不会重绑已有会话。

### 字段归属

| 类别 | 字段 / 入口 | 全局与项目文件 | 会话记录 |
|---|---|---|---|
| 模型输入 | provider/name、HTTP retry/timeout、reasoning | 不读取，仅本次输入和程序默认 | 保存实际值；允许的修订另记 ledger |
| 资源输入 | Catalog 模式、静态开关、知识包 ID；Catalog/Skill/静态资源位置由宿主管理 | 不读取 | 保存实际资源选择及生成的版本/hash 绑定 |
| 宿主进程与显示 | Web root/port/no-open、stream_model、context warning、phoenix | 不进入新会话偏好链；由宿主启动/展示层提供 | 不由旧会话恢复这些设置 |
| 凭据 | 模型 key；本机 Phoenix 无需 token | 不接收 | 不保存，当前宿主单独注入 |
| 授权和限制 | Abaqus/Solver permission、runtime timeout、limits | 不由项目文件自动授予或覆盖 | 保存实际合同与累计用量；权限修订走显式命令 |
| 任务和显式 profile | goal、workspace/state、run ID、回答、白名单、纯编辑/文档 spec、Concept/diagnostic 参数 | 不作为用户默认偏好 | 按任务、启动合同或人机交接语义记录 |
| 宿主生成值 | schema/protocol、Skill digest、知识 hash、usage、history、effect | 不接收 | 按恢复与证据合同保存 |

CLI 区分未提供与显式指定的静态检查模式：本次输入未指定时采用 off，恢复时显式传入 on/off 均拒绝，
沿用保存的 launch 值。私有知识包可在新建时选择，内置 Skill 和静态源由程序确定；生成会话绑定后，恢复不重读偏好来替换它们。

后续增加字段时，就近明确其输入来源、合同所有者、是否落盘、能否 amendment，以及恢复时的校验方式。
只服务当前进程的设置留在宿主；会影响会话行为的配置进入相应运行合同；进度和证据进入状态记录。
具体实现见 [launch](../../src/abaqus_sim_agent/coding/launch.py)、
[options](../../src/abaqus_sim_agent/coding/options.py)、
[amendments](../../src/abaqus_sim_agent/coding/amendments.py)、
[application](../../src/abaqus_sim_agent/coding/application.py)、
[resume](../../src/abaqus_sim_agent/coding/resume.py)。

## 数据放在哪里

**Workspace** 保存模型可见的脚本和 CAE/INP/ODB/JSON 等交付物。交互 CLI 默认使用启动时的绝对工作目录，
也可显式指定。公开力学从零Case要求空目录；verified-seed change 先验证 source 的合同、完成状态和 hash，
缓存脚本 bytes 后再建立新工作区，避免校验后源文件改变。

**State directory** 必须位于 workspace 外；普通交互默认在 `%LOCALAPPDATA%/abaqus-sim-agent/runs/<run_id>`。
其记录按职责分为：

- 基础合同和修订：`request.json`、`launch.json`、`amendments.json`、`initial_snapshot.json`。
- 会话恢复：`resume_state.json`、`history.json`、`step-persistence/`、`coding_run.lock`。
- 人工交接：`user_questions.json` 与按问题 ID 关联的 history 快照。
- 执行与知识证据：`execution_results.jsonl`、raw stdout/stderr、`api_lookups.jsonl`、
  `api_discoveries.jsonl`、`concept_lookups.jsonl`，以及显式策略的额外 journal。
- 验收：当前 `outcome.json`、verification 和历史 `outcomes/task-*/`；评测另保存 oracle 和 diff/provenance。

**Web management root** 默认位于应用数据目录的 `web` 子目录，必须是 ASCII 根目录，保存 `registry/`、`cache/`
和 `control/`。默认 Web CLI 将 `state_root` 设为普通 CLI 的应用数据目录 `runs/`；新 Web 会话也在那里保存 state。
程序化宿主省略 `state_root`，或 Web 显式传 `--root` 时，仍使用隔离的 `root/states/`。旧 Web 登记保留原
`root/states/<run_id>`，不搬迁；同一 ID 在两处同时存在时拒绝选择，不能静默覆盖或合并。
`registry/<run_id>/thread.json`、invocation、命令回执和 `ToolExecution` 仍是 Web 管理记录。CLI 会话不以此登记
作为读取前提；默认列表合并 Web 登记和公共状态目录中具有 request/launch 的会话，具体读取要求当前普通产品协议。
不兼容或损坏的记录显示不可用，不自动修复。Web 新建会话仍保留首次执行前的登记，以支持创建幂等与调度失败恢复。
`workspaces/` 仅为旧托管工作区登记保留。两类文件的顺序由恢复协议协调，不是数据库事务。

新会话必须显式选择已有本机 ASCII 目录并原地读写；未选择目录时不创建 run。所选目录与管理根、状态根互不包含，
会话建立后不可切换。registration v3 明确保存目录来源，加载时复核路径；旧 v2 登记明确拒绝，不猜测升级。
旧 managed 登记继续按原路径读取；新登记始终为 selected。目录浏览 API 只列出有界子目录，不读取文件内容或创建运行。
已有文件进入原始 workspace snapshot，仍按 fresh evidence 合同验收，见 [ADR-0034](../adr/0034-bind-selected-workspaces-for-web-runs.md) 和 [ADR-0041](../adr/0041-require-selected-workspace-for-web-runs.md)。

Web 确认删除会话后，先等待本宿主模型/工具和 Solver 实际收尾，再在 state 锁内永久清理该会话的 state
和 registry；工程目录及文件保留。`control/deletions` 只保存最小幂等/清理回执，不是回收站。无法确认的
外部执行和未决 effect 不通过删除记录掩盖，见 [ADR-0035](../adr/0035-delete-web-conversation-records-and-keep-workspaces.md)。
默认 CLI/Web 的状态共享只支持空闲时切换入口。外部进程持有 state 锁时，Web 禁止写操作和停止；不新增 PID
重附着、跨进程取消或全局工作区锁。普通 CLI 的终态 resume/任务修订语义不变，评测仍使用原批次入口。

已有 Web 会话可在空闲、待答或终态通过 operational amendment 覆盖 Abaqus/Solver permission。待答问题、
task revision、workspace/execution baseline 和累计 usage 保持；写入不调度 Agent，也不取消已提交执行。
活动工作段、待消费 Solver、`needs_reconcile`、外部锁或不确定命令状态拒绝修改。有限次数始终表示会话累计
总上限，调低至不高于已用量会使后续调用自然被现有 runtime gate 拒绝。

Catalog 随程序包安装，显式文档索引仍是外部知识数据，二者与 run state 分开；可恢复产品入口核对 request/launch 中的
内容绑定。显式工厂的只读参考资料尚无同等 launch 快照保证，见 [知识接入](knowledge-access.md)。

## 并发、持久化与崩溃边界

### 可选启用的遥测

CLI/Web 和首轮评测入口使用 `--phoenix` 启动一次官方 Phoenix SDK 会话。默认关闭；只向无鉴权本机 HTTP
服务发送。普通产品默认连接 `127.0.0.1:6006`，可用 `PHOENIX_COLLECTOR_ENDPOINT` 改本机端口；测评CLI在显式启用时
直接启动本次服务并固定使用6006，结束后关闭服务，不复用现有进程。服务启动或端口检查失败时不执行任务。不读取工作区
`.env.phoenix` 或项目偏好；入口负责配置、跨线程上下文传播及有界关闭，每个 run 不重配全局 SDK。
完整 Logfire SDK、旧启用参数和云端 Token 接线已移除；Pydantic 上游仍间接依赖 `logfire-api` 空操作 shim。

Pydantic AI 原生 instrumentation 采集模型和工具；官方 OpenInference processor 转换 Phoenix 消息展示，
官方 HTTP instrumentation 采集 Web 请求。宿主增加工作段、runtime、verifier，并以
run/revision/continuation/invocation/execution ID 关联。`session.id` 使用现有 run ID；后台执行与
submit/wait 分开记录，等待后开启新工作段，不恢复跨进程 span。usage 只记录明确标注的累计快照。

Evals 2.47 的内置 Case spans 依赖完整 Logfire SDK，移除后由公开 `CaseLifecycle` 建立每次 Case 的
`evaluation.case` span，并填回 `ReportCase` 的 trace/span ID；原 span_tree evaluator 继续使用同一
provider。评测专用 Phoenix client 继续将已有 assertion/score/label 复制为 span annotation。
公开力学 run/continue/resume 和任务基线共享入口在显式启用后，还会先通过官方 client 自动创建或复用题集、
创建本次原生 Experiment，再由原 Evals 执行任务。题集按公开输入和元数据内容摘要复用，绑定实际平台版本；
同名题集内容变化时拒绝运行，不上传隐藏参考答案。每次运行或恢复拥有独立实验，只读 report 不创建实验。

每题在原生命周期保存本地证据后，上传输出、评分、异常和已有 Trace 关联；本次未执行的结果投影不作为新运行。
服务不可用或初始登记失败时，不启动模型或 Abaqus；中途写入失败保留业务运行与本地结果，收尾后明确报错。
观测错误不覆盖原业务异常或取消。`telemetry/experiments/<invocation_id>.json` 保存记录回执，不参与恢复，
不是补传队列。Case、oracle、gate 和本地报告的所有权不变；平台默认平均值不替代计划题目分母。
任务基线记录任务阶段观察；后置独立验收和人工审查仍属于原本地报告。见[ADR-0050](../adr/0050-record-evals-as-phoenix-experiments.md)。

Phoenix 服务在独立uv工具环境运行，服务端版本固定20.16.0；普通产品不自动启动，测评CLI使用
[专用生命周期](../../src/abaqus_sim_agent/evaluation/phoenix_service.py)直接启动并等待本次宿主的就绪信号。
宿主调用官方 `launch_app(use_temp_dir=False)`，使用原生 `~/.phoenix/phoenix.db`；不继承Phoenix工作目录、数据库地址
及端口覆盖，不创建项目专属数据库配置。只启用本机HTTP，固定版本的额外gRPC接收器在隔离宿主内关闭。
已有16007验证库不搬迁。关闭时先收尾追踪，再通过stdin结束请求调用官方close_app；超时才终止本次进程树。
新进程继续读取同一数据库，但不接管已有服务。详见[ADR-0051](../adr/0051-manage-phoenix-lifecycle-for-evaluation.md)。
详细内容可含消息、脚本和
私有资料；已知模型凭据过滤不保证任意正文匿名化。不采集 HTTP body/header 或二进制附件，不配置 Console
exporter；原始 stdout/stderr、state 和 oracle 继续由原路径保存。工作台删除不清空 Phoenix 副本。
进程退出在业务收尾后尝试有界 SDK shutdown；普通产品追踪或已有评分注释发送失败仍只警告。
显式评测的新实验记录按上述规则检查和报错；观测故障不改变业务 outcome、不重跑业务，
也没有离线补传队列。数据与所有权决定见 [ADR-0044](../adr/0044-local-phoenix-observability.md)。

### 执行与会话

- 一个 state 目录只允许一个持有 OS 非阻塞文件锁的活动工作段。锁 handle 不传给子进程；锁文件存在不表示持锁。
- 待答释放执行锁，答复写入和继续执行重新取锁。锁按 state 隔离，不保证不同 run 共用 workspace 的事务隔离。
- Web 实例另持有 management root 单实例锁，并只允许一个主动模型工作段；SSE 断开不取消工作段。
- 网页对当前会话的状态请求串行执行；请求期间收到的刷新通知合并为一次后续读取，切换会话后忽略旧请求结果。
- 同一 Web 实例中的重叠工程目录若有另一活动会话、未完成 Solver 或待核查执行，则拒绝相关新建/执行；
  这不提供跨 CLI、其他 Web 根或外部编辑器的工作区锁。
- 普通 Web 消息先保存 Thread，再以 `continuation_id` 调用同一 CodingRun；消息与 terminal execution link 间的
  短双写窗口通过显式 continue 补链，不重新调用模型。
- Web Solver 由 `ToolExecution` 在专用事件循环运行；submit 立即把 execution ID 返回仍活动的 Agent，wait
  再消费终态结果并写入 execution journal。宿主重启把未完成记录转为 `needs_reconcile`。
- atomic JSON、append journal 与 Harness snapshot 分别保存宿主状态和步骤证据；它们不是一个数据库事务。
- 恢复要求当前 workspace 指纹匹配持久边界。损坏 tool-effect ledger 失败关闭，不通过 event log 猜造副作用状态。
- 未决 Abaqus effect 先进入 `needs_reconcile`。当前不会查询原 PID、重新附着、自动重提或通过人工对账解锁。
- 宿主还活着时，timeout/cancellation 会终止准确 PID 的进程树并保存能取得的日志；宿主/机器崩溃后的
  外部进程管理没有同等保证，见 [Job 生命周期](flows/job-lifecycle.md)。
- Web 主停止通过当前 operation ID 和 `AgentRun.cancel()` 结束模型或前台工具，等待工具收尾后才投影为
  stopped。后台 Solver 保留 ToolExecution 生命周期和独立取消入口；运行期间阻止新 Agent 工作段，终态
  workspace snapshot 精确匹配后才能继续。删除会话才组合两条取消路径。

## 依赖和装配

静态类型源准备由 `coding.static_preflight.prepare_static_preflight_source` 统一承担。CLI 准备新运行时调用它；
共享装配根据保存的 launch 准备缺省的静态源，并核对 request 中的模式及内容绑定，供 Web 和恢复入口复用。


`coding` 是产品宿主，`web` 是本地传输、Thread 与展示管理层，`execution` 是 vendor 副作用边界，
`verification` 是可信验收，`api_catalog` 和 `retrieval` 提供知识，`evaluation` 定义外层证据系统。
这是活动组件图，不额外套统一技术分层；`coding` 不导入 FastAPI 或前端协议。

`coding.verification_port` 由消费方拥有，具体 verifier 从外侧实现。`coding.service` 不直接依赖
物理 verifier；CLI/Web/evaluator 分别装配对应 scope。部分可选检索服务仍复用 evaluation 内实现，
清理或拆分必须先核对调用关系，不能把期望的分层当现状。

实现入口：[workspace](../../src/abaqus_sim_agent/coding/workspace.py)、
[service](../../src/abaqus_sim_agent/coding/service.py)、[verifier port](../../src/abaqus_sim_agent/coding/verification_port.py)、
[run lock](../../src/abaqus_sim_agent/coding/run_lock.py)、[runner](../../src/abaqus_sim_agent/execution/abaqus2024.py)、
[Web controller](../../src/abaqus_sim_agent/web/controller.py)、[Web storage](../../src/abaqus_sim_agent/web/storage.py)。

## 已实施 Web 与未采用拓展

[ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)已选择 FastAPI + assistant-ui 的本地工作台。
API、页面、Thread continuation、模型预览和后台 Solver submit/wait 已完成离线工程实现；两条真实 Web 主线仍在
[PLAN-0010](../plans/todo/0010-local-web-workbench.md)，不能由工程接线外推为真实建模或物理收益。
数据库 Job ledger、durable scheduler、多 worker 和远程执行没有因该 Web 决定自动获选。

对话 PNG 预览复用工作区文件读取接口，按历史工具记录中的相对路径或回复中的 Markdown 图片引用显示。
它读取的是工作区当前内容，不以当前 continuation 的 `fresh` 标记决定历史图片能否显示，也不新增图片副本、
附件索引或图片工具。支持 PNG/JPEG/WebP，沿用工作区路径、链接拒绝和 2 MiB 预览边界；图片响应禁止缓存，
后续重新加载会读取当前文件。原图覆盖后显示新内容，原图移动或删除后显示不可用。已有 artifact 接口的
hash 校验、执行验收和 OBJ 预览合同保持不变。源文件存在且状态格式兼容时，重启可从保存的对话记录恢复引用。

架构依据：[ADR-0017](../adr/0017-package-by-component-coding-run.md)、
[ADR-0019](../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md)、
[ADR-0020](../adr/0020-resume-coding-runs-with-harness-step-persistence.md)、
[ADR-0021](../adr/0021-use-amendments-and-task-revisions-for-continuation.md)、
[ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md)、
[ADR-0026](../adr/0026-scope-requests-and-text-responses.md)、
[ADR-0027](../adr/0027-decouple-web-thread-messages-from-task-revisions.md)。
