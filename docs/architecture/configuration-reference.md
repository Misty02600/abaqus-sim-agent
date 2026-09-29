# 配置逐字段合同

本文同时给出已确认的修订方案和当前代码的逐字段基线，明确区分目标与已实现行为。
总体流程见[系统框架](system-framework.md#配置输入运行合同与恢复记录)。

## 已确认的修订方案

依据 [ADR-0045](../adr/0045-use-explicit-run-options-without-preference-files.md)，取消用户级和项目级偏好文件。
**状态：第一阶段已实施。** 已撤回两级偏好读取，收回 Skill/launcher/静态资源路径和重复文档模式入口，提供默认 Web 管理根。
Catalog 已作为包内资源接入，普通目录参数已移除；原生 provider、直接获取并缓存的模型目录、动态窗口与宿主凭据保存已接入。

2026-09-28 用户进一步确认：**用户选择能力，程序管理内部资源。** 固定 Skill 库和静态检查资源不再要求普通用户
指定位置；保留工作区、模型、执行权限、静态检查开关和私有知识包选择。以下目标表已反映本次决定。

目标来源只有：

- CLI 新会话：本次显式参数 > 程序默认值。
- Web 新会话：本次创建输入 > 本次服务启动选项 > 程序默认值。
- 继续会话：保存的会话合同 + 允许的显式修订；宿主设置与凭据由当前进程提供。

### 原13个公共偏好字段的去向

每一行均取消用户/项目文件来源。保留显式输入不意味着新增同名配置文件或保存默认功能。

| 当前字段 | 目标决定 | 对会话的影响 / 未完成边界 |
|---|---|---|
| `model_provider` | 保留本次模型接入选择；复用 Pydantic AI 内置 provider，自定义 OpenCode 单独适配 | 保存实际 provider；新会话必须明确选择，不自动推导提供商 |
| `model_name` | 保留本次明确选择；不保存个人默认模型 | 保存实际模型；新会话必须明确选择，不提供默认模型或首项自动选择；OpenCode 限制不作用于其他 provider |
| `http_retries` | 程序默认 + 本次覆盖，继续区分 HTTP 尝试与 Agent 请求数 | 当前默认4、允许的 amendment 语义暂保留；无文件继承 |
| `http_timeout_seconds` | 程序默认 + 本次覆盖，保持请求超时与 active wall 分离 | 当前默认300秒及会话固定边界暂保留；扩大模型支持时按对应 SDK 语义接线 |
| `reasoning_policy` | 保留本次请求意图；默认沿用各 provider 原生策略，OpenCode Go 的专有 high 策略隔离 | 不把 DeepSeek 专有参数发送给其他模型，也不把当前两值设计宣称为通用接口 |
| `launcher_path` | 从普通用户配置、CLI/Web 路径选择中移除，宿主调用 `abq2024` | 实际命令继续记录；类型/状态变动按开发期规则明确拒绝不兼容 state，不做迁移 |
| `skill_library` | 普通产品自动定位包内 Skill，移除 `--skills-dir` 等库路径输入；暂不新增外部 Skill 扩展能力 | 继续保存实际 Skill 定义绑定；测试/评测的显式资源注入不作为普通产品配置 |
| `api_catalog_dir` | 已移除普通用户目录输入；程序直接定位包内 data | 保存实际资源位置与身份/hash；随 wheel/sdist 分发，不另行复制到 AppData |
| `api_catalog_mode` | 保留程序默认及本次模式选择 | 当前默认 automatic_verified_only、产品无 off 的合同暂保留；模式与资源位置分开 |
| `static_preflight_mode` | 保留程序默认 off 及本次 on/off；不读取文件默认 | 已选会话保持原值；Python 3.10 语法检查不受开关影响 |
| `abqpy_static_wheel` | 程序准备与支持版本匹配的静态资源，已移除普通用户 wheel 路径选项 | 已接入固定 abqpy 2024.10 wheel 的校验下载与缓存复用；保存活跃来源版本/hash，off 不加载，失败不伪装为 clean |
| `static_preflight_cache_dir` | 缓存及解包位置由程序决定，移除普通用户缓存目录选项 | 保留工作区外边界及按内容身份组织的不可变缓存；内部 locator 不成为新的用户偏好 |
| `knowledge_pack_ids` | 默认无私有包；网页直接提交本次勾选，未勾选明确提交空列表 | 保存实际包快照；界面不再提示继承用户/项目偏好，不自动记住为下次默认 |

### 其他入口的补充决定

| 当前入口 | 已确认目标 | 保留的边界 |
|---|---|---|
| `--document-policy-mode` | 移除重复模式开关；由显式提交且通过校验的 document spec 确定对应策略 | 当前 spec 对应 bounded_v1，所需 document tool 由装配推导；不靠文件名猜模式，也不把实验 profile 默认开启 |
| `--root`（Web 管理根） | 提供程序默认目录，取消必填要求；保留高级覆盖 | 它管理会话记录，必须与用户工程分开；已有根锁、ASCII 路径和目录归属检查继续保留 |
| `--state-dir`（CLI 会话状态） | 保留现有自动默认位置及高级覆盖 | 不因此创建用户/项目配置文件，也不改变恢复目标目录 |
| `--workspace` / Web 工作区选择 | 保留用户工程选择，Web 仍要求显式选择现有目录 | 工程文件归用户，不用自动生成的管理目录替代 |

Pi 的默认发现和显式扩展只是取舍依据：本产品当前没有开放任意外部 Skill 的要求，故不因 Pi 支持
`--skill` 就新增相应能力。后续若有明确扩展需求，应单独决定，不保留一个未定义用途的普通库路径选项。

权限、任务与实验输入按下文各自合同保留；端口、浏览器、显示、观测和凭据只属于当前宿主，不写成旧会话的
启动偏好。取消两级 JSON 不等于把所有字段写进 launch，也不等于删除模型内部的 typed 配置。

### 实施边界与验收

1. 删除用户/项目 settings.json 的自动发现、读取和合并，保留共同类型、CLI/Web 适配、默认值及最终校验。
   `RunOptions` 等名称按本次输入职责组织，不能仅改名而仍暗中读取文件。
2. 不删除磁盘上已有偏好文件，不生成迁移副本；文件即使损坏、填入不同模型，也不能影响新建配置的解析。
   工作区中的旧文件仍是普通文件，快照边界不作豁免。
3. CLI/Web 以等价显式输入生成等价运行条件；参数不提供、显式0、空列表和非法组合的语义继续测试。
   Web 当前进程的启动覆盖不得悄悄写到用户磁盘。
4. 普通产品无需 launcher 路径，受控进程使用 `abq2024`；命令缺失明确失败，已有权限、独立 Solver、取消和 raw log
   证据继续有效。测试/评测确需的 runner 注入不等同于恢复普通用户安装路径选项。
5. 普通产品已无需 Catalog 目录参数，直接读取随包资源；缺失/损坏仍明确失败，不自动降级为 off。验证 wheel/sdist 的资源 bytes 与 manifest 一致，不用其他本机目录替代发行资源。
6. 恢复读取原合同与 ledger，既不读偏好，也不把新的服务默认值覆盖旧模型/资源。累计 usage、执行 provenance、
   freshness、待答和未决 effect 保持原边界。
7. 移除界面、CLI 帮助和 README 中的用户/项目偏好宣传；保留尚需的本次选择。运行/评测检查与文档核对分开留证。
8. 普通 CLI/Web 无需指定 Skill 库、abqpy wheel 或静态缓存目录。静态检查开启时由程序准备匹配资源，来源缺失、
   不可读或准备失败必须给出真实失败/不可用状态，不能静默跳过后声称检查通过；来源身份与恢复校验继续保留。
9. 单独提供合法文档 spec 即可确定对应模式与必要工具；不再同时要求重复模式开关。缺少、损坏或不支持的 spec
   明确拒绝，不扩大既有任务合同，也不把 CLI 实验功能宣称为已有 Web 功能。
10. Web 不传 root 时可使用程序默认管理根；传入高级覆盖时仍按原目录边界检查。新会话始终选择已有工程目录，
    管理目录默认化不能恢复“自动创建托管空工程”的旧行为。

模型扩展已接入 Pydantic AI 原生 provider，OpenCode 继续使用 DeepSeek 专用适配。Catalog 包内分发已集成，但第三方文档公开再分发许可仍未确认；此次不发布，也不补建仓库计划。

## 当前实现：范围与阅读方式

- 覆盖普通 CLI 的 41 个选项、Web 启动命令的 11 个选项，共 44 个不同选项名，不计 `--help`；其中登录/退出属于宿主操作，不是运行配置。
- 对 8 个公共运行选项、8 个 `ServerConfig` 字段、运行控制和显式 profile 输入分别定义。
- 对 request/launch 的保存位置逐字段映射；重复序列化不计为另一项用户配置。
- 会话 ID、操作命令、实验 spec 和宿主生成记录单独列出，避免将它们混为应用偏好。
- 第三方 SDK 的全部环境变量、每个评测 Case 的物理数据、私有知识包资源 manifest、UI 临时展开状态和
  算法实现常量不属于本表的应用配置表面；相关资源合同继续由所属组件维护。

当前行为查“当前实现：公共运行选项”；尚未完成的目标查上方修订方案。表内“固定”表示本会话的普通恢复不能重新指定；
“可修订”表示通过已有 typed amendment 校验后修改有效值，原始文件不覆盖，usage 不清零。

## 当前实现：本次输入与合并规则

公共输入为 `RunOptions`，只接受 CLI/Web 本次输入，不读取、创建、迁移或删除用户/项目 settings.json。

- CLI：本次显式参数 > 程序默认。Web：创建请求 `options` > 本次服务启动 `overrides` > 程序默认。
- 省略字段表示未指定，显式 null 拒绝；严格类型、未知字段和不兼容组合仍报错。0 是实际数值，不代表未指定。
- 列表整体替换，知识包空列表表示本次不启用；不保存为下次个人默认。
- CLI 显式文件路径基于启动 cwd；公共 RunOptions 不含资源路径。Catalog 位置由包内 locator 决定，不接受显式覆盖。
- 内置 Skill 由包定位，普通 runner 命令为 `abq2024`；Windows 在进程执行时按 PATH/PATHEXT 定位 batch 命令，
  不把解析结果作为用户设置保存。状态中的 `runtime.launcher_path` 保留字段名，普通新会话记录值 `abq2024`；
  评测底层的绝对 launcher 注入继续按其合同使用。
- 静态检查 on 时由程序准备固定 abqpy 2024.10 wheel。来源为 PyPI 官方不可变 URL，下载校验1884662字节及
  SHA-256 `bdfe99139281489354c01f00b7ee8b96ee1d281b461441dcfe7fad5fda894504` 后原子发布到应用缓存。
  有效缓存不联网，损坏缓存不静默覆盖；off 不下载，缺少 BasedPyright extra、网络不可用或校验失败均不伪装成 clean。
- 会话保存实际条件和资源绑定；恢复沿用保存合同与 ledger。旧偏好文件即使存在也不读取，但工作区中的任何
  文件修改仍按原 workspace snapshot 规则检查。

实现：[options.py](../../src/abaqus_sim_agent/coding/options.py)、
[公共参数](../../src/abaqus_sim_agent/coding/option_arguments.py)、
[静态资源准备](../../src/abaqus_sim_agent/coding/static_preflight.py)。

## 当前实现：公共运行选项

本节来源均为上述本次输入。Web HTTP 使用同名 `options` 子字段；页面当前直接提供模型和知识包选择，
其他字段可通过同一 HTTP 合同传入，不能据此称页面已有全部编辑控件。

| 字段 | CLI 参数 | 无任何来源时的有效默认 | 类型、约束与用途 | 会话保存与修改 |
|---|---|---|---|---|
| `model_provider` | CLI `--model-provider` / Web 创建输入 | 无默认，必须明确选择 | `providers.ModelProvider` 中的原生 provider 与 OpenCode；决定 SDK、协议及凭据选择 | `launch.model_provider`；CLI 可修订，须兼容已保存推理策略并有对应凭据 |
| `model_name` | CLI `--model` / Web 创建输入 | 无默认，必须明确选择 | 非空、无首尾空白；所有 provider 采用同一选择规则；明确 provider/model 必须匹配当前可选候选，目录外型号拒绝；失败不换用其他模型。目录不代表远端账号有权访问；DeepSeek 前缀限制只在 OpenCode adapter | `launch.model_name`；CLI 可修订，不保存个人默认 |
| `http_retries` | `--http-retries` | 通常 `4`；Mistral、Typesafe、Hugging Face、Bedrock（含 gateway）使用 SDK 默认 | 整数 ≥0；仅使用 SDK 默认重试的 provider 拒绝显式数值；SDK 对可重试 HTTP 失败的额外尝试次数，不等于 Agent 请求次数 | `launch.http_retries`；CLI 可修订，`0` 必须作为显式值保留 |
| `http_timeout_seconds` | `--http-timeout-seconds` | `300.0` | 有限正数；传给模型/SDK 的请求超时，不等于 active wall 或 Solver 超时 | `launch.http_timeout_seconds`；固定，恢复显式指定拒绝 |
| `reasoning_policy` | `--reasoning-policy` | 最终 Go + V4-Flash 为 `enabled_high`，其他为 `provider_default` | 仅允许这两个值；`enabled_high` 与最终 provider/model 不兼容时拒绝；显式值优先于自动选择 | `launch.reasoning_policy`；固定。修订模型时不重新按当前偏好推导它 |
| `api_catalog_mode` | `--api-catalog-mode` | `automatic_verified_only` | `full` / `full_with_discovery` / `automatic_verified_only` / `automatic_exact_signature`；普通产品不接受 `off` | `launch.api_catalog_mode`；固定，不由是否传目录推断成 `full` |
| `static_preflight_mode` | `--static-preflight-mode` | `off` | `off/on`；只控制可选 BasedPyright，两个 Python runtime 的 3.10 语法检查始终存在 | `launch.static_preflight_mode`；固定，恢复显式 on/off 都拒绝 |
| `knowledge_pack_ids` | `--knowledge-pack`（可重复）、`--no-knowledge-packs` | `()`，不启用私有包 | 最多32个、唯一、匹配 `[a-z0-9][a-z0-9-]{0,63}`；整体替换，空列表清空 | launch 保存实际 `domain_knowledge` 快照绑定；固定，不在恢复时重扫新选择 |

保存来源路径与保存内容指纹各有用途，不能删除其中一项来“去重”。源码中 `ResolvedRunOptions` 是本次
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
model provider、model name、HTTP retries。二者省略表示不改，显式 null 拒绝，空 patch 拒绝。不要根据旧 ADR
当时的“预算均可修改”描述，推断当前 limits 已有修改入口。

实现：[contracts.py](../../src/abaqus_sim_agent/coding/contracts.py)、
[amendments.py](../../src/abaqus_sim_agent/coding/amendments.py)。

## 宿主与凭据：逐字段

`ServerConfig` 的8个字段如下。它是程序化宿主输入，不能把 dataclass 类型注解当成所有边界都已做运行时校验；
各值仍由目录管理、HTTP server 或相应 typed 控制模型检查。

| 字段 | 默认 / 入口 | 使用者与处理决定 | 进会话吗 |
|---|---|---|---|
| `root` | Web `--root` 可省略，默认应用数据目录下 `web`；程序化宿主仍显式传 root | 管理根，目录注册器检查；按固定根保存 states/registry，与选定工程互不包含 | 根本身是宿主配置；派生的该 run `state_dir` 保存于 request |
| `knowledge_packs_root` | None：用户数据目录 `knowledge/packs`；程序化宿主可覆盖 | 发现私有包的本机目录，源内容由包准备器验证 | 不保存发现根；实际采用的包快照由会话保存 |
| `stream_model` | True；程序化宿主输入 | 控制当前宿主是否流式展示模型请求；CLI 当前调用默认非流式 | 不作为旧会话的恢复配置 |
| `overrides` | 空 `RunOptions`；Web 启动公共参数 | 当前服务为新会话提供的非模型选项覆盖；model_provider/model_name 禁止放入 overrides，必须由创建请求指定；其余选项比创建请求优先级低，不读取配置文件 | 容器不落盘；最终采用的字段按公共表保存 |
| `limits` | `CodingRunLimits()` | 新会话限制的 typed 输入 | 创建时进 request，恢复使用保存值 |
| `runtime_policy` | `RuntimeExecutionPolicy()` | 新会话权限/超时基础值，再叠加创建请求显式权限 | 创建时进 request，允许字段以后走 amendment |
| `evaluation_tool_limits` | None | 为评测/测试注入低成本调用门限，不以 server budget 再覆盖一遍 | 显式设置时进 request |
| `context_warning_threshold_tokens` | 900000；None 关闭提醒 | 当前 UI 与最新模型输入计数比较；不是模型窗口或 compaction 门限 | 不进入 launch，不改变会话 context policy |

| 其他字段 / 变量 | 默认、来源与用途 | 持久化与失败边界 |
|---|---|---|
| Web `port` / `--port` | 8765，整数参数；宿主绑定 `127.0.0.1` | 不进会话；当前由 server/socket 检查绑定可用性，不宣称 CLI 已预检整个端口范围 |
| Web `no_open` / `--no-open` | False，服务 started 后打开普通本机 URL | 不进会话；每次启动独立选择 |
| `phoenix` / `--phoenix` | False，必须显式启用 | 不从项目偏好自动开启；运行证据可记录观测元数据，但恢复不据此启用导出 |
| `DEEPSEEK_API_KEY` | 无；当前进程环境 | 官方网关凭据，捕获后剥离继承环境；不写入偏好或 request/launch |
| `OPENCODE_API_KEY` | 无；当前进程环境 | Go/Zen 凭据；Web 按会话 provider 选取，缺失拒绝，不回退到别的模型 |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://127.0.0.1:6006`；当前进程环境 | 只接受无鉴权本机 HTTP 地址，可指定端口；`--phoenix` 启用时校验，不进会话配置 |
| `PHOENIX_API_KEY`、`PHOENIX_CLIENT_HEADERS`、`OTEL_EXPORTER_OTLP*HEADERS` | 不作为产品配置输入 | 本机模式不需要密钥；清除以避免子进程继承 |
| `OTEL_EXPORTER_OTLP*ENDPOINT`、`OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*` | 不作为产品配置输入 | SDK 会话忽略其他导出地址及请求头采集设置，退出时恢复；不发现工作区 `.env.phoenix` |

固定回环地址、进程收尾等待、文件预览大小等实现常量不因出现在源码中就自动开放成用户配置。
实现：[Web CLI](../../src/abaqus_sim_agent/web/cli.py)、[controller](../../src/abaqus_sim_agent/web/controller.py)、
[model](../../src/abaqus_sim_agent/coding/model.py)、[telemetry](../../src/abaqus_sim_agent/telemetry.py)。

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
| `document_policy_mode` | 无用户模式开关；无 spec 时 off | 从受支持 document spec 推导 bounded_v1 与 document tool，实际 binding.mode 继续保存 |
| `document_policy_spec` | `--document-policy-spec`，无 | 显式 JSON 后端/范围/宿主需求；相对路径以 spec 目录为基准，形成 request/launch 中的 document_policy |
| `runtime_script_policy.allowed_cae_scripts` | `--allowed-cae-script` 可重复，None | 工作区相对 `.py` 白名单，大小写无重复；None 无此限制，空 tuple 不允许任何路径；进 request |
| `runtime_script_policy.allowed_abaqus_python_scripts` | `--allowed-abaqus-python-script` 可重复，None | 同上，适用提取 Python；进 request |
| `runtime_script_policy.allowed_solver_inputs` | `--allowed-solver-input` 可重复，None | 同上但后缀 `.inp`；进 request |
| `edit_scope` | `--edit-scope-file`，无 | 受信任的单表达式修改合同；拒绝与 runtime static preflight 等不兼容组合；进 request |

关闭 Concept 与 diagnostic 而仍显式给索引会拒绝；bounded document 使用独立 spec 后端，Concept 路径仅能另供
diagnostic advisory。CLI 根据这些 profile 决定问答/输出合同，不能直接等同于 Web 普通会话。
`--document-policy-mode` 已移除；只传 spec 即可选择对应策略，显式指定 Concept off 则因冲突拒绝。

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
| `api_catalog` | 宿主读取包内 Catalog 后生成的版本/hash/card 数等绑定 |
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
| `context_policy_version` | 新会话 `model-window-tiered-summary-v1`；旧固定1M记录保持原值，不迁移 |
| `context_window` | 由框架/适配器解析并保存的窗口；不是用户输入，恢复沿用原值，显式换模型时由程序重新推导 |
| `model_base_url` | 仅识别旧开发期记录；非空即明确拒绝恢复。新会话不写入该字段，不迁移旧自定义地址 |
| `provider_environment` | provider 所需的非凭据环境快照；恢复时检查，不隐式改指向 |
| `filesystem_policy_version` | `utf8-cp936-preserve-v1`；固定编码与写回规则 |
| `model_provider` | 公共配置的实际网关 |
| `model_name` | 公共配置的实际模型 |
| `http_retries` | 公共配置的实际 HTTP retry 值 |
| `skill_library` | 实际 Skill 绝对路径 |
| `api_catalog_dir` | 程序定位的包内 Catalog 实际路径，仅作为会话记录；恢复核对原内容身份 |
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

`ModelExecutionConfig` 的9个字段对应上表 model_provider/name、http_retries/timeout、reasoning_policy、
planning_enabled、interaction_policy、context_window、provider_environment。它和 launch 之间的字段复制、模型工厂的重复显式参数仍是实现冗余，
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
| `abaqus-appearance` | localStorage，light/dark/system，默认 system；非法或不可读时按 system | “设置 → 常规 → 外观”即时切换浅色/深色/跟随系统；system 随系统变化，跨刷新保留；不写入会话、用户或项目配置文件 |
| `abaqus-new-workspace` | sessionStorage，无默认路径 | 当前标签页的新会话目录草稿；创建时仍由宿主验证 |
| `abaqus-draft:<cacheKey>` | sessionStorage，空文本 | 未发送的输入草稿，不能冒充已保存 Thread 消息 |
| `abaqus-command:<runId>:<action>` | sessionStorage，含操作 ID 与 payload | 浏览器重试辅助；宿主回执才拥有幂等真值 |
| 本次模型选择 | 当前页面内存，初始 provider/name 均为空 | 用户从统一列表直接点选模型，条目同时确定 provider/name；可按型号或提供商搜索；未完成选择禁用新会话发送；新建下一会话清空选择 |
| 本次知识包勾选 | 当前页面内存，默认空列表 | 直接勾选，无继承开关；创建时始终提交 knowledge_pack_ids，未选为 []，覆盖服务选项；新建下一会话清空，不保存成偏好 |

## 核对与维护

本表依据源码核对，不把文档本身当成运行验证。已有关联测试覆盖公共来源、CLI/Web 同值解析、路径基准、
严格类型、空列表、旧会话使用原模型/资源，以及 Web 静态来源接线：
[本次输入测试](../../tests/test_run_options.py)、[Web 配置测试](../../tests/test_web_run_options.py)、
[静态资源测试](../../tests/test_managed_static_source.py)。
具体测试通过数保留在对应运行结果，不在此复制流水账。

新增配置时必须在相应字段行说明来源、缺省、覆盖、持久化及恢复规则，并核对 CLI/Web 是否共同消费。
目前保留的区别均已显式列出：网页并非全部实验 profile 的入口，权限不由偏好文件授予，进程设置不跟随
旧会话，项目配置文件改动仍受 workspace 一致性检查。不得以“字段都在 JSON 中”或“类型检查通过”代替这些决定。

## 模型发现与连接

接入职责、选择与恢复的正式取舍见 [ADR-0046](../adr/0046-use-native-providers-and-available-model-selection.md)；
本节维护当前接口、排序与逐字段实现合同。

`--list-model-providers` 和 Web `GET /api/v1/model-providers` 是不按凭据过滤的连接发现入口，不依赖目录下载，
返回的 `models` 为兼容既有 DTO 保留的空数组；实际候选统一通过下述模型列表取得。
`--list-models` 和 Web `GET /api/v1/models` 共用 `model_selection.available_models`：仅返回认证来源已配置、
SDK 可导入、上下文窗口至少1024且元数据支持工具/文本输出的模型。条目只有 provider、name、context_window，
不返回凭据。目录缺失或无合格候选的 provider 暂不能新建会话；凭据管理仍可用于已有会话，不通过手填型号绕过。

按 [ADR-0048](../adr/0048-fetch-models-dev-with-local-cache.md)，程序直接读取 `https://models.dev/api.json`。
每进程首次需要目录时下载，之后读取内存快照；无已配置认证时不必下载。网页“刷新可选模型”通过
`GET /api/v1/models?refresh=true` 重新获取，`refresh` 缺省为 false，只是本次刷新操作，不持久化。
程序用固定10秒 HTTP 超时，成功解析后原子写入
`platformdirs.user_cache_path("abaqus-sim-agent", appauthor=False)/models/models.dev.json`。
缓存保存公开 JSON，不含凭据，不属于偏好配置；没有目录地址、缓存路径或周期输入。

网络或格式失败使用最近有效的内存/磁盘缓存，首次无缓存返回 `model_catalog_unavailable`（HTTP 503），
提示检查网络后刷新；失败数据不覆盖有效缓存。缓存写入失败时当前进程仍使用成功获取的数据，并记录宿主警告。
新建会话与主动换型号必须取得有效候选，即使 SDK 能匹配型号也不能绕过目录；恢复使用已保存窗口，不访问目录。

第三方目录筛除不支持工具/文本及标为停用的条目，不再用 SDK 名称表补候选。移除生成脚本、包内模型 JSON
和 Pi 源码提取。OpenCode 仍保留现有局部适配的3个 DeepSeek 候选。models.dev 当前没有 Codex 订阅目录，
因此 Codex 暂不开放新建会话；凭据仍可保存供已有会话使用，不另维护型号或窗口表，也不引导用户手填。
Azure 私有部署名和本地服务安装型号不能从公共目录推导，当前没有候选时不开放普通新会话。
GitHub Models（不是 Copilot）、SambaNova 等未被该来源覆盖，Heroku 等仍可能缺窗口，不能宣称
每个连接都有完整菜单；这些服务若需完善发现，需要另行接其目录接口或取得可靠元数据。

候选列表按 provider ID、model ID 的字符串升序排序；这只是稳定顺序，不是质量、价格或新旧推荐。
新会话必须明确给出 provider/model 两项，缺少任意一项都提示先选择模型；即使列表只有一个候选也不自动选中。
CLI 使用 --model-provider 和 --model；Web 只接受本次创建请求中的选择，移除 Web 服务启动的两个模型参数。
其余运行选项仍按 Web 创建输入 > 本次服务启动输入 > 程序默认值合并。
按 [ADR-0049](../adr/0049-select-product-models-only-from-available-catalog.md)，完整明确的 provider/model 也必须匹配可选列表。
网页在消息输入框下方保留一行模型入口，点击只展开统一模型搜索列表，选定模型后自动收起；不提供目录外输入。CLI `--model` 和 API 字段只用于选择已列出的型号；不匹配不回退。SDK 本机/云认证链仍负责实际调用，
但新的普通会话必须先有满足当前认证发现和元数据要求的候选。
校验失败保留原选择并明确报错，不自动换模型。最终 provider/name/window 保存到 launch；恢复直接用原合同。
连接新的提供商、SDK/目录变化或可选顺序变化均不重选旧会话模型。
不维护额外的型号黑名单、推荐型号表或手工能力库；SDK 通用元数据可能有缺漏，实际请求仍可能失败。
此时保留明确错误，不静默切换模型；排序仅用于展示，不再决定默认选择。

认证发现使用本应用保存的凭据、已捕获的环境 API Key，以及 SDK 可在本地解析的凭据。
AWS 复用 botocore 的环境、共享凭据和配置文件 provider，按 SDK 的 profile/字段规则解析，不用文件存在代替有效凭据；
Google 复用 google-auth 的标准 ADC 路径及 authorized-user/service-account 解析器。发现阶段不执行外部认证命令、
刷新令牌、交换角色或访问云元数据服务。这些来源仍由实际模型调用时的完整 SDK 认证链处理。
配置来源可解析不证明令牌有效、模型权限或额度；旧会话凭据缺失不触发模型重选。
Web `GET /api/v1/model-connections` 仅返回本地已配置认证的 provider ID，供连接状态和凭据管理使用，不返回凭据。
设置 API Key 或完成登录后刷新候选和连接状态；同一凭据家族的协议别名共用凭据，凭据不存入会话。
登录不会自动选择模型。模型列表汇集已配置认证的来源，每项以提供商名称作标签；点击一项同时提交 provider/name，无单独提供商选择步骤。搜索仅过滤已有候选，不接受目录外输入。已连接但没有候选的提供商（如 Codex）显示“暂无可选模型”，没有可点击的型号，不能开始对话。认证操作位于右上角“设置 → 模型账号”，只在此处选择提供商并登录、保存或移除 Key。模型弹窗仅保留进入设置的小入口，不展示认证表单。设置变更刷新模型列表，但不自动选中型号或修改旧会话。

`GET /api/v1/model-provider-dependencies` 单独检查 SDK。API Key 通过宿主环境、CLI 隐藏输入或
Web `ModelConnection(provider, api_key)` 传入。交互输入和登录结果保存到
`platformdirs.user_data_path("abaqus-sim-agent", appauthor=False)/auth.json`，Windows 通常为
`%LOCALAPPDATA%/abaqus-sim-agent/auth.json`。CLI/Web 共用，路径由程序决定，不保存到工作区、会话或偏好文件。
本应用保存值优先于环境密钥；删除本应用记录后环境和 SDK 本机来源仍可使用。环境密钥不自动写入文件。

| 宿主操作字段 | 来源、缺省与校验 | 持久化与恢复规则 |
|---|---|---|
| CLI `--login PROVIDER` | 显式操作，无默认；与 `--logout` 互斥，不能同时执行任务 | API Key 隐藏输入或原生 OAuth；保存成功后退出；不决定下次模型 |
| CLI `--logout PROVIDER` | 显式操作，无默认 | 删除该凭据家族；不撤销远端授权、不修改环境/其他应用文件、不改会话模型 |
| CLI `--oauth-client-id` / Web `LoginRequest.client_id` | 仅 Copilot 设备登录要求自己的 OAuth 应用 ID；无默认，不借其他应用身份 | 只用于本次登录，不保存为用户偏好或会话字段 |
| Web `ModelConnection.provider/api_key` | 已有提供商，非空 Key；Codex 拒绝 API Key，使用登录 | 更新共享文件中的对应家族；更新不影响其他家族 |
| Web `LoginRequest.provider` / 登录 ID | 显式选择 Codex/Copilot；ID 由宿主生成，状态 waiting/completed/failed | 进度在内存，成功凭据落盘；保存失败显示失败，返回值不含令牌 |
| Web `DELETE /model-connections/{provider}` | 显式删除，需要同源 session/CSRF | 与 CLI logout 相同；在运行中的请求不被隐式取消 |

`auth.json` 由程序维护：记录 `kind`（api_key/codex_oauth）、`secret`、Codex 所需 `refresh_token/account_id`，
以及防止并发覆盖的 `revision`。这些是持久化记录，不是用户配置项；无凭据时不自动创建文件。
文件明文保存，依赖本机用户目录权限；写入加跨进程锁并原子替换。损坏时明确报错，不覆盖、不猜测修复。
Codex SDK 调用存储回调回写刷新结果，陈旧刷新不能覆盖重新登录或已删除记录。Copilot 原生流程需要自己的
Client ID；直接保存并使用 SDK 返回的 access token，不因返回 expiry/refresh 字段而拒绝登录。
不另外交换 Copilot 短期令牌，不实现 OAuth 自动续期，也不保存当前未使用的 refresh_token/expiry 字段；
远端凭据过期或撤销后重新登录或更新 Token。保存成功不表示凭据永久有效，也不代表已经验证推理权限。
CLI `--login github-copilot` 默认隐藏输入已有 Token，显式附带 `--oauth-client-id` 才启动设备登录。
本机其他应用的账号凭据仍由其框架管理，不改写其文件。创建或连接不等于已经验证远端模型。

### 程序拥有的模型窗口与端点

普通 CLI/Web 不接受 `context_window` 和 `model_base_url`，相关参数、页面控件与 API 字段均已移除。
HTTP 传入旧字段返回校验错误，不静默忽略。上下文窗口使用 SDK 和目录已知值中的较小值，只有一方有值则采用它，
已有的局部 provider 适配仍保留；未知时提示该模型
暂不支持创建会话，不要求用户手填，也不把任意未知模型当作1M；窗口快照仍用于恢复和按比例压缩。
显式更换模型的 amendment 先匹配当前可选列表，再保存所选窗口；未实际换型号则保持原窗口，用户不能单独覆盖它。

端点遵循 provider SDK 的原生接线，不提供项目通用 URL 覆盖、按任意端点设置凭据或自定义部署窗口的入口。
已有 SDK 云连接/环境来源及 `provider_environment` 的恢复绑定保持原合同；这不代表 Azure 等部署场景已完成
产品验收，也不新增一套地址环境变量。旧 `model_base_url` 非空的开发期会话明确拒绝恢复，不自动改指向。
