# 使用指南

[返回项目首页](../README.md) · [开发与评测](development.md)

从源码安装和首次启动见[快速开始](../README.md#快速开始)。本页保留日常操作、可选功能与诊断说明。

- [本地 Web 工作台](#本地-web-工作台)
- [运行 CodingRun](#运行-codingrun)
- [可选功能](#可选功能)
- [按需查看本地 Phoenix Trace](#按需查看本地-phoenix-trace)
- [当前产品边界](#当前产品边界)

## 本地 Web 工作台

已接入 FastAPI、React/TypeScript 和 assistant-ui ExternalStore。页面接受任意问题、解释请求、局部步骤或完整任务，支持文字答复、必要澄清、显式继续、
工具记录、宿主验收与用量，以及工作区文本/图片、当前CAE工具产生的fresh OBJ几何预览。CLI 与 Web 共用同一个 CodingRunService，
不增加 Agent、运行工具或物理成功判定。当前工程验收使用确定性模型；两条新真实 Web 仿真主线仍待独立执行。

源码运行需要 Node.js 22+ 构建一次；构建后的 wheel 自带页面，运行它无需 Node：

```powershell
uv sync --locked --all-groups --extra web
uv run --no-sync python scripts/build_web.py
uv run --no-sync abaqus-sim-agent-web
```

启动前可在宿主环境配置 `OPENCODE_API_KEY` 或 `DEEPSEEK_API_KEY` 等相应提供商凭据，也可在右上角“设置 → 模型账号”登录或填写 API Key。服务就绪后默认打开
普通本机地址 `http://127.0.0.1:8765/`，使用 `--no-open` 可关闭自动打开。页面会无感建立本地浏览器会话，
不需要复制终端 token。默认 CLI/Web 共用 `%LOCALAPPDATA%/abaqus-sim-agent/runs/<run_id>` 保存运行状态；
Web 的 `registry/`、`cache/`、`control/` 仍保存在应用数据目录下的 `web/`。CLI 已保存的当前格式普通会话
会出现在网页列表中，不需要生成 Web 登记；刷新页面即可发现其他进程新建的会话。也可用
`--root D:\abaqus-web` 启动隔离的管理目录，此时只读取该目录的 `states/`，不扫描默认 CLI 目录。
旧 Web 会话继续从原 `web/states/` 读取，不搬迁状态；`workspaces/` 只保留旧托管工作区记录。
新会话必须先选择已有本机 ASCII 目录，网页不会自动创建托管空工作区；文件原地读写，会话建立后目录固定，
所选目录不能包含宿主管理根、状态根或位于其中。左侧栏显示当前工作区并可切换：选择已有工作区会恢复其最近会话，选择其他目录会进入新会话草稿。
原会话始终绑定原目录。现有文件会进入验收基线，state/evidence 仍由管理根保存。
Web 登记格式现为 v3，旧 v2 明确拒绝读取，不自动迁移。普通产品不限制模型请求、提问、低成本工具或 active wall；Abaqus 默认允许，
Solver 默认禁止，创建会话时可改为允许或允许有限次数。Go/Flash 使用 enabled/high、HTTP 300 秒和 4 次
SDK request retry，普通产品默认关闭 Planning。

CLI 会话空闲后可以在 Web 查看、回答澄清或追加消息；Web 已落盘的会话也可通过 CLI `--resume <state目录>`
恢复原状态。两端保持原模型、权限、累计用量和执行证据，同一状态仍由 OS 锁排他保护。另一进程正在执行时，
网页只显示占用状态，不提供跨进程停止或接管。CLI 对终态的纯 `--resume` 仍拒绝空跑，`--resume --goal` 仍是
显式任务修订，不等同 Web 普通追加消息。本次不增加评测页面、不导入历史批次、不承诺旧 schema 兼容。

会话列表支持确认后永久删除记录，保留工程文件；活动执行由宿主先收尾，失败时显示错误并允许重试。
会话建立运行状态后，可在“运行详情”修改 Abaqus/Solver 权限；待答状态也可修改，问题保持不变。修改只影响
未来提交，不自动继续会话或取消已有执行，有限次数按累计总上限解释，已消耗用量不会清零。

页面已支持基础多轮对话：首次点击“发送”直接开始，回复后可在同一会话继续追问或追加要求；
Enter发送、Shift+Enter换行，草稿按会话保存在当前浏览器会话中。历史从宿主恢复，追加消息沿用原工作区和
累计用量。当前处理期间可编辑草稿，处理结束后发送。回复支持流式逐步显示和复制，会话可重命名；“停止任务”
会中断当前 Agent 工作段及其前台工具，停止后保留已收到的回复、文件和用量，可继续或发送新消息。已经后台化
的 Solver 不随该操作取消，仍可从状态栏单独“取消求解”。`solver_job(action=submit)` 在 Web 中作为独立
`ToolExecution` 运行并立即把 execution ID 返回模型；模型可继续独立工作，需要结果时再调用 `action=wait`。
状态栏显示 Job、已用时间和当前状态，运行详情保留 PID、退出码与错误。当前宿主仍持有进程控制句柄时可以
取消这一次 Solver；正常完成当前 Agent run 前必须等待已提交的 Solver，用户中断当前工作段时后台求解可继续。
后台 Solver 运行期间不会启动新 Agent 工作段；到终态且工作区与宿主记录精确匹配后才允许继续。
消息和澄清答复先由宿主持久化，再调度执行；若已保存但未启动，页面提示显式继续。普通Web追加消息保存到
`thread.json`并作为同一CodingRun history上的continuation执行，不推进task revision；每条新消息仍以提交前的
workspace/execution状态划定本段fresh evidence。显式修改任务合同的CLI `--goal` amendment继续建立task revision。

界面采用Pi启发的紧凑布局：会话与文件位于左栏，宽屏下运行详情和文件预览共用可收起、可调宽的右侧栏；
窄屏下以覆盖面板显示。工具调用先显示
目标、阶段、耗时、超时、警告和文件变更摘要，完整输入/返回折叠保留。底栏直接显示宿主阶段、连接、模型、
推理配置、累计用量、wall time及当前允许操作。会话标题可搜索；`Ctrl/Cmd+K`打开命令面板，输入框外可用
`N`新建、`R`刷新。视觉组件和文件图标的Pi/Catppuccin来源见[复用记录](../webui/THIRD_PARTY_REUSE.md)。

新普通CLI/Web会话可仅回答问题，不要求生成文件或调用Abaqus；页面显示“已回答”，不赋予执行验收范围。
只做一个环节时，不要求其他环节的材料、载荷或边界条件；只在当前请求确实依赖缺失事实时澄清。
开发期旧产品 state 不迁移；保留的冻结trial与报告仍按原合同解释。见
[ADR-0030](../docs/adr/0030-drop-pre-release-coding-run-state-compatibility.md)。

当前只接受本机单用户访问，每个服务只有一个主动工作段，忙时拒绝第二个执行。浏览器断开不停止任务；
正常关闭服务会等待当前 Agent 工作段；页面的停止按钮按活动 operation ID 取消当前 Agent 与前台工具并保存
中断状态，过期按钮不能取消后来的工作段。Solver submit
立即返回 execution ID；模型调用 wait 后普通 await 终态，等待不消耗 token 或 active wall，也不重复提交。
服务在 Solver 运行中关闭时会终止本机控制句柄并转为 `needs_reconcile`；重启后不按 PID/Job 重新附着，
也不会自动重跑。本版没有文件上传。文本预览上限256 KiB，
网页下载上限200 MiB；更大文件从本地工作区读取。HTML/SVG仅作为附件下载。

对话中的本地图片按工作区相对路径读取，例如 `![位移图](outputs/figures/displacement.png)`，
支持 PNG/JPEG/WebP，沿用工作区文件预览的 2 MiB 上限。CAE 建模执行记录下的 PNG 同样按该次记录的路径显示，
不要求图片仍是当前轮次的新产物。重启和追加消息不会清除已有引用；覆盖原图后重新加载显示当前内容，
删除或移动原图后显示读取失败。程序不另存图片副本，图片展示也不代表旧交付物的 hash 验证仍然通过。

接口与状态职责见[架构总览](../docs/architecture/overview.md)和[系统框架](../docs/architecture/system-framework.md)。
前端开发与第三方来源见 [webui/README.md](../webui/README.md)。`/third-party-notices` 提供构建随附的依赖声明。

## 运行 CodingRun

**配置调整状态：** 已取消用户级/项目级偏好文件读取。普通入口使用 `abq2024` 命令、包内 Skill 和程序管理的
静态资源；Web 管理根默认位于 `%LOCALAPPDATA%/abaqus-sim-agent/web`，可用 `--root` 高级覆盖。
Catalog 已随应用包安装，CLI/Web 自动定位并直接读取；不需要目录参数或首次启动解压。已接入 Pydantic AI 原生 provider、模型候选列表及模型窗口；OpenCode 的 DeepSeek 适配独立保留。
模型窗口由框架和适配器自动确定，普通 CLI/Web 不提供窗口或自定义 API 地址输入；未知窗口的模型暂不支持创建会话。
Catalog 的第三方文档内容公开再分发许可仍未确认，见[来源说明](../src/abaqus_sim_agent/api_catalog/data/THIRD_PARTY_NOTICES.md)。

新会话初始为“待选择模型”，不自动选择默认模型或列表首项。网页直接搜索并点选已连接来源的模型，提供商仅作为条目标签，
选定工作区和模型后才能发送消息；CLI 必须提供 --model-provider 与 --model。--list-models 与网页共用候选列表。列表为空时提示添加凭据或刷新目录；网页仅从列表选择，CLI/API 显式型号也必须匹配候选，不开放目录外手填。
`--list-model-providers` 与页面“设置 → 模型账号”展示可接入提供商；连接就绪不等于远端模型权限已验证。
恢复会话沿用保存的模型，缺少原凭据时提示补充，不自动切换。OpenCode 的型号和 session header 仍由单独适配处理。

网页输入 Key 或完成登录后自动保存，重启后继续使用；CLI/Web 共用本机应用数据目录的 `auth.json`。
CLI 可用 `uv run abaqus-sim-agent --login openai` 隐藏输入并保存 Key，或 `--login openai-codex` 完成账号登录，
`--logout PROVIDER` 删除本应用的记录。环境密钥也可使用，但不会自动写入文件。凭据文件是明文，不应提交或分发。
模型菜单直接获取 models.dev 并自动缓存；每进程首次需要目录时获取，网页可点“刷新可选模型”重新获取。
联网失败使用有效缓存，首次无缓存则明确提示；不再要求维护者生成目录并发布新版。缓存路径由程序管理，
没有目录路径或刷新周期配置。SDK 名称表不再补候选；Codex 登录后会出现在已连接提供商中，但模型列表为空，不能选模型开始新会话；已有会话仍按保存记录恢复。
列表表示公共候选，不保证账号额度和远端权限，见 [ADR-0048](../docs/adr/0048-fetch-models-dev-with-local-cache.md)。
Copilot 可通过 `--login github-copilot` 保存已有 Token；设备登录使用 Pydantic AI 原生接口，需提供自己的 OAuth Client ID（CLI `--oauth-client-id`）。返回的 access token 直接用于调用，不另做交换或自动续期；过期或撤销后重新登录或更新 Token。
Azure 私有部署、本地服务型号不能由公共目录自动推导。详细边界见 [模型发现与连接](../docs/architecture/configuration-reference.md#模型发现与连接)。

CLI/Web 新会话模型必须明确选择；其他运行选项共用来源规则：CLI 显式参数覆盖程序默认值；Web 创建请求的 `options` 覆盖当前服务启动选项，
再使用程序默认值。原用户/项目 settings.json 即使存在或损坏也不读取、不修改、不迁移。CLI 相对路径基于启动
目录，Web 创建请求相对路径基于所选工作区。字段省略表示未指定，列表整体替换，`[]` 清空本次知识包选择；
CLI 可用 `--no-knowledge-packs`；网页默认不选知识包，需要时直接勾选本会话要用的包，不继承服务启动选择。显式 `null`、未知字段和非法组合会报错。

宿主端口、打开浏览器、遥测开关属于本次进程；权限由本次显式请求决定；任务、白名单及实验 profile 是
调用输入。实际资源在创建会话时验证并生成固定绑定，hash 和
执行进度由宿主生成，不是用户配置字段。具体规则见[配置逐字段合同](../docs/architecture/configuration-reference.md)。

CLI/Web 的本次选择只保存进相应会话。恢复读取已保存的会话合同及修订，不套用新默认，也不因缺少凭据自动切换模型。
Web 宿主按会话网关使用
启动时捕获的对应凭据。详细边界见[配置输入与恢复记录](../docs/architecture/system-framework.md#配置输入运行合同与恢复记录)。

普通产品 CLI 接受用户任务，完成范围为 `execution_evidence`。先运行 `uv run abaqus-sim-agent --list-models`，再将下面的型号占位符替换为列表中对应提供商的可选型号：

```powershell
$env:DEEPSEEK_API_KEY = "<your-key>"
New-Item -ItemType Directory -Force -Path D:\abaqus-runs\coding-demo
uv run abaqus-sim-agent `
  --model-provider deepseek `
  --model "<从 --list-models 选择的型号>" `
  --goal "创建 list_files.py，运行时列出当前目录的文件名" `
  --workspace D:\abaqus-runs\coding-demo
```

也可使用 `--goal "<任务要求>"`，与 `--request-file` 二选一。后者接受 UTF-8 文本或带 `goal` 字段的
JSON，不自动加载物理verifier。新建run时，旧`--answers <ANSWERS_JSON>`会明确选择legacy协议；
已保存的新协议run拒绝混用该旧格式，请使用`--answer-file`。切换 Go 时增加
`--model-provider opencode-go --model deepseek-v4-flash` 并配置对应密钥。

### 新默认问答：暂停与继续

模型单独调用ask_user后，宿主保存问题与history并释放运行锁，再在Console接收答复；
空输入、EOF或Ctrl+C保留`waiting_for_user`，CLI退出码为2（待答，不是模型失败）。
`--resume <STATE_DIR>`会先显示原问题，不请求模型；用户等待不计入active wall。
已保存答复或撤销的问题转为`interrupted`的待继续边界，下一次执行回填对应deferred结果，不重复提问。

非交互答复文件为UTF-8 JSON，例如`{"question_id":"<实际显示的ID>","answer":"1 mm"}`：
答复文件放在workspace之外，避免被视为未经修订的工作区变化。

```console
uv run --no-sync abaqus-sim-agent --resume <STATE_DIR> --answer-file <ANSWER_JSON>
uv run --no-sync abaqus-sim-agent --resume <STATE_DIR> --cancel-question <QUESTION_ID>
```

答复ID必须匹配该run当前问题；同ID同答复幂等，不同答复不覆盖。待答期间修改goal/配置须先显式撤销问题。
已执行Abaqus后收到新工程答复，会形成新task revision，旧产物不能验收新要求；未决runtime reconcile优先。
新普通 CLI/Web 会话使用 launch v11，绑定 Catalog、context policy、文件编码策略与实际 filesystem Skill digest；不兼容的开发期
state 显式拒绝。显式评测profile的历史证据不回填新默认。
这证明问答交接可运行，不保证模型一定识别缺参，也不构成外部Job恢复；见[ADR-0024](../docs/adr/0024-host-owned-user-question-waiting.md)。

宿主以 `abq2024` 启动 Abaqus，命令需能通过 PATH 找到；`--state-dir` 可指定工作区外的证据目录，
未指定时使用本机应用数据目录。建议始终显式指定工作区；省略时会使用 CLI 启动目录。
领域 Skill 默认来自[包内资源](../src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md)。

普通产品的 model requests、clarification、lookup/preflight 与 active wall 均为 unlimited。Abaqus/Solver 使用
`--abaqus-permission` / `--solver-permission` 配置 `deny`、`allow` 或 `allow_with_limit`，有限模式配合
`--abaqus-limit` / `--solver-limit`。三个 Abaqus runtime 默认无进程 timeout，可分别用
`--cae-timeout-seconds`、`--abaqus-python-timeout-seconds`、`--solver-timeout-seconds` 设置有限值。
HTTP timeout 默认 300 秒，`--http-retries` 默认 4 次 SDK request retry。

新建 Go / `deepseek-v4-flash` run 显式开启 thinking、reasoning effort=`high`，不向新产品或评测运行暴露
Planning，也不另加低单响应输出上限。`--http-timeout-seconds`、`--reasoning-policy` 只用于新run；其他网关
仍使用provider-default推理，不静默切换网关。新run允许先读取工作区中用户提供的事实，
确认必需输入缺失后澄清，仍禁止补造物理参数或据其推进建模/执行。

有效模型、context policy、文件编码策略与 Skill 定义绑定写入 immutable `launch.json` v11；旧开发期 launch 不迁移。
新推理策略在同一 run 内固定，模型/provider amendment 与该策略不兼容时在写入前拒绝。
旧评测profile与历史结果保持原样；新领域任务目前仍为[离线候选](../docs/plans/todo/0006-domain-task-evaluation-baseline.md)，
不能把旧零重试成绩当作新high配置成绩，也不能仅凭请求字段声称服务端已验证执行high。

### 续跑与修改任务

使用启动时打印的 state 目录恢复，不要重新传 workspace、Skill 或检索配置。新普通会话答复待答问题使用
`--answer-file`；`--answers` 仅适用于旧协议，不能与新协议混用：

```powershell
uv run abaqus-sim-agent --resume <state-directory>

uv run abaqus-sim-agent --resume <state-directory> `
  --solver-permission allow_with_limit `
  --solver-limit 2 `
  --amendment-reason "用户批准本次运行使用 Solver"

uv run abaqus-sim-agent --resume <state-directory> `
  --goal "<新的明确任务要求>" `
  --amendment-reason "用户修改任务合同"
```

runtime permission/timeout、provider/model、HTTP retry 修改写入 typed amendment，累计用量不清零。新 goal 建立 task revision，
保留对话但重新捕获 workspace snapshot、划定执行证据范围并验收。没有新 amendment 的 terminal run 不重开；
workspace 漂移、损坏的 effect ledger、同 state 并发或未决 Abaqus effect 不能通过 resume 绕过。
详见[续跑边界](../docs/adr/0020-resume-coding-runs-with-harness-step-persistence.md)与
[Amendment / Task Revision](../docs/adr/0021-use-amendments-and-task-revisions-for-continuation.md)。

## 可选功能

Go的DeepSeek适配沿用Pydantic AI原生profile，将已保存的thinking内容通过`reasoning_content`回传，
符合[DeepSeek工具调用协议](https://api-docs.deepseek.com/guides/thinking_mode/)。
2026-09-14已移除历史“不回传”覆盖；thinking enabled/high与HTTP重试配置不受影响。
旧日志与结果保留，恢复时使用已有历史内容；缺失的推理内容不补造。新评测需重新封存源码版本。

Catalog 是随应用包安装的新普通会话必选资源；其余能力默认关闭，需要显式配置。实现或离线测试通过不等于已证明真实任务收益。

| 能力 | 启用方式与边界 |
|---|---|
| Exact API Catalog | 随应用包安装并自动定位，无目录配置；默认开放精确查询、关闭目录发现；工具由通用白名单/排除列表选择，报错后的自动提示固定保留。 |
| 官方文档工具 | `--concept-index-dir <索引> --concept-retrieval-mode tool`；`search_abaqus_docs` 按需查询，也允许零查询，普通产品不设调用次数上限。 |
| Solver diagnostic advisory | `--diagnostic-retrieval-mode advisory --concept-index-dir <含BM25的索引>`；仅为符合条件的Solver诊断附加RRF证据，不替换原runtime状态。 |
| 静态反馈 | `--static-preflight-mode on` 启用固定 abqpy/BasedPyright 分析；默认 off。诊断随运行结果返回，语法错误始终在 Python runtime 前阻断。 |
| 工作区文本编码 | 文件工具严格支持 UTF-8/CP936；读取和搜索报告编码，编辑保留原编码，新文件使用 UTF-8。 |
| 有界文档查证 | `--document-policy-spec <spec.json>`；从受支持 spec 确定策略和 document tool，同一Coder执行精确引用回评与最多一次补查，使用独立后端，不同时注册旧文档工具。 |
| 纯引用编辑 | `--edit-scope-file <trusted-scope.json>`；调用方冻结原文/hash和一个Python表达式区间，仅允许引用替换，不暴露Abaqus运行工具。 |
| 本机私有领域知识包 | `--knowledge-pack <pack_id>` 可重复；只从当前用户应用数据目录发现 strict manifest，创建 run 时快照，恢复不回读源目录。Web 在新会话页显式勾选。 |

### 本机私有领域知识包

知识包不进入仓库、wheel 或公开 benchmark。宿主用 `platformdirs` 解析当前用户的数据目录；先运行下面的命令
查看本机实际根目录和已识别包：

```console
uv run --no-sync abaqus-sim-agent --list-knowledge-packs
```

在输出根目录下建立 `<pack_id>/pack.json` 和资源文件。目录名必须等于 `pack_id`；首版 manifest 示例：

```json
{
  "schema_version": "domain-knowledge-pack-v1",
  "pack_id": "flexible-electronics-fea",
  "release": "2026.09.1",
  "summary": "柔性电子结构与材料建模参考",
  "entrypoints": ["README.md"],
  "compatibility": ["Abaqus 2024", "reference-only"],
  "resources": [
    {
      "path": "README.md",
      "kind": "guide",
      "source": {
        "kind": "original",
        "title": "本机维护者整理"
      }
    }
  ]
}
```

允许的资源是 manifest 明确列出的 UTF-8 `.md`、`.txt`、`.py`、`.inp`、`.json`、`.yaml`、`.yml`；
它们只作为参考文本，不会执行或注册为 Skill。CLI 显式选择：

```powershell
uv run --no-sync abaqus-sim-agent `
  --goal "使用柔性电子参考资料完成当前建模请求" `
  --workspace D:\abaqus-runs\flexible-electronics `
  --knowledge-pack flexible-electronics-fea
```

发现包不代表自动启用；未传参数时保持关闭。新 run 会把实际 bytes 和 digest 快照到 workspace 外的 state；
resume、Web continuation 和 task revision 继续使用原快照，不能重新传 `--knowledge-pack` 换包。读取资料、
离线 schema/path 测试或单次模型观察都不证明内容专业正确、Abaqus 2024 兼容或任务物理通过。完整边界见
[知识接入](../docs/architecture/knowledge-access.md)与
[ADR-0029](../docs/adr/0029-discover-private-knowledge-from-user-data.md)。

新 CLI/Web 始终绑定 Catalog 并支持 ask_user_v1，查询 usage、去重和证据随问答恢复累计。
`discover_abaqus_api` 可通过通用工具选择启用，按已知 receiver 列出方法名称与 Card ID，
模型应继续 `lookup_abaqus_api` 读取证据；候选不会记为已读卡片。journal 保留查询、
计数与实际交付内容。历史 BM25/E5 自然语言发现实验已退出维护；当前目录工具的语义见
[知识接入与恢复边界](../docs/architecture/knowledge-access.md)。
没有Python异常类型的CAE错误，只有在宿主确认非超时失败、frame指向本次执行hash匹配的工作区脚本，
且调用可以唯一定位时才进入API提示；歧义或源码变化时不推断修复。主动查询工具是否开启不影响这条自动提示路径。

工具选择使用本次`--tools`白名单及`--exclude-tools`排除列表，名称用逗号分隔，排除优先。
不指定时保留当前可用的默认功能工具，仅默认关闭API目录发现。显式空白名单关闭功能工具；
问答、能力加载、长工具结果读取及最终输出协议继续保留。工具可见性不授予Abaqus/Solver执行权限。

```powershell
# 只保留文件读取及API查证；协议工具由程序保留。
uv run abaqus-sim-agent --tools read_file,list_directory,lookup_abaqus_api,discover_abaqus_api --model-provider <提供商> --model <型号> --goal "检查已有脚本的API用法"
# 在默认集合中关闭主动查证；运行报错后的自动提示仍然保留。
uv run abaqus-sim-agent --exclude-tools lookup_abaqus_api --model-provider <提供商> --model <型号> --goal "完成已有任务"
```

CLI/Web共用`tools`和`exclude_tools`输入，Web可在创建请求的`options`中传数组；不新增偏好文件或开关界面。
launch v11保存实际集合，恢复不接受覆盖。旧产品v10须新建会话，原文件保留；冻结评测仍使用原worker。

可选静态反馈示例（文件 allowlist 是独立的可选执行限制）：

```powershell
uv sync --extra static-preflight
New-Item -ItemType Directory -Force -Path D:\abaqus-runs\plate-static
uv run abaqus-sim-agent `
  --request-file D:\abaqus-data\request.json `
  --model-provider "<提供商>" `
  --model "<可选型号>" `
  --workspace D:\abaqus-runs\plate-static `
  --static-preflight-mode on `
  --allowed-cae-script build_model.py `
  --allowed-abaqus-python-script extract_results.py `
  --allowed-solver-input model.inp
```

首次开启静态检查时，程序从 PyPI 官方固定 URL 获取 abqpy 2024.10 wheel，校验大小和 SHA-256 后缓存；已有有效
缓存时不联网。源码只解包用于分析，不安装或执行。缓存位于 platformdirs 选择的应用缓存目录内的
`static-preflight/`；下载失败或内容不符明确报错，不伪装为 clean。
生产脚本检查不执行或导入工作区源码；warning仍交真实runtime，静态检查不替代独立verifier。
更多边界见[语法检查与静态反馈](../docs/adr/0038-unify-runtime-syntax-and-static-feedback.md)与
[纯编辑合同](../docs/adr/0023-constrain-explicit-reference-edits-before-writing.md)。

`bounded_v1` 未获行为资格，保持默认off；spec路径相对spec文件解析，resume复用已保存绑定，不能重绑检索配置。
新评分模式已将任务结果、必要约束、协议遵循和终止原因分栏，并移除额外的低单次输出上限；
这不改写旧批次分数。见[查证实现与原批次](../docs/plans/done/0003-bounded-evidence-use-in-coder.md)及
[测评校准](../docs/plans/done/0004-evaluation-validity-and-staged-path.md)。
源码清理后，此前封存的阶段B spec不再匹配当前源码；未来实验须重新核对依赖并生成新seal，不能直接沿用旧spec。

### 构建知识资源

官方 HTML、下载模型、abqpy wheel、索引和运行产物不随仓库分发。当前文档语料为 Abaqus 2026 FD02，
不能视作 Abaqus 2024 API兼容证明；检索 `found` 也不等于相关或充分。

构建、查询本地文档：

```powershell
uv run python -m abaqus_sim_agent.retrieval build `
  --docs-root D:\SIMULIA\Documentation\2026LE --index-dir var\rag
uv run python -m abaqus_sim_agent.retrieval build-bm25 --index-dir var\rag
uv run python -m abaqus_sim_agent.retrieval search `
  --index-dir var\rag --mode hybrid "plane stress reduced integration quadrilateral"
```

Catalog资源通过单一构建命令维护，由`pyproject.toml`的`[project.scripts]`注册；普通CLI/Web自动使用包内Catalog，无需手动构建。输出须为新目录，不覆盖已封存资源：

```powershell
uv run abaqus-sim-agent-catalog `
  --runtime-release 2024 `
  --docs-root D:\SIMULIA\Documentation\2026LE `
  --abqpy-source var\api-catalog\sources\abqpy-2024.10-py3-none-any.whl `
  --abqpy-version 2024.10 `
  --runtime-notes benchmarks\api_catalog\abaqus_2024_runtime_notes.jsonl `
  --output-dir var\api-catalog\local-build
```

final15 是历史实验的不可变 Catalog；[final15 绑定](../benchmarks/api_catalog/abaqus_api_catalog_final15_20260828.json)
用于复核原批次。上面的命令使用当前修订的 runtime notes，不能声称复现 final15 hash 或旧成绩；
说明修订与已有目录的关系见[知识接入](../docs/architecture/knowledge-access.md)。

Catalog命令只负责构建，不再提供查询、评分或语义检索子命令。精确查询由`ApiCatalog.lookup()`及Agent工具调用，
离线质量校验复用`api_catalog.evaluation`和对应工程测试。
新建Catalog会保留abqpy方法docstring；lookup可用`Part.setElementType.__doc__`按需读取原文，
参数查询仍使用`Part.setElementType.regions`。旧目录保持可读，不自动补写说明。
官方对象用途、参数表格和多来源定义仍随卡片保留。旧语义发现、意图改写及API查证实验的报告保持原文，
历史源码与复现边界见[测评入口](../benchmarks/README.md#已退出维护的实验代码)；当前产品接线见[知识接入](../docs/architecture/knowledge-access.md)。

检索评测、当前证据基线和已知缺口统一见[RAG索引](../benchmarks/rag/README.md)；
独立服务参数可通过 `uv run python -m abaqus_sim_agent.evaluation.runtime_evidence_service --help` 查看；
它需要已有且hash匹配的索引、ranker与scope配置，冷启动成本见[服务记录](../benchmarks/rag/runtime_evidence_service_v1_20260912.md)。

## 按需查看本地 Phoenix Trace

Phoenix 追踪 SDK 随产品安装，CLI、Web 和下列评测入口加 `--phoenix` 才启用。默认关闭，无需账号或 Token。
测评 CLI 会自动启动本次 Phoenix 服务，等待就绪后再执行；记录和追踪收尾后关闭服务、保留数据库。
无需另外启动服务或设置 collector 地址，端口固定为6006；已有服务占用该端口时明确报错，不复用或终止它。
服务端由 uvx 在隔离环境准备，固定20.16.0，不加入普通产品运行依赖，首次启用可能需要下载包。

运行期间可打开 [本机 Phoenix](http://127.0.0.1:6006)。数据库使用原生默认 `~/.phoenix/phoenix.db`，
Windows 当前用户对应 `%USERPROFILE%\.phoenix\phoenix.db`；不同批次继续使用同一数据库，历史记录不清空。
自动模式不设置项目专属目录，也不迁移先前16007验证库。仅查看已保存数据或为普通Agent排障时，可手动启动官方服务：

```powershell
$env:PHOENIX_HOST = "127.0.0.1"
$env:PHOENIX_TELEMETRY_ENABLED = "false"
$env:PHOENIX_ALLOW_EXTERNAL_RESOURCES = "false"
uvx --python 3.12 --from arize-phoenix==20.16.0 phoenix serve
```

普通 CLI/Web 不自动启动服务；开发排障需要追踪时，在服务运行后启动原产品命令并加参数：

```powershell
uv run --no-sync abaqus-sim-agent-web --phoenix --root D:\abaqus-web
```

打开 [本机 Phoenix](http://127.0.0.1:6006)，进入 `abaqus-sim-agent` 项目，查看消息、工具参数/返回、
工作段、runtime、verifier 和评测 Case。普通 CLI 同样支持 `--phoenix`；不需要追踪时省略该参数。
模型凭据与工作区仍按原方式提供，普通终端/Web 进度和 raw logs 保持原用途。

普通产品连接其他本机端口时，可设置 `PHOENIX_COLLECTOR_ENDPOINT`，例如 `http://127.0.0.1:6007`。
测评自动模式会临时把该变量统一为6006并在退出后恢复，服务进程不继承 `PHOENIX_WORKING_DIR`、数据库地址和端口覆盖。
目前只支持无鉴权的本机 HTTP 地址；不读取工作区 `.env.phoenix`，也不通过项目偏好自动开启。
普通产品的追踪导出失败时会有 SDK 诊断，业务继续。显式开启 Phoenix 的评测运行会在任务开始前检查服务并登记实验；
初始登记失败则不启动任务，中途结果写入失败保留本地结果并在收尾时报错。未送达数据没有离线队列，不自动补传。

启用会保存脚本、问题和工具读入的私有文本；已知凭据过滤不等于任意正文匿名化。关闭二进制采集，不额外
复制整个 ODB/CAE 或 raw log 文件。工作台删除会话不会删除 Phoenix 已保存的副本；通过 Phoenix 单独管理。

当前评测接线为 `evaluation.public_mechanics_suite`、`evaluation.task_baseline_runner`、
`evaluation.task_baseline_pilot`、`evaluation.simple_modeling_batch`（模块前缀均为 `abaqus_sim_agent`）。
评测组另含 Phoenix client：逐 Case 建立 Trace，将现有 assertion/score/label 复制为对应 span 的评分。
原 Case、oracle、gate 和本地报告继续负责验收。上述运行入口会自动创建或复用 Phoenix 题集，并为本次运行登记
原生 Experiment；逐题上传输出、评分及追踪关联，不需要手工录入，也不由 Phoenix 重跑任务。不同运行或恢复
保留独立实验，题目内容变化时使用另一份题集；只读 report 不新增实验，不回填旧 worker 或历史成绩。
在网页的 **Datasets & Experiments** 中查看结果；平台默认平均值不能替代本地报告的计划分母。
任务基线的实验输出仅代表任务阶段，后置验收与人工审查仍以原本地报告为准。
公开力学套件在实际运行时记录每次 attempt，报告重建将评分关联到已有追踪；命令与关联边界见
[力学结果报告](../benchmarks/coding/result_reporting.md#phoenix追踪与评分)。
实现与验证范围见[验证证据](../docs/architecture/verification-evidence.md)。各类测评的固定代码入口见[测评与证据入口](../benchmarks/README.md)。

## 当前产品边界

- 只有一个 LLM Agent。旧多 Agent Protocol、Graph State、`StructuredModelPort` 和 Mock Executor 源码
  已清理；历史决定与实验记录保留，不代表当前实现。
- 普通产品 Agent 使用 Harness 工作区 Shell 完成正常编码任务，但直接 `abaqus*` / `abq*` launcher 会被拒绝；
  Abaqus 通过 `run_cae_script`、`solver_job(action=submit|wait)`、`run_abaqus_python` 运行。宿主专用 oracle
  入口不向模型暴露。脚本仍是普通动态 Python，
  这些限制不是 OS 沙箱或不可信租户隔离。
- 工作区必须是已解析的 ASCII 路径，state/evidence 位于工作区之外。宿主约束路径、Job 名、调用预算、
  超时进程树和 artifact freshness，并保留原始 stdout/stderr bytes 与执行记录。
- 缺少会改变物理问题的输入时必须澄清，不能从模板或检索结果补造材料、尺寸、载荷等参数。
- 新普通会话通过 `ask_user(question)`交接必要输入；launch v11 可直接交付 typed 文字答复，并绑定 context policy、Skill digest、UTF-8/CP936 文件策略、必选 API Catalog 与可选私有知识快照；文件/执行请求仍返回`ready_for_verification`或`failed`；
  未迁移的固定评测profile按各自合同使用`needs_input`协议。普通 CLI 的
  `execution_evidence` 验收 fresh 交付物、Python 3.10 语法和最后一次已使用的 runtime stage，
  **不证明物理正确，也不覆盖全部自然语言语义**。固定 evaluator 才使用独立 INP/ODB `task_oracle`。
- 支持 Harness/file-backed 会话续跑、累计预算和显式任务修订；未决 Abaqus effect 停在
  `needs_reconcile`，不能自动重复提交。没有外部 Job 重新附着、完整 durable workflow、数据库控制面
  或多进程 Coordinator。

先读[架构总览](../docs/architecture/overview.md)；执行与状态边界见
[系统框架](../docs/architecture/system-framework.md)和[一次 CodingRun](../docs/architecture/flows/simulation-run.md)。

项目调研、论文阅读和固定源码分析统一见[调研索引](../docs/research/README.md)；
研究结论与已迁入的产品实现分别记录。
