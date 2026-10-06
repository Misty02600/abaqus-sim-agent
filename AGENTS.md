# Project Instructions

## 项目目标

- 本项目服务于秋招准备，主目标是 AI Agent / LLM 应用工程，其次是 Python AI 后端 / AI 平台；Abaqus 是 AI for Science / CAE 的领域落点。优先做扎实功能，理解框架行为、代码路径、失败边界和工程取舍。
- 不以寻找创新点、堆叠技术栈或增强包装说服力为目标。其他求职项目用于理解可迁移的工程问题，不拼接无关业务。
- 能力声明必须区分实现、离线测试、真实模型观察、持证仿真和物理验收。依赖、设计稿、Mock、单次实验不能写成已完成的通用产品能力；SQL、API 等只有进入真实调用路径并验证后才算项目能力。

## 开始工作前

- 先检查当前分支、工作树改动和作用域指令。按当前分支的源码与文档判断现状，不把其他工作树或旧分支的状态当作当前事实。
- 先读 [架构总览](docs/architecture/overview.md)；涉及架构、状态所有权、权限、兼容或“之前决定过”的问题，先查 [ADR 索引](docs/adr/README.md)及相关已采纳决定，再查实现和测试。
- 配置设计先查 [配置输入与恢复记录](docs/architecture/system-framework.md#配置输入运行合同与恢复记录)及 [ADR-0045](docs/adr/0045-use-explicit-run-options-without-preference-files.md)：采用程序默认值、本次显式输入与必要会话记录，不读取用户/项目偏好文件；区分宿主设置、运行输入和恢复状态，不重新扩建自动偏好来源。Catalog 等固定资源由程序管理。
- 模型输出上限是用户于 2026-10-06 明确允许的单项例外：按提供商/模型保存到应用管理的 `model-output-settings.json`，只供新会话解析，已有会话沿用 launch；不扩展为通用偏好系统。逐字段语义见配置合同。
- 模型接入与选用先查 [ADR-0046](docs/adr/0046-use-native-providers-and-available-model-selection.md)：复用原生 provider，OpenCode 专用适配保持局部；CLI/Web 共用可用模型列表，新会话必须本次明确选择模型，不自动取默认或首项，恢复不自动换模型。普通输入不暴露窗口或通用 API 地址，不另建型号黑名单；支持范围与验证边界以当前文档和实现为准。
- 宿主凭据见 [ADR-0047](docs/adr/0047-persist-host-credentials-and-bundle-model-catalog.md)：CLI/Web 共用程序管理的 auth.json，环境密钥不自动保存。模型目录按 [ADR-0048](docs/adr/0048-fetch-models-dev-with-local-cache.md) 直接获取 models.dev 并缓存，不再生成打包、提取 Pi 源码或使用 SDK 名称表兜底；目录不等于账号授权清单。
- 普通产品按 [ADR-0049](docs/adr/0049-select-product-models-only-from-available-catalog.md) 必须显式从可选目录选择；未选模型不能发送，登录不自动选择，已连接但无候选的 provider 仍展示空列表；不开放目录外手填型号，CLI/API 新建和主动换型号同样检查候选。SDK 按名称推导不是远端校验；已有会话恢复沿用保存合同，不重新筛选。
- 新增或修改配置时同步 [配置逐字段合同](docs/architecture/configuration-reference.md)，逐项确认来源、缺省、覆盖、校验、持久化与恢复修改规则；不能用分类说明代替字段决定。
- 普通用户选择能力，程序管理固定内部资源；已确认的 Skill/静态来源路径及重复模式入口收敛见逐字段目标表。不要仅因上游支持扩展就新增本项目没有需求的配置入口，已决定与已实施必须分别说明。
- 当前路线由 ADR-0008 及后续决定约束：一个 Pydantic AI Harness Coder。旧 LangGraph、多 Agent、DeckDraft 和持久 Job Service 路线已被替代。增加第二套运行时、角色或控制面需要真实需求与新的取舍，不能从旧 ADR 自动恢复。

## 产品与执行边界

- 唯一产品 Agent 是 `abaqus_2024_coder`，使用 Pydantic AI 原生 tool loop、typed output、依赖注入与 Harness Coder/Skills/StepPersistence。Runner、verifier、evaluator、查询工具和宿主 service 均不是 Agent。
- Agent 在单一工作区编写普通动态 Abaqus Python；不强制 DeckDraft/ChangeSet 或确定性 renderer。包内领域 Skill 是产品提示与知识资源，和本文件的仓库维护指令分开。
- `CodingRunService` 拥有会话、limits、runtime policy、usage、恢复与最终 outcome。新普通 CLI/Web 使用 `ask_user(question)`；宿主保存 `waiting_for_user` 后释放运行锁，用户等待不消耗 active wall。普通产品的模型请求、提问、低成本工具和 active wall 均 unlimited；固定评测可显式有限。项目仍处于开发期，不承诺 pre-release 产品 run/state 的向后兼容或迁移；不兼容 state 必须显式拒绝，不能猜测升级。保留的trial、seal和报告不回填新协议；旧Gold独立驱动已退出活动入口，冻结报告和输入仍保留。
- 缺少会改变物理问题的必要输入时先澄清，不能根据常见值、模板或检索结果补造参数。新会话可以先读工作区事实；Abaqus API、文件名等实现问题由 Agent 处理。
- `ready_for_verification` 只申请验收，必须声明非空、无重复的工作区相对交付物 manifest。普通 CLI 的 `execution_evidence` 验证 fresh 交付物、fresh Python 的 3.10 语法和每种已用 runtime 的最后一次 stage；不证明完整自然语言语义或物理正确。固定 evaluator 才注入模型不可见的 `task_oracle`。
- 新普通产品会话是标准 Harness Coder：保留工作区 Shell，但按配置路径和 `abaqus*` / `abq*` 版本化名称拒绝直接启动 vendor launcher；Abaqus 执行使用 `run_cae_script`、`solver_job(action=submit|wait)`、`run_abaqus_python`。冻结 evaluator 继续使用各自保存的工具合同；`run_trusted_abaqus_python` 永不注册给模型。
- 建模脚本应只生成模型与 INP，求解经独立 Solver 工具，提取经 Abaqus Python/odbAccess。宿主在 CAE runtime 前确定性拒绝 AST 中直接出现的 `.submit(...)` 和 `.waitForCompletion(...)`；`getattr`、`exec` 和运行时重绑定等动态绕行不在该最小策略覆盖范围内，不得宣传为 Python 沙箱。
- 两个 Python runtime 执行前始终检查 Python 3.10 语法，文件编辑可保留半成品。BasedPyright 仅有 off/on、默认 off；诊断与真实运行结果同时返回，不做原样重试放行或 API 案例阻断。检查器不可用不得伪装成 clean，已有权限和执行证据边界继续有效。
- Coder 文件工具严格支持 UTF-8/CP936：读取和搜索报告采用编码，编辑或覆盖保持已有编码，新文件使用 UTF-8，hash 绑定原始 bytes；无法无损解码或写回时修改前拒绝。Web 文本预览复用同一策略，runner raw log 合同不变。
- 模型脚本和输入位于已解析的 ASCII 工作区，state/evidence 位于工作区外；校验路径归属、后缀、Job 名及可选逐工具文件 allowlist。runtime permission/allowance、准确进程树终止、raw stdout/stderr bytes 和 artifact freshness 必须保留。这些边界不是 OS 沙箱或不可信租户隔离。
- runtime permission/allowance 只在模型实际调用相应工具时返回拒绝；不能因 verifier 拒绝就提前终止仍可进行的普通文件修复。
- 恢复必须保留累计 usage/allowance、原始 freshness 基线、execution provenance、协议、context policy 和知识/Skill 绑定。同一 state 使用 OS 独占锁；未决 Abaqus tool effect 停在 `needs_reconcile`，禁止盲目重跑。不得把文件后端续跑称为外部 Job exactly-once、PID 重新附着或完整 durable workflow。
- Task revision 与 operational amendment 分开：新任务建立新验收基线，旧 outcome 归档；runtime policy 或 provider 修订不能清零已消耗用量。损坏 ledger 不猜测修复，不用 event log 推断缺失的副作用记录。
- Web 会话在无活动/待核查执行时可用 operational amendment 修改 Abaqus/Solver 权限；待答问题保持，修改不自动继续也不取消已有执行，只影响未来提交。有限次数是累计总上限，usage/allowance 不清零。
- 新普通会话使用 launch v10，绑定按实际模型窗口选择的 context policy、filesystem Skill digest、UTF-8/CP936 文件策略与必选 API Catalog：当前请求可以交付 `ResponseDecision` 文字答复，文件或执行请求仍须走非空 manifest 与宿主 verifier；当前 revision 已有执行或终态工作区变化时不能用文字答复绕过验收。
- Harness Planning 适配只为解释冻结历史轨迹保留；新产品和新评测不得启用，也不新增重新开启入口。历史 manifest 中的 `planning_enabled=true` 保留原值。
- 本地 Web 工作台复用 `coding/application.py`、`composition.py` 和 `CodingRunService`。命令使用 REST，SSE 只通知重取；Web registry、Thread、invocation 和 `ToolExecution` 是管理记录，不覆盖 CodingRun 的 request、limits/runtime policy、runtime evidence、verification 或 outcome。
- Web CLI 固定回环监听，服务 started 后默认打开普通本机 URL，`--no-open` 可关闭；页面通过 GET session 无感建立 HttpOnly cookie 与 CSRF，不使用终端 fragment token。非回环部署仍未开放；见 ADR-0037。
- 新 Web 会话必须先显式选择已有本机 ASCII 工作区，不自动创建托管空目录；创建后固定绑定，文件原地读写。所选目录与管理根互不包含，state/evidence 仍由宿主保存；目录接线与边界见 ADR-0034、ADR-0041。历史 managed 登记按原路径继续读取。
- Web 删除会话先完成本宿主受控执行的收尾，再永久清理会话记录；托管或选定的工程文件始终保留。最小删除回执仅用于幂等和失败重试，不是回收站；见 ADR-0035。
- Web 主停止操作按活动 operation ID 中断当前 Agent 工作段，并通过 `AgentRun.cancel()` 传播到模型和前台工具；成功收尾后保存 `interrupted/stopped`，保留文件、history 与累计 usage。已后台化的 Solver 不随主停止取消，继续由独立 ToolExecution 观察或取消；其运行期间不启动新 Agent 工作段，终态后只按宿主记录的精确 workspace snapshot 接回。删除会话仍执行完整收尾。见 ADR-0036。
- Web 普通追加消息先持久化到 Thread，再以 `continuation_id` 恢复同一 CodingRun history；它不推进 task revision，但每段单独捕获 workspace/execution baseline，旧产物和旧执行不能冒充 fresh evidence。显式任务合同变化仍使用 amendment/task revision。
- 新普通会话的 `solver_job(action=submit)` 由宿主 `ToolExecution` 或 CLI 后台 task 启动并立即返回 execution ID；模型可完成独立工作，再用 `action=wait` 普通等待结果。pending Solver 存在时不能结束当前 run；当前宿主可观察和取消仍受控的进程树，重启中的活动 Solver 转 `needs_reconcile`，不承诺跨进程重附着或 exactly-once。

## 知识与评测

- 新普通产品自动绑定随应用安装的 Catalog，不要求用户选择目录；默认 `automatic_verified_only` 且不暴露主动查询工具；`full`、`full_with_discovery`、`automatic_exact_signature` 由显式 profile 选择。Concept、diagnostic advisory、bounded document policy、ACI、typed probe 和纯编辑限制仍按显式 profile 启用；旧冻结评测的 `off` 条件不回填。当前入口与组合见 [知识接入](docs/architecture/knowledge-access.md)。
- `full_with_discovery` 已接入产品：discovery 返回候选名称，必须继续 exact lookup 才有卡片证据；候选、静态声明、runtime note 和实际运行成功不能互相替代。检索不能补造缺失物理输入。
- 2026 官方文档、2024 abqpy 静态声明与 Abaqus 2024 runtime evidence 分别标来源；不能把文档命中视为版本兼容或任务收益。不自动将 probe、失败修复或单题解法写入全局 Skill、Catalog 或跨 run Memory。
- 公开力学、verified-seed change、API repair、检索评测和开放产品任务分开。领域仿真按物理Case计数，输入变体不增加题目权重。变更任务先重新验证 source 合同、验收与 hash，再缓存种子 bytes；共享执行证据不能代替逐 Case oracle。suite 按每个 Case 的 gate 验收，不能用总通过数掩盖失败 Case。
- 旧 request、Prompt/Skill、profile、预算、source/lock/index 绑定、trial、seal 和报告保留原值；新配置或新合同使用新批次，不能回填旧成绩。失败、Provider 中断、人工干预和 post-hoc replay 必须保留并区分。
- 小样本通过不代表稳定成功率；接线通过、检索命中、模型采纳和物理收益分别留证。运行记录与允许声明见 [验证证据入口](docs/architecture/verification-evidence.md)，具体数值只在对应冻结报告中维护。

## 文档沉淀

- 文档工作使用 repo-docs skill。AGENTS.md 只放持续约束和查阅入口；architecture 记录当前事实、公开语义、状态所有权与稳定流程；ADR 保存正式架构取舍；benchmark 保存实验合同和结果；research 保存可复用调研；scratch 保存未成形探索。
- 完成改动前核对受影响的当前文档：公开入口、工具权限、协议/profile、状态所有权、恢复和支持边界变化时，同步对应 architecture/README。历史报告不充当当前状态说明，不在多份文档追加同一实验流水账。
- 新 ADR 必须涉及系统边界、稳定公开契约、状态/数据所有权、安全、部署或跨组件不变量，存在真实替代方向和实质代价，并持续约束后续维护。普通 bug、默认值、预算、Prompt/Schema 小修和单次评测不自动建 ADR；理由就近进入实现、测试、当前文档或评测记录。
- 已采纳/未采纳 ADR 不重写当时背景、决定和后果。方向改变新建 ADR，明确接受依据和双向替代关系；实现演进更新 architecture。经文档治理归档时保持编号、原状态和正文语义，只修路径、链接与关系，并维护 history 索引。
- 不因复杂、多步骤或跨会话创建 `docs/plans/`。只有用户明确要求计划、TODO、稍后处理或维护现有 plan 时才操作其生命周期；普通实现不自动接管、关闭相关 plan，也不事后补建 done 记录。
- 正式文档不依赖被忽略的 scratch、个人 notes 或本机运行文件才能理解结论。链接应指向真实文件；`.code-notes/`、`.tours/personal/` 只承载个人源码理解，不在 docs 铺逐文件导航。
- 保留已有 Git 忽略策略；文档优化不自动授权提交、推送、改写历史或改变实验文件。删除重复说明前确认其有效约束和证据有明确落点。

## 开发与复用

- 宿主 Python 3.12 使用 `uv`，虚拟环境放在本项目 `.venv`；Abaqus vendor Python 3.10 与宿主分开。精确依赖以 pyproject.toml/uv.lock 为准，运行参数以有效 launch 配置为准。
- 按改动运行相关离线测试与静态检查；持证测试/真实模型评测单独说明环境、批次与证据。文档修改不能被描述为重新验证了模型或 Abaqus。
- 本项目保留独立 Git、包结构和维护历史。可有来源地借鉴、改编上游，避免低价值重复劳动，也不整仓照搬。
- 直接复制或实质改编代码、提示词、资产时记录上游、基线提交、原文件和修改，并履行许可证义务；案例语料单独确认来源和再分发权。
- 同级 `references/` 是默认只读的独立研究快照，不是产品源码或运行依赖；实验修改须保留基线可追溯性。清理默认关闭或位于 evaluation 的代码前先查真实依赖，不能仅凭目录名删除仍被产品复用的组件。
