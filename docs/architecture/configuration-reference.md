# 配置逐字段合同

本文逐项说明当前配置的归属、来源、默认值、持久化及修改规则。适用当前工作树的普通产品入口，
不是新增配置文件，也不是待实现的配置清单。总体流程见[系统框架](system-framework.md#配置输入运行合同与恢复记录)。

## 范围与阅读方式

- 覆盖普通 CLI 的 44 个选项、Web 启动命令的 19 个选项，共 47 个不同选项名，不计 `--help`。
- 对 15 个公共偏好字段、8 个 `ServerConfig` 字段、运行控制和显式 profile 输入分别定义。
- 对 request/launch 的保存位置逐字段映射；重复序列化不计为另一项用户配置。
- 会话 ID、操作命令、实验 spec 和宿主生成记录单独列出，避免将它们混为应用偏好。
- 第三方 SDK 的全部环境变量、每个评测 Case 的物理数据、私有知识包资源 manifest、UI 临时展开状态和
  算法实现常量不属于本表的应用配置表面；相关资源合同继续由所属组件维护。

普通使用只需看“公共偏好”。开发维护再按字段查其余章节。表内“固定”表示本会话的普通恢复不能重新指定；
“可修订”表示通过已有 typed amendment 校验后修改有效值，原始文件不覆盖，usage 不清零。

## 公共来源与合并规则

两级文件使用同一个 `RunPreferences`：用户级 `settings.json` 位于 `platformdirs.user_config_path`，Windows
为 `%LOCALAPPDATA%/abaqus-sim-agent/settings.json`；项目级为所选 workspace 的 `.abaqus-sim-agent/settings.json`。
不向父目录查找项目文件。以下 15 个字段在两级均允许；环境变量不覆盖这些字段。

- CLI：显式参数 > 项目文件 > 用户文件 > 内置默认。Web：创建请求的 `preferences` > 服务启动 `overrides` >
  所选 workspace 项目文件 > 用户文件 > 内置默认。Web 不以服务 cwd 选择项目配置。
- 逐字段覆盖；字段省略表示继承，显式 `null` 拒绝。没有文件表示无偏好；空文件、格式错误、未知字段、
  错误类型和不兼容的最终组合报错。低优先级文件损坏时也报错，不因高优先级恰好覆盖它而跳过校验。
- 列表整体替换，不拼接。知识包 `[]` 表示明确不启用任何包。
- 文件内相对路径基于该文件所在目录；CLI 相对路径基于启动 cwd；Web 创建请求相对路径基于所选 workspace。
  路径在所属输入适配器中解析，不能把同一个相对字符串换个来源后按原目录解释。
- 解析只读文件，不创建或保存用户偏好，不将 CLI/Web 临时值回写。持久修改偏好目前由用户显式编辑文件完成。
- 新建后将实际使用值和资源绑定写入会话；恢复不调用偏好读取器。允许的修改通过 amendment 进入有效合同。
- **项目文件仍在 workspace snapshot 内**：外部修改项目配置可能触发 `workspace changed`，即使恢复不会重新
  应用其中的默认值。维持原有工作区一致性边界，不为配置文件豁免 hash，也不把“不重读偏好”宣传成“任意修改后均可恢复”。

实现：[preferences.py](../../src/abaqus_sim_agent/coding/preferences.py)、
[共享 CLI 参数](../../src/abaqus_sim_agent/coding/preference_arguments.py)。

## 公共偏好：逐字段

本节来源均为上述公共来源。Web HTTP 使用同名 `preferences` 子字段；页面当前直接提供模型和知识包选择，
其他字段可通过同一 JSON 合同或配置文件传入，不能据此称页面已有全部编辑控件。

| 字段 | CLI 参数 | 无任何来源时的有效默认 | 类型、约束与用途 | 会话保存与修改 |
|---|---|---|---|---|
| `model_provider` | `--model-provider` | `deepseek` | 框架原生提供商及 OpenCode Go/Zen；见 `/api/v1/model-providers`，暂不包含 xAI；不按 SDK/凭据过滤 | `launch.model_provider`；CLI 可修订，须兼容已保存推理策略并有对应凭据 |
| `model_name` | `--model` | `deepseek-v4-flash` | 非空、无首尾空白；OpenCode 适配仍限 `deepseek-`。其他提供商要求显式模型名，不借用 DeepSeek 默认名；候选不等于远端可用性 | `launch.model_name`；CLI 可修订，不自动更新偏好 |
| `http_retries` | `--http-retries` | 通常 `4`；Bedrock、HF、Mistral、TypeSafe 为 SDK 默认 | 显式整数 ≥0；OpenAI 兼容、Anthropic、Groq、Cohere 配置 SDK，Google 使用 attempts=值+1；不支持次数配置的 provider 拒绝显式指定，运行快照以 null 表示 SDK 默认；不等于 Agent 请求次数 | `launch.http_retries`；CLI 可修订，`0` 必须作为显式值保留 |
| `http_timeout_seconds` | `--http-timeout-seconds` | `300.0` | 有限正数；传给模型/SDK 的请求超时，不等于 active wall 或 Solver 超时 | `launch.http_timeout_seconds`；固定，恢复显式指定拒绝 |
| `reasoning_policy` | `--reasoning-policy` | 最终 Go + V4-Flash 为 `enabled_high`，其他为 `provider_default` | 仅允许这两个值；`enabled_high` 与最终 provider/model 不兼容时拒绝；显式值优先于自动选择 | `launch.reasoning_policy`；固定。修订模型时不重新按当前偏好推导它 |
| `context_window` | `--context-window` | 框架 profile / genai-prices；OpenCode 的指定 V4 与官方 deepseek-flash 使用有依据的 1M | 显式整数 ≥1024；未知时要求填写；不扩容远端模型 | `launch.context_window`；普通恢复沿用。CLI 可显式修订；改模型时将重新解析的窗口写入 amendment，禁止仅改模型后继续沿用旧窗口 |
| `model_base_url` | `--model-base-url` | 提供商原生默认地址 / 宿主环境 | 无账号密码、query 或 fragment 的 HTTP(S) 地址；实际协议仍由 provider 决定 | launch；固定，恢复仅可传相同地址作为使用环境凭据的显式授权。已有自定义地址的会话切换 provider 要求新建 |
| `launcher_path` | `--launcher` | `D:\SIMULIA\Commands\abq2024.bat` | 解析为绝对路径；为 runner 选择 vendor launcher，不由文件名推断运行成功或许可状态 | `request.runtime.launcher_path`；固定，CLI/Web 相同来源规则 |
| `skill_library` | `--skills-dir` | 包内 Skill 库 | 路径必须能准备所需 Skill；绑定 `abaqus-2024-coding` 等实际能力定义的 digest | `launch.skill_library` + `capability_definitions`；固定，恢复核对定义 |
| `api_catalog_dir` | `--api-catalog-dir` | 无；新产品会话必需 | Catalog artifact 位于 workspace 外；打开后校验 manifest 并生成内容绑定 | 路径在 launch，内容身份在 `request.api_catalog`；固定，恢复按本会话路径加载 |
| `api_catalog_mode` | `--api-catalog-mode` | `automatic_verified_only` | `full` / `full_with_discovery` / `automatic_verified_only` / `automatic_exact_signature`；普通产品不接受 `off` | `launch.api_catalog_mode`；固定，不由是否传目录推断成 `full` |
| `static_preflight_mode` | `--static-preflight-mode` | `off` | `off/on`；只控制可选 BasedPyright，两个 Python runtime 的 3.10 语法检查始终存在 | `launch.static_preflight_mode`；固定，恢复显式 on/off 都拒绝 |
| `abqpy_static_wheel` | `--abqpy-static-wheel` | 无 | 静态检查 on 时必需，来源位于 workspace 外；off 时该默认路径闲置，不读取也不绑定 | on 时路径进 launch、版本/hash 进 `request.static_preflight`；固定 |
| `static_preflight_cache_dir` | `--static-preflight-cache-dir` | 无 | 静态检查 on 时必需，缓存位于 workspace 外；off 时闲置；准备器验证不可变解包来源 | 活跃路径进 launch；固定，不把临时命令覆盖回写偏好 |
| `knowledge_pack_ids` | `--knowledge-pack`（可重复）、`--no-knowledge-packs` | `()`，不启用私有包 | 最多32个、唯一、匹配 `[a-z0-9][a-z0-9-]{0,63}`；整体替换，空列表清空 | launch 保存实际 `domain_knowledge` 快照绑定；固定，不在恢复时重扫新选择 |

保存来源路径与保存内容指纹各有用途，不能删除其中一项来“去重”。源码中 `ResolvedRunPreferences` 是本次
解析结果：`execution`、`runtime` 引用已有 typed 模型，其余字段传给装配；它本身不增加第三份配置文件。

## 运行权限与限制：逐字段

本节不接受全局/项目偏好文件输入。普通入口的默认来自既有控制模型；Web 创建请求可显式覆盖权限。
允许次数是累计上限，实际使用量另存。`deny`、`allow`、`allow_with_limit` 分别表示禁止、不限次数、有限次数；
有限 `limit` 必须 ≥1。Abaqus 是父权限，Solver 还受自身权限限制；父权限拒绝时子权限不能越过它。

| 字段 | 当前入口 | 默认、约束及含义 | 持久化 / 会话内修改 |
|---|---|---|---|
| `runtime_policy.abaqus` | CLI `--abaqus-permission` / `--abaqus-limit`；Web `abaqus_permission` 与权限面板；宿主注入 | `allow`；单独传 limit 选择 `allow_with_limit`；limit 与 deny/allow 同传拒绝 | request；CLI/Web 可显式修订，保留累计 abaqus usage |
| `runtime_policy.solver` | CLI `--solver-permission` / `--solver-limit`；Web `solver_permission` 与权限面板；宿主注入 | `deny`；有限值规则同上；每次 Solver 同时受父权限检查 | request；CLI/Web 可显式修订，保留累计 solver usage |
| `runtime_policy.cae_timeout` | CLI `--cae-timeout-seconds`；typed 宿主输入 | `unlimited`；CLI 数值为有限正秒数 | request；CLI 可修订。CLI 暂无将有限值改回 unlimited 的参数，typed patch 可表达 |
| `runtime_policy.abaqus_python_timeout` | CLI `--abaqus-python-timeout-seconds`；typed 宿主输入 | `unlimited`；有限正秒数 | request；修订方式同 CAE timeout |
| `runtime_policy.solver_timeout` | CLI `--solver-timeout-seconds`；typed 宿主输入 | `unlimited`；有限正秒数；与提交后的后台观察状态分开 | request；修改只影响未来提交，不取消既有 Solver |
| `limits.model_requests` | 程序化入口、固定评测；普通 CLI 无计数开关 | `unlimited`；有限值为整数 ≥1，累计模型请求门限 | request；当前 operational patch 不提供此字段 |
| `limits.clarifications` | 程序化入口、固定评测 | `unlimited`；有限值为整数 ≥0 | request；当前无修改 patch |
| `limits.active_wall` | 程序化入口、固定评测 | `unlimited`；有限值为正秒数，用户/外部执行等待按 active-wall 合同扣除 | request；当前无修改 patch，累计值不因重启清零 |
| `evaluation_tool_limits.api_lookup_calls` | 固定评测/宿主 typed 输入 | 对象缺省为 None；字段 None 表示无上限，整数 ≥0 为有限 | request 中可选评测对象；普通产品不主动设置 |
| `evaluation_tool_limits.concept_lookup_calls` | 同上 | None 或整数 ≥0 | 同上 |
| `evaluation_tool_limits.diagnostic_retrieval_calls` | 同上 | None 或整数 ≥0 | 同上 |
| `evaluation_tool_limits.static_preflight_calls` | 同上 | None 或整数 ≥0 | 同上 |

`RuntimeExecutionPolicyPatch` 只含上面五个 runtime-policy 字段；`CodingRunConfigPatch` 只含 runtime policy、
model provider、model name、HTTP retries、context_window 和宿主生成的 provider_environment。二者省略表示不改，显式 null 拒绝，空 patch 拒绝。不要根据旧 ADR
当时的“预算均可修改”描述，推断当前 limits 已有修改入口。

实现：[contracts.py](../../src/abaqus_sim_agent/coding/contracts.py)、
[amendments.py](../../src/abaqus_sim_agent/coding/amendments.py)。

## 宿主与凭据：逐字段

`ServerConfig` 的8个字段如下。它是程序化宿主输入，不能把 dataclass 类型注解当成所有边界都已做运行时校验；
各值仍由目录管理、HTTP server 或相应 typed 控制模型检查。

| 字段 | 默认 / 入口 | 使用者与处理决定 | 进会话吗 |
|---|---|---|---|
| `root` | Web `--root` 必需 | 管理根，目录注册器检查；按固定根保存 states/registry，与选定工程互不包含 | 根本身是宿主配置；派生的该 run `state_dir` 保存于 request |
| `knowledge_packs_root` | None：用户数据目录 `knowledge/packs`；程序化宿主可覆盖 | 发现私有包的本机目录，源内容由包准备器验证 | 不保存发现根；实际采用的包快照由会话保存 |
| `stream_model` | True；程序化宿主输入 | 控制当前宿主是否流式展示模型请求；CLI 当前调用默认非流式 | 不作为旧会话的恢复配置 |
| `overrides` | 空 `RunPreferences`；Web 启动公共参数 | 当前服务为新会话提供的显式覆盖；比创建请求优先级低，比文件高 | 容器不落盘；最终采用的字段按公共表保存 |
| `limits` | `CodingRunLimits()` | 新会话限制的 typed 输入 | 创建时进 request，恢复使用保存值 |
| `runtime_policy` | `RuntimeExecutionPolicy()` | 新会话权限/超时基础值，再叠加创建请求显式权限 | 创建时进 request，允许字段以后走 amendment |
| `evaluation_tool_limits` | None | 为评测/测试注入低成本调用门限，不以 server budget 再覆盖一遍 | 显式设置时进 request |
| `context_warning_threshold_tokens` | 旧会话 900000；None 关闭旧会话提醒 | 新会话提醒阈值取本字段与 launch 窗口 90% 的较小值；None 禁用，旧会话使用本字段 | 字段本身不进 launch；新策略从会话窗口推导，不伪装成 provider 硬限制 |

| 其他字段 / 变量 | 默认、来源与用途 | 持久化与失败边界 |
|---|---|---|
| Web `port` / `--port` | 8765，整数参数；宿主绑定 `127.0.0.1` | 不进会话；当前由 server/socket 检查绑定可用性，不宣称 CLI 已预检整个端口范围 |
| Web `no_open` / `--no-open` | False，服务 started 后打开普通本机 URL | 不进会话；每次启动独立选择 |
| `DEEPSEEK_API_KEY` | 无；当前进程环境 | 官方网关凭据，捕获后剥离继承环境；不写入偏好或 request/launch |
| `OPENCODE_API_KEY` | 无；当前进程环境 | Go/Zen 凭据；Web 按会话 provider 选取，缺失拒绝，不回退到别的模型 |

模型 API Key 的环境变量名由 [provider 目录](../../src/abaqus_sim_agent/coding/providers.py)集中维护。宿主捕获并移除这些密钥后按所选 provider 注入。AWS 等本机云凭据继续由原生 SDK 读取；工作区 Shell 与 vendor 子进程额外剥离 API key、token、AWS access/secret key 和 Azure client secret 环境变量。这不是 OS 沙箱，不能阻止程序读取宿主可访问的凭据文件。

Web `/model-connections` 接收 provider 与 SecretStr API key，受同一回环 cookie/CSRF 保护，只修改内存凭据，不写偏好、launch 或浏览器存储。自定义地址的凭据按 provider + 地址单独绑定，不继承官方接口的密钥；重新启动服务后需重新设置。`/model-logins` 复用框架 OpenAI Codex PKCE / GitHub Copilot device flow，后者需用户自己的 OAuth Client ID。OAuth 按默认服务接口登录，自定义接口使用独立 API Key。登录任务可取消，宿主退出时取消未完成任务；令牌及刷新后的令牌保留在内存。远端登录/API 权限尚未真实验证，不能把设置凭据当成连接验收。

CLI 使用来自偏好的自定义地址时，交互模式单独提示该地址的密钥；非交互模式要求显式传 `--model-base-url` 后才使用环境凭据。恢复允许传与已保存值完全相同的地址，不改写会话。

固定回环地址、进程收尾等待、文件预览大小等实现常量不因出现在源码中就自动开放成用户配置。
实现：[Web CLI](../../src/abaqus_sim_agent/web/cli.py)、[controller](../../src/abaqus_sim_agent/web/controller.py)、

## 任务输入与操作命令：逐项

均不进入全局/项目偏好。输入来源是本次 CLI/Web 命令；同名文件内容不能被当作可任意覆盖整套配置的 JSON。

| 字段 / 命令 | 默认与校验 | 保存位置及后续行为 |
|---|---|---|
| `goal` / `--goal` | 新建必需的非空任务文本；CLI 去首尾空白；Web 最长20000字符 | 初始 request；显式修改走 task revision |
| `request_file` / `--request-file` | 无；UTF-8 文本或含字符串 `goal` 的 JSON，与 `--goal` 互斥 | 只提取任务文本；不把文件中其他键加载成设置 |
| `workspace` / `--workspace` | CLI 默认启动 cwd；Web 必须显式选择现有 ASCII 目录 | 解析绝对路径进 request；固定，不在恢复时换目录 |
| `state_dir` / `--state-dir` | CLI 自动选用户数据下 `runs/<run_id>`；Web 从 root 派生 | 绝对路径进 request；必须在 workspace 外，固定 |
| `run_id` / `--run-id` | CLI `run-<随机标识>`；Web 由创建幂等 ID 派生 | request/run identity；格式受合同校验，不能作为恢复覆盖 |
| `resume` / `--resume` | 无；指定已有 state 目录 | 选择恢复目标，不是一项待保存的新偏好；schema、ledger、文件与副作用仍需检查 |
| `answer_file` / `--answer-file` | 无；包含 question ID/answer 的严格 JSON | 保存用户答复和关联状态；必须与待答问题匹配，要求 resume |
| `cancel_question` / `--cancel-question` | 无；问题 ID | 显式取消待答记录，不伪造用户回答，要求 resume |
| `answers` / `--answers` | 无；旧式 `field_id → string` 映射 | legacy profile 的脚本化澄清输入；普通 ask_user 恢复要求 answer-file，不能混用 |
| `amendment_reason` / `--amendment-reason` | 有变更而未传时采用宿主说明 | ledger 审计说明；没有配置/任务变更却传 reason 时拒绝 |
| `list_model_providers` / `--list-model-providers` | False | 查询命令，不导入可选 SDK 或读取凭据；返回提供商与已知模型候选 |
| `list_knowledge_packs` / `--list-knowledge-packs` | False | 查询命令，列出后退出，不创建会话或 launch |
| Web `idempotency_key` | 创建时必需 UUID | registry 的创建身份与请求摘要；重复同请求重放，不同请求冲突 |
| Web `command_id` | 后续操作必需 UUID | 命令回执与重放检查；不是可配置的会话参数 |
| Web `expected_task_revision`、`expected_amendment_sequence` | 相应命令要求当前版本 | 防止过期请求覆盖，不改变版本计数的宿主所有权 |

## CLI 显式 profile：逐字段

这些输入不在偏好文件中，也未开放为 Web 创建配置。恢复从原 request/launch 取得，不能重新绑定。

| 字段 | CLI 入口与默认 | 约束及落盘 |
|---|---|---|
| `concept_index_dir` | `--concept-index-dir`，无 | 在 workspace 外；主动 Concept 或 diagnostic 模式需要对应索引；路径进 launch，身份进 request |
| `concept_retrieval_mode` | `--concept-retrieval-mode`，`off` | `off/tool`；tool 需要索引或 bounded document 后端；进 launch |
| `diagnostic_retrieval_mode` | `--diagnostic-retrieval-mode`，`off` | `off/advisory`；advisory 需要显式 Concept 索引与可验证 BM25 语料；进 launch |
| `document_policy_mode` | `--document-policy-mode`，`off` | 当前只有 `bounded_v1` 可启用；必须同时给 spec 和 Concept tool；由 binding.mode 保存 |
| `document_policy_spec` | `--document-policy-spec`，无 | 显式 JSON 后端/范围/宿主需求；相对路径以 spec 目录为基准，形成 request/launch 中的 document_policy |
| `runtime_script_policy.allowed_cae_scripts` | `--allowed-cae-script` 可重复，None | 工作区相对 `.py` 白名单，大小写无重复；None 无此限制，空 tuple 不允许任何路径；进 request |
| `runtime_script_policy.allowed_abaqus_python_scripts` | `--allowed-abaqus-python-script` 可重复，None | 同上，适用提取 Python；进 request |
| `runtime_script_policy.allowed_solver_inputs` | `--allowed-solver-input` 可重复，None | 同上但后缀 `.inp`；进 request |
| `edit_scope` | `--edit-scope-file`，无 | 受信任的单表达式修改合同；拒绝与 runtime static preflight 等不兼容组合；进 request |

关闭 Concept 与 diagnostic 而仍显式给索引会拒绝；bounded document 使用独立 spec 后端，Concept 路径仅能另供
diagnostic advisory。CLI 根据这些 profile 决定问答/输出合同，不能直接等同于 Web 普通会话。
`document_policy_mode` 和 spec 的双重输入仍存在；收敛该冗余属于后续接口修改，不在本表伪装为已删除。

### DocumentPolicySpec 文件内部

以下全部来自显式 spec，不能作为全局默认，不可在恢复时替换。spec 的实际值及后端证据绑定进入 document_policy。
嵌套需求与独立操作是调用方声明的任务合同，不允许检索自动补造。

| 字段 | 默认 / 类型 | 校验与语义 |
|---|---|---|
| `schema_version` | `document-policy-spec-v1` | 固定格式标识 |
| `candidate` | 必需路径 | 后端 candidate，相对 spec 目录解析；绑定后必须是规范绝对路径 |
| `symbol_index` | 必需路径 | 符号索引，同一来源规则 |
| `scope_rules` | 必需路径 | 范围规则，同一来源规则 |
| `ranker_directory` | 必需路径 | ranker 资源，同一来源规则 |
| `scope` | 必需 `DocumentScope` | 文档查询范围，不是模型推断的物理事实 |
| `scope.product` | None 或 Product | 所声明的产品范围 |
| `scope.source_release` | None 或1–80字符 | 所声明的文档版本，不代表 runtime 已验证 |
| `scope.allowed_sources` | `()`，最多64项 | 所声明的来源限制 |
| `scope.constraints_origin` | 必需：host_typed / user_confirmed / model_interpreted | 记录约束来源，不能把模型解释写成用户确认 |
| `host_needs` | `()`，最多8项 | 宿主声明需求列表；规范化条件和字段标识不能重复 |
| `host_needs[].condition` | 必需，1–1000字符且非空白 | 要确认的条件 |
| `host_needs[].critical` | True | 是否关键条件 |
| `host_needs[].affects_step` | 必需，1–1000字符且非空白 | 影响哪个步骤 |
| `host_needs[].origin` | 必需：user_declared / task_contract | 宿主声明来源 |
| `host_needs[].kind` | `knowledge`；也可 physical_input | 区分知识缺口与物理输入缺口 |
| `host_needs[].clarification_field_id` | None | physical_input 必须有规范字段 ID，knowledge 必须无该 ID |
| `independent_operations` | `()`，最多8项 | 已声明可独立进行的操作；操作 identity 唯一 |
| `independent_operations[].kind` | 必需：cae / abaqus_python / solver_input | 决定允许路径后缀 |
| `independent_operations[].relative_path` | 必需，1–240字符 | 工作区相对 `.py` 或 `.inp`，禁止越界 |
| `independent_operations[].sha256` | 必需 digest | 绑定声明的文件内容 |
| `independent_operations[].independent_of_conditions` | 必需，1–8项 | 只能引用已声明的 host need 条件 |
| `independent_operations[].host_reference` | 必需，1–1000字符 | 声明依据，不能把它当运行成功证据 |

后端资源还需满足 request 中的 workspace/state 外部路径边界。实现：
[document_policy_binding](../../src/abaqus_sim_agent/coding/document_policy_binding.py)、
[document_policy_contracts](../../src/abaqus_sim_agent/coding/document_policy_contracts.py)。

### ReferenceEditScope 文件内部

| 字段 | 默认 / 类型 | 校验与语义 |
|---|---|---|
| `policy_version` | `single-reference-edit-v1` | 固定合同，不开放策略自动升级 |
| `path` | 必需字符串 | 已有工作区 Python 文件的规范 ASCII 相对路径 |
| `baseline_source` | 必需非空原文 | UTF-8 原文，包括原始换行；只用于该任务基线 |
| `baseline_sha256` | 必需64位小写 hex | 必须等于 baseline_source 原始 UTF-8 bytes 的 hash |
| `start` | 必需整数 ≥0 | LF 规范化后的字符偏移，包含起点 |
| `end` | 必需整数 >0 | 不包含终点；start < end ≤ 规范化文本长度，范围恰好是完整表达式 |

实际候选还须通过引用表达式与范围外文本不变校验。实现：[edit_scope.py](../../src/abaqus_sim_agent/coding/edit_scope.py)。

## 保存字段的权威来源

下列映射使每个 request/launch 字段都有来源，不能把“JSON 中出现”当成应新增一个用户开关的理由。

| `CodingRunRequest` 字段 | 唯一语义来源 |
|---|---|
| `run_id` | 本次运行身份，见任务表 |
| `goal` | 本次目标文本；后续 revision 另记 |
| `workspace` | 解析后的工程路径 |
| `state_dir` | 解析后的宿主状态路径 |
| `runtime` | 公共 launcher + 当前支持的 runtime 描述；`name=abaqus-2024`、`version=2024`，版本用于 Catalog 一致性检查；name 是描述元数据，不是另一个 launcher 选择开关 |
| `runtime_script_policy` | 显式任务白名单 |
| `api_catalog` | 宿主读取所选 Catalog 后生成的版本/hash/card 数等绑定 |
| `concept_index` | 显式 Concept/diagnostic 索引的内容绑定 |
| `document_policy` | 显式文档 spec 及后端绑定 |
| `static_preflight` | 启用时准备的静态来源/引擎身份，关闭时 None |
| `edit_scope` | 显式单表达式任务合同 |
| `clarification_policy_version` | `physics-materiality-clarification-v1`，宿主固定规则 |
| `limits` | 运行控制模型 |
| `runtime_policy` | 运行权限与超时模型 |
| `evaluation_tool_limits` | 可选评测调用门限 |

| `LaunchManifest` 字段 | 来源与用途 |
|---|---|
| `schema_version` | 普通产品写 `coding-run-launch-v10`；模型还可解析 v9 用于有限历史场景，产品恢复入口拒绝 v9 |
| `question_protocol` | 普通会话 `ask_user_v1`；CLI 显式 legacy profile 可为 None，由装配决定 |
| `document_policy` | 与 request 的同一文档绑定；重复保存必须一致，不是第二个覆盖源 |
| `response_enabled` | 普通会话 True；CLI 随其问答/profile 合同决定；不是独立用户默认 |
| `domain_knowledge` | 所选私有包物化后的不可变快照绑定；无包时 None |
| `capability_definitions` | 宿主从实际 Skill 定义计算的 capability ID/digest |
| `context_policy_version` | 新普通会话 `model-window-tiered-summary-v1`：90% 触发，保留原文最多 min(32000, window/8)；旧 `deepseek-1m-tiered-summary-v1` 沿 960K 固定阈值 |
| `context_window` | 实际采用窗口的快照，恢复不再查询新元数据；旧合同可省略 |
| `model_base_url` | 可选自定义模型地址，不能包含凭据 |
| `provider_environment` | 新会话绑定所选 provider 的非凭据接口/区域/组织环境值；恢复时不同则拒绝，不静默更换端点。凭据不进入该字段 |
| `filesystem_policy_version` | `utf8-cp936-preserve-v1`；固定编码与写回规则 |
| `model_provider` | 公共配置的实际网关 |
| `model_name` | 公共配置的实际模型 |
| `http_retries` | 公共配置的实际 HTTP retry 值 |
| `skill_library` | 实际 Skill 绝对路径 |
| `api_catalog_dir` | 实际 Catalog 绝对路径 |
| `api_catalog_mode` | 实际 Catalog 模式 |
| `concept_index_dir` | 显式 profile 的索引路径或 None |
| `concept_retrieval_mode` | 显式 profile 的 off/tool |
| `diagnostic_retrieval_mode` | 显式 profile 的 off/advisory |
| `static_preflight_mode` | 公共配置的实际 off/on |
| `abqpy_static_wheel` | on 时实际来源路径；off 时 None |
| `static_preflight_cache_dir` | on 时实际缓存路径；off 时 None |
| `reasoning_policy` | 按最终组合验证的实际推理策略 |
| `http_timeout_seconds` | 公共配置的实际 HTTP timeout |
| `planning_enabled` | 新产品 False；字段为历史解释保留，不开放重新启用入口 |
| `interaction_policy` | 新运行工厂采用 workspace_facts_first；底层仍可解释 legacy 评测，不能据此开放产品回退开关 |

`ModelExecutionConfig` 的10个字段对应上表 model_provider/name、http_retries/timeout、reasoning_policy、
planning_enabled、interaction_policy、context_window、model_base_url 和 provider_environment。它和 launch 之间的字段复制、模型工厂的重复显式参数仍是实现冗余，
不能把它们解释为另一层可独立覆盖的配置。

## 程序化 profile 与依赖注入

这些是底层 Agent 工厂的特殊调用合同，不是全局/项目配置。未经过产品 launch 绑定的显式评测输入，
必须由该 evaluator 自己冻结，不宣称普通 resume 能恢复全部程序化组合。

| 工厂输入 | 默认 | 职责与约束 |
|---|---|---|
| `diagnostic_retrieval_instructions_enabled` | None：跟随 diagnostic mode | 评测可固定两臂说明；不改变实际 advisory 工具模式 |
| `immutable_workspace_paths` | `()` | 文件工具写保护输入，不能宣称限制所有 Shell/动态 Python 绕行 |
| `probe_protocol_mode` | `off` | typed probe 为显式实验协议，与纯编辑冲突 |
| `probe_target_scripts` | None | typed probe 的额外目标脚本范围 |
| `task_instructions` | None | evaluator 的任务专用要求，不作为普通用户默认提示词 |
| `foundation_skill_profile` | `off` | 可选 linear_structural_fea_v1，需要 Skill 库 |
| `structural_topic_skill_profile` | `off` | 可选 structural_mechanics_v1，需要 Skill 库 |
| `knowledge_resources_dir` | None | 已物化的只读资料目录；普通产品来自私有包快照，直接工厂调用由调用者负责证据 |
| `knowledge_allowed_patterns` | `("*.md", "*.py")` | 只读资料工具的允许模式；普通产品装配传自己的固定规则 |
| `product_coding_tools` | 底层 False；普通产品由问答/response profile 推导 | 决定受限 Shell 与后台 Solver 接线，不是用户偏好 |
| `context_policy_enabled` | 底层 False；普通 launch v10 启用 | 由产品协议决定，不能由新偏好关闭原会话 context policy |
| `step_store` | 未注入时由 state 构建 | 宿主存储依赖；不是路径之外再一套可合并配置 |
| `model`、`request`、`execution_config` | 依调用入口注入 | 已解析的对象；其字段语义按前述合同，不注册新的覆盖层 |

其余工厂参数 skill_library、api_catalog_mode、concept_retrieval_mode、diagnostic_retrieval_mode、planning_enabled、
question_protocol、response_enabled 已在前表逐项定义。实现：[agent.py](../../src/abaqus_sim_agent/coding/agent.py)。

## 旧评测 budget 与浏览器偏好

旧 `CodingRunBudget` 只在固定评测/测试边界转换为当前控制模型，服务配置不再接收它。默认值属于旧输入合同，
不能推导为普通产品默认：

| 旧字段 | 默认 | 转换目标 |
|---|---|---|
| `max_clarifications` | 2 | limits.clarifications |
| `max_model_requests` | 30 | limits.model_requests |
| `max_api_lookup_calls` | 4 | evaluation_tool_limits.api_lookup_calls |
| `max_concept_lookup_calls` | 2 | evaluation_tool_limits.concept_lookup_calls |
| `max_diagnostic_retrieval_calls` | 2 | evaluation_tool_limits.diagnostic_retrieval_calls |
| `max_static_preflight_calls` | 12 | evaluation_tool_limits.static_preflight_calls |
| `max_abaqus_calls` | 9 | runtime_policy.abaqus；0 转 deny，正值转有限许可 |
| `max_solver_calls` | 3 | runtime_policy.solver；不得大于 max_abaqus_calls |
| `wall_time_seconds` | 2700.0 | limits.active_wall |

浏览器本地保存的项目不是 CodingRun 配置，也不会写回全局/项目 JSON：

| 键 / 值 | 所有者与默认 | 生命周期 |
|---|---|---|
| `abaqus-sidebar-width` | localStorage，默认280px，限制220–520px | 浏览器显示偏好，可跨刷新；不随会话恢复 |
| `abaqus-new-workspace` | sessionStorage，无默认路径 | 当前标签页的新会话目录草稿；创建时仍由宿主验证 |
| `abaqus-draft:<cacheKey>` | sessionStorage，空文本 | 未发送的输入草稿，不能冒充已保存 Thread 消息 |
| `abaqus-command:<runId>:<action>` | sessionStorage，含操作 ID 与 payload | 浏览器重试辅助；宿主回执才拥有幂等真值 |
| 本次模型、知识包勾选 | 当前页面内存，默认继承 | 提交时成为本次显式输入；不自动保存成用户偏好 |

## 核对与维护

本表依据源码核对，不把文档本身当成运行验证。已有关联测试覆盖公共来源、CLI/Web 同值解析、路径基准、
严格类型、空列表、旧会话使用原模型/资源，以及 Web 静态来源接线：
[偏好测试](../../tests/test_model_preferences.py)、[Web 配置测试](../../tests/test_web_model_preferences.py)。
具体测试通过数保留在对应运行结果，不在此复制流水账。

新增配置时必须在相应字段行说明来源、缺省、覆盖、持久化及恢复规则，并核对 CLI/Web 是否共同消费。
目前保留的区别均已显式列出：网页并非全部实验 profile 的入口，权限不由偏好文件授予，进程设置不跟随
旧会话，项目配置文件改动仍受 workspace 一致性检查。不得以“字段都在 JSON 中”或“类型检查通过”代替这些决定。
