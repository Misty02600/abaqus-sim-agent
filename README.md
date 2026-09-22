# Abaqus Sim Agent

面向 Abaqus 2024 的本地 Coding Agent。单一 `abaqus_2024_coder` 使用 Pydantic AI Harness 在独立工作区编写
普通 Abaqus Python，按请求范围通过受控工具完成 CAE 建模、Standard 求解或 ODB 提取，再由宿主验收交付物。
也可以直接提问、讨论或请求一个局部步骤；纯文字回答不要求生成文件或执行仿真。

项目服务于 AI Agent / LLM 应用工程方向的秋招准备，重点是可验证的代码路径、类型合同、工具权限、
外部进程副作用和评测；不把依赖、设计稿或单次实验包装成通用 CAE 产品能力。

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
- 新普通会话通过 `ask_user(question)`交接必要输入；launch v7 可直接交付 typed 文字答复，并绑定 context policy、Skill digest 与可选私有知识快照；文件/执行请求仍返回`ready_for_verification`或`failed`；
  新CLI可显式组合API Catalog；旧run、Gold及其他未迁移profile保留`needs_input`协议。普通 CLI 的
  `execution_evidence` 验收 fresh 交付物、Python 3.10 语法和最后一次已使用的 runtime stage，
  **不证明物理正确，也不覆盖全部自然语言语义**。固定 evaluator 才使用独立 INP/ODB `task_oracle`。
- 支持 Harness/file-backed 会话续跑、累计预算和显式任务修订；未决 Abaqus effect 停在
  `needs_reconcile`，不能自动重复提交。没有外部 Job 重新附着、完整 durable workflow、数据库控制面
  或多进程 Coordinator。

先读[架构总览](docs/architecture/overview.md)；执行与状态边界见
[系统框架](docs/architecture/system-framework.md)和[一次 CodingRun](docs/architecture/flows/simulation-run.md)。

项目调研、论文阅读和固定源码分析统一见[调研索引](docs/research/README.md)；
研究结论与已迁入的产品实现分别记录。

## 安装与开发

宿主使用 Python 3.12，`uv` 将环境放在项目内 `.venv`。实际仿真还需要 Windows、本机 Abaqus 2024
和可用许可证；Abaqus vendor Python 3.10 与宿主解释器分开。

```console
uv sync --all-groups
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync basedpyright
uv run --no-sync abaqus-sim-agent --help
```

Windows 的 Just recipes 需要 PowerShell 7.4+（`pwsh`）；直接运行上述 uv 命令没有该要求。
持证集成测试需要先显式设置 `ABAQUS_LICENSED_TESTS=1`，再选择 `pytest -m abaqus`；这会使用本机许可证。
不要把普通离线回归通过当作真实 Abaqus 闭环通过。

## 本地 Web 工作台

已接入 FastAPI、React/TypeScript 和 assistant-ui ExternalStore。页面接受任意问题、解释请求、局部步骤或完整任务，支持文字答复、必要澄清、显式继续、
工具记录、宿主验收与用量，以及文本、已有 PNG、当前CAE工具产生的fresh OBJ几何预览和下载。CLI 与 Web 共用同一个 CodingRunService，
不增加 Agent、运行工具或物理成功判定。当前工程验收使用确定性模型；两条新真实 Web 仿真主线仍待独立执行。

源码运行需要 Node.js 22+ 构建一次；构建后的 wheel 自带页面，运行它无需 Node：

```powershell
uv sync --locked --all-groups --extra web
uv run --no-sync python scripts/build_web.py
uv run --no-sync abaqus-sim-agent-web --root D:\abaqus-web --model-provider opencode-go
```

启动前在宿主环境配置 `OPENCODE_API_KEY`；默认官方网关使用 `DEEPSEEK_API_KEY`。打开终端打印的完整
本地会话链接。根目录必须是 ASCII 路径，下面分开保存 `workspaces/`、`states/`、`registry/` 和临时缓存；
每个任务从空工作区开始。普通产品不限制模型请求、提问、低成本工具或 active wall；Abaqus 默认允许，
Solver 默认禁止，创建会话时可改为允许或允许有限次数。Go/Flash 使用 enabled/high、HTTP 300 秒和 4 次
SDK request retry，普通产品默认关闭 Planning。

页面已支持基础多轮对话：首次点击“发送”直接开始，回复后可在同一会话继续追问或追加要求；
Enter发送、Shift+Enter换行，草稿按会话保存在当前浏览器会话中。历史从宿主恢复，追加消息沿用原工作区和
累计用量。当前处理期间可编辑草稿，处理结束后发送。回复支持流式逐步显示和复制，会话可重命名；“停止回复”只在模型请求阶段可用，
停止后保留已收到的内容，可继续或发送新消息。`solver_job(action=submit)` 在 Web 中作为独立
`ToolExecution` 运行并立即把 execution ID 返回模型；模型可继续独立工作，需要结果时再调用 `action=wait`。
状态栏显示 Job、已用时间和当前状态，运行详情保留 PID、退出码与错误。当前宿主仍持有进程控制句柄时可以
取消这一次 Solver；当前 Agent run 结束前必须等待已提交的 Solver。
消息和澄清答复先由宿主持久化，再调度执行；若已保存但未启动，页面提示显式继续。普通Web追加消息保存到
`thread.json`并作为同一CodingRun history上的continuation执行，不推进task revision；每条新消息仍以提交前的
workspace/execution状态划定本段fresh evidence。显式修改任务合同的CLI `--goal` amendment继续建立task revision。

界面采用Pi启发的深色紧凑布局：会话与文件位于左栏，对话保持窄正文宽度，运行详情按需打开；工具调用先显示
目标、阶段、耗时、超时、警告和文件变更摘要，完整输入/返回折叠保留。底栏直接显示宿主阶段、连接、模型、
推理配置、累计用量、wall time及当前允许操作。会话标题可搜索；`Ctrl/Cmd+K`打开命令面板，输入框外可用
`N`新建、`R`刷新。视觉组件和文件图标的Pi/Catppuccin来源见[复用记录](webui/THIRD_PARTY_REUSE.md)。

新普通CLI/Web会话可仅回答问题，不要求生成文件或调用Abaqus；页面显示“已回答”，不赋予执行验收范围。
只做一个环节时，不要求其他环节的材料、载荷或边界条件；只在当前请求确实依赖缺失事实时澄清。
开发期旧产品 state 不迁移；固定 Gold、trial 与报告仍保留原合同和证据。见
[ADR-0030](docs/adr/0030-drop-pre-release-coding-run-state-compatibility.md)。

当前只接受本机单用户访问，每个服务只有一个主动工作段，忙时拒绝第二个执行。浏览器断开不停止任务；
正常关闭服务会等待当前模型工作段；页面的停止按钮会关闭当前模型请求并保存中断状态。Solver submit
立即返回 execution ID；模型调用 wait 后普通 await 终态，等待不消耗 token 或 active wall，也不重复提交。
服务在 Solver 运行中关闭时会终止本机控制句柄并转为 `needs_reconcile`；重启后不按 PID/Job 重新附着，
也不会自动重跑。本版没有文件上传。文本预览上限256 KiB，
网页下载上限200 MiB；更大文件从本地工作区读取。HTML/SVG仅作为附件下载。

接口与状态职责见[架构总览](docs/architecture/overview.md)和[系统框架](docs/architecture/system-framework.md)。
前端开发与第三方来源见 [webui/README.md](webui/README.md)。`/third-party-notices` 提供构建随附的依赖声明。

## 运行 CodingRun

默认网关为 DeepSeek 官方 API，读取 `DEEPSEEK_API_KEY`；`opencode-go` / `opencode-zen` 读取
`OPENCODE_API_KEY`，不会隐藏切换 Provider。模型 ID 可由 `--model` 显式指定，默认
`deepseek-v4-flash`；免费 Zen ID 与付费网关 ID 不能互换。Go adapter 为同一 run/resume 提供稳定、
经 SHA-256 处理的 session 标识和项目 User-Agent。

下面的板拉伸请求要求先询问厚度。使用普通产品 CLI 运行它，仍只获得 `execution_evidence`，
不是后面的 Gold 物理验收：

```powershell
$env:DEEPSEEK_API_KEY = "<your-key>"
New-Item -ItemType Directory -Force -Path D:\abaqus-runs\plate-interactive
uv run abaqus-sim-agent `
  --request-file benchmarks\coding\plate_tension_request.json `
  --workspace D:\abaqus-runs\plate-interactive
```

也可使用 `--goal "<任务要求>"`，与 `--request-file` 二选一。后者接受 UTF-8 文本或带 `goal` 字段的
JSON，并不自动加载 Gold 的 verifier 或预算。非交互澄清使用
新建run时，旧`--answers benchmarks/coding/plate_tension_answers.json`会明确选择legacy协议；
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
新普通 CLI/Web 会话使用 launch v7，绑定 context policy 与实际 filesystem Skill digest；不兼容的开发期
state 显式拒绝。旧 Gold 及显式评测 profile 的历史证据保留，不回填新默认。
这证明问答交接可运行，不保证模型一定识别缺参，也不构成外部Job恢复；见[ADR-0024](docs/adr/0024-host-owned-user-question-waiting.md)。

`--launcher` 可覆盖默认 `D:\SIMULIA\Commands\abq2024.bat`；`--state-dir` 可指定工作区外的证据目录，
未指定时使用本机应用数据目录。建议始终显式指定工作区；省略时会使用 CLI 启动目录。
领域 Skill 默认来自[包内资源](src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md)。

普通产品的 model requests、clarification、lookup/preflight 与 active wall 均为 unlimited。Abaqus/Solver 使用
`--abaqus-permission` / `--solver-permission` 配置 `deny`、`allow` 或 `allow_with_limit`，有限模式配合
`--abaqus-limit` / `--solver-limit`。三个 Abaqus runtime 默认无进程 timeout，可分别用
`--cae-timeout-seconds`、`--abaqus-python-timeout-seconds`、`--solver-timeout-seconds` 设置有限值。
HTTP timeout 默认 300 秒，`--http-retries` 默认 4 次 SDK request retry。

新建 Go / `deepseek-v4-flash` run 显式开启 thinking、reasoning effort=`high`，普通产品默认关闭 Planning，
不另加低单响应输出上限。`--http-timeout-seconds`、`--reasoning-policy`、`--planning/--no-planning`
只用于新run；其他网关仍使用provider-default推理，不静默切换网关。新run允许先读取工作区中用户提供的事实，
确认必需输入缺失后澄清，仍禁止补造物理参数或据其推进建模/执行。

有效模型、context policy 与 Skill 定义绑定写入 immutable `launch.json` v7；旧开发期 launch 不迁移。
新推理策略在同一 run 内固定，模型/provider amendment 与该策略不兼容时在写入前拒绝。
旧评测profile与历史结果保持原样；新领域任务目前仍为[离线候选](docs/plans/todo/0006-domain-task-evaluation-baseline.md)，
不能把旧零重试成绩当作新high配置成绩，也不能仅凭请求字段声称服务端已验证执行high。

### 续跑与修改任务

使用启动时打印的 state 目录恢复，不要重新传 workspace、Skill 或检索配置；需要新的 scripted answer
时仍可提供 `--answers`：

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
详见[续跑边界](docs/adr/0020-resume-coding-runs-with-harness-step-persistence.md)与
[Amendment / Task Revision](docs/adr/0021-use-amendments-and-task-revisions-for-continuation.md)。

## 可选功能

Go的DeepSeek适配沿用Pydantic AI原生profile，将已保存的thinking内容通过`reasoning_content`回传，
符合[DeepSeek工具调用协议](https://api-docs.deepseek.com/guides/thinking_mode/)。
2026-09-14已移除历史“不回传”覆盖；thinking enabled/high与HTTP重试配置不受影响。
旧日志与结果保留，恢复时使用已有历史内容；缺失的推理内容不补造。新评测需重新封存源码版本。

以下能力默认关闭，需要显式配置；实现或离线测试通过不等于已证明真实任务收益。

| 能力 | 启用方式与边界 |
|---|---|
| Exact API Catalog | `--api-catalog-dir <目录>` 配合 `--api-catalog-mode full`、`full_with_discovery`、`automatic_verified_only` 或 `automatic_exact_signature`；只传目录时默认 full。分别提供主动精确查证、额外的 receiver 方法目录、错误触发 verified note 或紧凑签名。 |
| 官方文档工具 | `--concept-index-dir <索引> --concept-retrieval-mode tool`；`search_abaqus_docs` 按需查询，也允许零查询，普通产品不设调用次数上限。 |
| Solver diagnostic advisory | `--diagnostic-retrieval-mode advisory --concept-index-dir <含BM25的索引>`；仅为符合条件的Solver诊断附加RRF证据，不替换原runtime状态。 |
| 分级静态检查 | `--static-preflight-mode advisory` 或 `enforced_verified`；后者要求固定abqpy wheel、外部cache与task-owned生产脚本allowlist，拒绝发生在Abaqus预算前。 |
| 有界文档查证 | `--document-policy-mode bounded_v1 --document-policy-spec <spec.json> --concept-retrieval-mode tool`；同一Coder执行精确引用回评与最多一次补查，使用spec中的独立后端，不同时注册旧文档工具。 |
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
[知识接入](docs/architecture/knowledge-access.md)与
[ADR-0029](docs/adr/0029-discover-private-knowledge-from-user-data.md)。

新 CLI 单独启用 Catalog 时支持 ask_user_v1，查询 usage、去重和证据随问答恢复累计。
`full_with_discovery` 已接入产品 CLI 和恢复：`discover_abaqus_api` 按已知 receiver 列出方法名称与 Card ID，
模型应继续 `lookup_abaqus_api` 读取证据；候选不会记为已读卡片。journal 保留查询、
计数与实际交付内容。该目录工具不是下面的 BM25/E5 自然语言发现实验，详见
[知识接入与恢复边界](docs/architecture/knowledge-access.md)。
没有Python异常类型的CAE错误，只有在宿主确认非超时失败、frame指向本次执行hash匹配的工作区脚本，
且调用可以唯一定位时才进入API提示；歧义或源码变化时不推断修复。Catalog仍需显式启用。

ACI 示例：

```powershell
uv sync --extra static-preflight
New-Item -ItemType Directory -Force -Path D:\abaqus-runs\plate-static
uv run abaqus-sim-agent `
  --request-file benchmarks\coding\plate_tension_request.json `
  --workspace D:\abaqus-runs\plate-static `
  --static-preflight-mode enforced_verified `
  --abqpy-static-wheel var\api-catalog\sources\abqpy-2024.10-py3-none-any.whl `
  --static-preflight-cache-dir D:\abaqus-runs\static-preflight-cache `
  --allowed-cae-script build_model.py `
  --allowed-abaqus-python-script extract_results.py `
  --allowed-solver-input plate_tension.inp
```

生产脚本检查不执行或导入工作区源码；warning仍交真实runtime，静态检查不替代独立verifier。
更多边界见[ACI](docs/adr/0018-enforce-verified-abaqus-aci.md)与
[纯编辑合同](docs/adr/0023-constrain-explicit-reference-edits-before-writing.md)。

`bounded_v1` 未获行为资格，保持默认off；spec路径相对spec文件解析，resume复用已保存绑定，不能重绑检索配置。
新评分模式已将任务结果、必要约束、协议遵循和终止原因分栏，并移除额外的低单次输出上限；
这不改写旧批次分数。见[查证实现与原批次](docs/plans/done/0003-bounded-evidence-use-in-coder.md)及
[测评校准](docs/plans/done/0004-evaluation-validity-and-staged-path.md)。
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

构建新的本地Catalog目录，再做精确查询；不要覆盖已封存的目录：

```powershell
uv run python -m abaqus_sim_agent.api_catalog build `
  --runtime-release 2024 `
  --docs-root D:\SIMULIA\Documentation\2026LE `
  --abqpy-source var\api-catalog\sources\abqpy-2024.10-py3-none-any.whl `
  --abqpy-version 2024.10 `
  --runtime-notes benchmarks\api_catalog\abaqus_2024_runtime_notes.jsonl `
  --output-dir var\api-catalog\local-build

uv run python -m abaqus_sim_agent.api_catalog lookup `
  --catalog-dir var\api-catalog\local-build Model.SurfaceTraction.directionVector
```

当前推荐的历史实验Catalog为immutable final15，但重新构建不能自动声称复现该hash；
使用前核对[final15绑定](benchmarks/api_catalog/abaqus_api_catalog_final15_20260828.json)。

新建Catalog会保留abqpy方法docstring；已有lookup可用`Part.setElementType.__doc__`按需读取原文，
参数查询仍使用`Part.setElementType.regions`。旧目录保持可读，不自动补写说明。
独立 BM25/E5 自然语言发现支持以下离线命令；它尚未成为普通 CLI 的模型工具，与产品 receiver 目录不同：

```powershell
uv run --no-sync python -m abaqus_sim_agent.api_catalog discover `
  --catalog-dir var/api-catalog/abaqus-2024-docstrings-v1 `
  --index-dir var/api-catalog/discovery-docstrings-v1-e5 `
  --strategy dense --encoder-dir var/models/multilingual-e5-base-d128750 `
  "将指定有限元单元类型赋给零件中的几何区域"
```

`--strategy bm25`不需要encoder；`index`构建新索引，`read`按候选card_id读取绑定的完整卡片。
这些命令需要本地构建资产，不自动下载模型。候选不证明适用性或物理正确性。
复核命令、固定模型文件绑定和失败分析见[1B/2A报告](benchmarks/api_catalog/docstrings_discovery_v1_20260914.md)。

v2 保留官方对象用途与方法 Table data，支持 `index --projection api-discovery-view-v2` 显式构建，
旧 v1 仍可读取。内容补齐与原题开发回放见 [v2 报告](benchmarks/api_catalog/reference_context_discovery_v2_20260914.md)，
不把局部命中改善当作中文范围可用或真实任务收益。

离线发现还可显式使用`discover --intent-file <JSON>`，JSON包含宿主保留的`original_request`和用于排名的
`operation`。`--lexical-profile english-snowball-v1`启用英文停用词/词干处理，仅作用于BM25；默认legacy和Exact定位不变。
原需求会完整回传供采用前核对，不代表自动验证约束。该意图搜索仍是独立能力，见
[操作意图与词法处理报告](benchmarks/api_catalog/operation_intent_lexical_v1_20260914.md)。

API 意图准备、评测专用语义发现和历史失败反馈各有独立实验，详细分数、解释错误与 Provider 中断保留在
[扩展测评](benchmarks/api_catalog/intent_suite_expansion_20260914.md)、
[API 方案小闭环](benchmarks/api_catalog/api_discovery_loop_v1_20260915.md)及
[历史失败查证](benchmarks/api_catalog/historical_api_feedback_v1_20260915.md)。这些零 Abaqus 观察不能称仿真通过。
产品当前接线以 [知识接入](docs/architecture/knowledge-access.md)为准，实验历史不替代当前接口说明。

检索评测、当前证据基线和已知缺口统一见[RAG索引](benchmarks/rag/README.md)；
独立服务参数可通过 `uv run python -m abaqus_sim_agent.evaluation.runtime_evidence_service --help` 查看；
它需要已有且hash匹配的索引、ranker与scope配置，冷启动成本见[服务记录](benchmarks/rag/runtime_evidence_service_v1_20260912.md)。

## 项目调研与论文阅读

[调研索引](docs/research/README.md)统一组织项目横向比较、相关论文与阅读记录，以及 AbaqusAgent 论文与源码详读。
各篇保留来源、阅读状态和适用边界；采用情况以当前架构与评测证据为准。

## 测试与真实任务评测

默认 `pytest` 只运行当前产品主路径与核心合同；检索/证据处理、可选策略等低层组件回归，以及评测基础设施和
持证 Abaqus 集成测试显式分层，避免日常回归反复执行与本次产品改动无关的内部算法或冻结试验：

```console
uv run --no-sync pytest -q
uv run --no-sync pytest -q -m component
uv run --no-sync pytest -q -m evaluation
```

持证集成测试需要先设置 `ABAQUS_LICENSED_TESTS=1`，再运行 `pytest -m abaqus`；这会使用本机许可证。
`component` 仍由 CI 和发布检查显式运行；`evaluation` 可独立复核冻结 evaluator、benchmark 与研究回归。
一次性实验的历史证据由版本化 benchmark/report 与 Git 历史保留，不额外维护一套历史 pytest。
真实模型行为测试与持证任务另行运行，
分别记录实际调用和验收结果。

评测系统本身的离线自测使用Pydantic Evals：固定12个假任务、轮内并发1/2、逐例落盘与模拟审查，
不请求模型或启动Abaqus。在已安装eval依赖的项目环境中运行（demo目标目录必须尚不存在）：

```powershell
uv run --no-sync python -m abaqus_sim_agent.evaluation.task_baseline_runner demo var/audits/offline-selftest-example --max-concurrency 2
uv run --no-sync python -m abaqus_sim_agent.evaluation.task_baseline_runner summarize var/audits/offline-selftest-example
```

`review <root> --slot <slot> --review-file <json>`可导入`source=fixture`的模拟审查并保留前版；
这里只验证补评/汇总，不接受真实人工或物理通过声明。没有审查的自动通过结果仍为未决。
四张任务卡已通过`run_service_fixture`接入真实CodingRunService，并用FunctionModel和假进程验证；
可运行`uv run --no-sync pytest -q tests/test_task_baseline_service.py`复核，不请求模型或启动Abaqus。
原产品结果与后置评测分开，live入口及人工物理审查仍见[PLAN-0006](docs/plans/todo/0006-domain-task-evaluation-baseline.md)。

单条T1开发试点入口为`abaqus_sim_agent.evaluation.task_baseline_pilot`：`prepare <spec.json> --run-root <new-root>`
只生成配置，不调用模型/Abaqus；`run <spec.json> --approve-spec-sha256 <digest>`须用户先批准，会实际调用Go和Abaqus，
并在证据可用时最多独立读取一次ODB。`verify <root>`复用已有后置结果，不重跑Agent；有未完成的读取预留也不重试。
`review-template <root>`生成空白人工模板，再通过上述runner的`review`导入source=human且绑定原结果摘要的记录。
该live schema只允许一条串行T1，不混入offline fixture或直接扩成正式十二条；未签署前不报完整通过。
新试点在`http-attempts.json`记录SDK解析前的响应结构摘要（无正文、参数原文、推理内容或凭据），
只解析不超过1 MiB的响应、最多记录16个工具；这是诊断上限，不是模型输出上限。
已确认的工具参数校验纠错耗尽在外围评测中记为`tool_arguments`，不代表已确定模型或网关责任，
也不改变产品的interrupted终态、HTTP/工具重试配置或旧报告。新代码运行前须重新准备并批准spec。
`logical_requests`保留框架usage口径；新增`model_requests_started`按SDK首次HTTP发送计数，包含最终失败请求，
`http_attempts`包含重试。旧日志缺少重试序号或遇到重定向等不能可靠归并的情况，已发起数标未知，不按body hash推算。
交互评分独立于任务终态：已知先运行后澄清的违规不能被预算失败掩盖；开始工作前的Provider中断仍可保持未知。
子任务取消会取消整个评测批次：保留已完成结果和活动任务的中断凭据，后续槽位不再启动，
向调用方传播取消而不生成空报表项；不会自动续跑或继续后置取数。

当前重点是简单完整建模与缺参澄清，截面修改/警告修复不进入下一批。固定双任务入口
`abaqus_sim_agent.evaluation.simple_modeling_batch`接受`prepare <spec.json> --run-root <new-root>`，
新spec v3显式绑定ask_user_v1和human_confirmed答复模式，封存缺厚度板和完整三维杆各一次；
旧v1保持legacy，旧v2保持原脚本答复fixture，不静默改写历史配置。
`run <spec.json> --approve-spec-sha256 <digest>`必须先获得用户批准。
两条任务模型/文件阶段并发2，Abaqus进程在本批次内串行1，排队时间记录且计入任务wall。
全部Agent结束后每题最多独立取数一次；`verify <root>`仅复用已有结果或消费已批准、尚未使用的取数预算。
人工审查仍由共享runner的`review`入口导入，不能以自动数值通过冒充完整验收。

新评测遇到ask_user即保存waiting_for_user并结束本次活动段，不按关键词识别问题、不自动发送1 mm；
另一条任务可以继续。人工读取完整问题后，只确认任务卡已声明的答案（本题均匀板厚1 mm），
通过产品`--resume <STATE_DIR> --answer-file <ANSWER_JSON>`提交精确question_id和answer；
答复文件放在workspace之外。额外参数、建模建议或重复提问不自动回答，先记录并决定如何处理。
人工取答不计active wall，恢复仍累计原请求和运行预算；没有新增批次轮询器或自然语言判题器。
批次已落盘的trial/summary不因产品续跑而改写，续跑须单独授权并另存关联记录；不将其回填为原批次通过。

固定Gold包含二维板CPS4R、三维杆C3D8R和B31梁；另有verified-seed截面、载荷、几何与材料变更。
它们只证明各自的固定合同。三次至少两次通过是smoke gate，不是稳定成功率；
参考脚本通过、Agent通过、检索命中和物理验收分别报告。

显式启动持证plate Gold（会调用模型和Abaqus，并为各次尝试创建独立空工作区）：

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.plate_tension `
  --run-root D:\abaqus-runs\plate-eval
```

已有模型变更必须使用一个经当前合同重新校验的成功source run，仅从缓存复制两份脚本：

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.model_change_suite `
  --run-root D:\abaqus-runs\plate-model-change `
  --source-run-dir D:\abaqus-runs\plate-eval\<completed-run-id> `
  --case-id dual_material --model-provider opencode-go
```

完整运行命令、每个profile的真实结果、失败样本和限制统一保存在以下入口，不在根README重复实验流水：

- [Coding / Gold / verified-seed评测](benchmarks/coding/README.md)：含B31 v7、截面变更、弯曲合同v2、plate suite和API修复。
- [RAG / BM25 / Dense / RRF评测](benchmarks/rag/README.md)：含开发集、已消费held-out、复现与当前证据基线。
- [文档行为阶段A](benchmarks/coding/behavior_measurement_phase_a_20260912.json)：离线校准；不代表阶段B/C已执行。
- [API Catalog证据](benchmarks/api_catalog/abaqus_api_catalog_final15_20260828.json)：固定构建来源、runtime notes与benchmark。
- [架构决定索引](docs/adr/README.md)、[当前实现范围](docs/architecture/agent-implementation-scope.md)、
  [技术栈与讲解边界](docs/architecture/technology-stack.md)。

保留原始失败报告、版本绑定、withdrawal/consumption记录和独立oracle。新的评分解释应另立版本；
历史seal不因当前源码清理而被改写，也不能继续作为当前源码的封存证明。

## 维护与发布

模板更新：在干净工作树运行 `just update-template`；出现Copier冲突后先解决冲突并删除已处理的
`.rej`，再运行 `just finish-template-update`，不要重新启动更新。后者刷新lock并执行检查；
模板PR也应执行它并携带相应lock变更。模板身份/profile变化需单独迁移。

本仓库维护并提交 `uv.lock`；Renovate配置不等于Hosted App已启用。
CI执行Ruff、BasedPyright和pytest，具体规则以[工作流](.github/workflows/ci.yml)与[Just recipes](justfile)为准。

发布须在源码通过CI后于 `main` 显式运行 `just bump`：该命令创建版本提交和annotated tag，并原子推送到
`origin`，不是只读检查。tag触发[Release工作流](.github/workflows/release.yml)，检查并构建wheel/sdist，
附到GitHub Release，不发布PyPI；发行包不包含Abaqus、许可证、模型凭据或本机知识索引。

本项目使用[MIT许可证](LICENSE)，保持独立Git历史。借鉴或改编上游内容须记录仓库、基线、原文件和修改，
并履行第三方许可证义务，见[来源与复用边界](docs/adr/0005-traceable-upstream-reuse.md)。
