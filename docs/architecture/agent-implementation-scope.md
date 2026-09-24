# Agent、协议与权限范围

## 一个真正运行的 Agent

`abaqus_2024_coder` 是 Pydantic AI Agent，由 Harness Coder 提供工作区文件能力、包内 Skills 提供领域指导。
它有 strict 输出、run-scoped 依赖、工具白名单、消息/工具 trace 和独立测试；修复时宿主再次调用同一个
Coder，不构成多 Agent。Runner、verifier、evaluator 与用户问答组件都是确定性组件。

当前没有旧 Interpreter/Architect/Input Writer/Reviewer/Verifier/Supervisor 产品调用链。
历史取舍保留在 [ADR-0008](../adr/0008-pydantic-ai-harness-abaqus-coding-run.md)及其替代记录中。

## 模型可见能力

| 能力 | 可以做什么 | 执行边界 |
|---|---|---|
| Harness 文件与 Shell 工具 | 在 canonical workspace 读写、搜索并运行普通编码命令 | 不注册 SubAgents；普通产品保留 run-scoped Shell，直接 Abaqus launcher 被 guardrail 拒绝；固定 evaluator 按自身工具表运行 |
| 包内领域 Skill | 获取运行方式、物理澄清、诊断与停止条件 | Skill 是指导，不是兼容性或任务成功证明 |
| `run_cae_script` | 在 CAE kernel 执行工作区 Python | `.py`、路径、Abaqus permission、默认无限 timeout 与可选逐工具 allowlist |
| `solver_job` | `submit` 从已有 `.inp` 启动后台 Standard Job；`wait` 按 execution ID 等待结果 | submit 消耗一次 Solver allowance 并立即返回；wait 不重复计数，pending execution 阻止 run 终态 |
| `run_abaqus_python` | 在 vendor Python 使用 odbAccess | 工作区 `.py`、Abaqus permission、默认无限 timeout 与可选 allowlist |
| `ask_user` | 交接无法从现有事实确定的必要物理信息 | 新协议不能同轮混发问题与其他工具/final；宿主整轮拒绝后者 |
| 可选知识工具 | exact lookup、receiver discovery、文档或只读资料 | 各有显式开关；当前接线和证据类型见 [知识接入](knowledge-access.md) |

宿主的 `run_trusted_abaqus_python` 不注册给模型。冻结 evaluator 可继续声明 `run_solver_job`，普通产品
使用 `solver_job`；两者的执行结果进入同一 journal/verifier。`run_cae_script` 在 permission、static preflight 和外部进程前
解析 Python 3.10 AST，并拒绝直接出现的 `.submit(...)` / `.waitForCompletion(...)` 调用；Job 创建和
`writeInput(...)` 保持允许。该最小策略不追踪 `getattr`、`exec` 或运行时重绑定，普通动态 Python 的这些
工具限制不构成 OS 沙箱或不可信租户隔离。

## Harness 组合合同

Harness 0.33 的 `Coder` 默认提供六个工具和跨 run 存活的持久 Shell；当前产品通过公开 capability 组合接口
保留以下合同，具体装配见 [agent.py](../../src/abaqus_sim_agent/coding/agent.py)：

- 文件工具使用 `FileSystem` 的默认纯 Python 工具集与 content hashes，保留 `list_directory`、`search_files`、
  `file_info` 等入口，不依赖 Coder 新增的 ripgrep 工具。上游 `edit_file` 也接受批量 `replacements`；
  ACI 在落盘前检查依次替换后的完整候选，包括 CRLF 源文件。纯编辑 profile 仍拒绝批量替换，只允许其原有受限入口。
- Shell 显式选择 `run_command`、`start_command`、`check_command`、`stop_command`；后台命令随 run 退出清理，
  使用原有 launcher guardrail、环境过滤及 Shell 超时策略。持久 `shell` 工具不注册。
- `Planning` 按有效配置独立组合，普通产品默认关闭；显式 evaluator profile 继续使用受限 `write_plan`。
- 工具输出保留 `ToolOutputLimits` 的 spill/read-back 路径，避免上游 Coder 默认直接截断后无法回读完整结果。
  产品 1M tiered compaction 与 typed capability activation 保留机制继续由项目装配。
- 新 Coder 默认通用指令与 `RepairToolArguments` 未自动启用；问答和恢复继续由 `CodingRunService` 与
  `StepPersistence` 协作，不另外接入 `AskUser`、conversation store 或 `BackgroundTools`。

已核查 0.33 的 run-scoped Shell 仍调用 `os.killpg`，Windows 下可复现异常；FileStepStore 的同步 JSONL
追加方法仍没有并行写入锁。因此宿主补丁精确绑定 0.33，并分别验证超时/后台退出清理和并行 effect 追加。
新组合通过离线合同回归不等于真实模型行为或仿真效果已经重新验证；旧批次按冻结源码/依赖保留原工具 schema。

## 模型交接与宿主终态

| 协议 / 输出 | 宿主含义 |
|---|---|
| 新 `ask_user_v1` | `ask_user(question)` 通过原生 DeferredToolRequests 交接；宿主保存 `WaitingForUser` |
| 旧 `needs_input` | legacy strict decision，继续按旧 field_id/answer 与原澄清合同运行 |
| `ready_for_verification` | 声明非空、去重的工作区相对 manifest，申请宿主验收；不是完成声明 |
| `failed` | 当前任务无法继续，由宿主形成失败 outcome |

新普通 CLI 及其显式 Catalog 组合可使用 ask_user。旧 run、旧 Gold、旧 `--answers` 与未迁移的
Concept/ACI/document/纯编辑组合按保存合同使用 legacy；不从“新默认”推导旧 run 自动换协议。
CLI 对新会话可先读取工作区事实再提问；缺失材料、尺寸、载荷等必要物理输入仍不能补造或带缺参执行。

问题应面向用户掌握的工程事实；API owner、Set/findAt 容器、文件名等实现问题不转嫁给用户。
问答状态机保证已提问后的交接，不保证模型一定识别缺参。

产品 `execution_evidence` 逐项检查 manifest 的存在性与 freshness、fresh Python 的 3.10 语法、
每种已用 runtime 的最后一次 stage。固定 evaluator 的 `task_oracle` 才检查指定 INP/ODB 与物理结果。
两种 scope 不能互换，模型也不能直接产生最终 `CodingRunOutcome`。

## 显式约束与实验 profile

- `RuntimeScriptPolicy` 按三种入口分别限制精确相对路径；拒绝在 probe 状态、allowance 预留和 runner 前发生，
  返回 `RuntimeScriptPolicyDenied`。null 保持工作区路径边界。
- ACI 默认 off。`enforced_verified` 要求 task-owned production allowlist：写入前检查完整 Python 3.10
  候选，运行前按当前 hash 复核，再按冻结 evidence 分类 API blocker；未知 SWIG/type 仅 warning。
  checker 不可用或预算耗尽 fail-closed，旧 advisory 保留原兼容行为。
- typed probe 默认 off。显式启用时区分 production/diagnostic；conclusion/adoption 与允许 target hash
  由宿主记录，validated 不能绕过 production adoption；不自动发布经验，当前不支持其完整恢复。
- `--edit-scope-file` 冻结现有 Python 文件和单表达式范围，写前约束范围外不变，且不注册 Abaqus runtime。
  pending 写入或漂移失败关闭；它约束编辑权限，不验证 API 或物理语义。
- evaluator 的 `verify_on_budget_exhaustion` 默认关闭，显式启用时仍须由宿主完整验收；不能因预算耗尽
  就将缺失 provenance 或结果的任务标为 completed。

具体默认值、组合限制和有效配置分别由 [合同](../../src/abaqus_sim_agent/coding/contracts.py)、
[CLI](../../src/abaqus_sim_agent/coding/cli.py)、[模型配置](../../src/abaqus_sim_agent/coding/model.py)拥有。
旧 Case 的预算、allowlist、Prompt/Skill 和 oracle 不因文档或产品默认演进而改变。

## 以后增加专业 Agent 的门槛

增加 Agent 要解决已经复现、且不能由确定性检查或同一 Coder 的有界修复经济解决的问题。新角色必须具有
独立模型目标、strict typed 输入输出、窄工具权限、run-scoped 依赖、失败语义、trace 和独立测试；给函数
换角色名或让 verifier 再调用模型都不构成理由。

首选由宿主用普通 async Python 调用专业 Agent，并继续拥有预算、恢复、task revision 和终态。父 Coder
需要自主委派自包含任务时可以评估 Harness `SubAgents`，但不向子 Agent 默认继承工作区写入或三个 Abaqus
工具。只有这套控制流演变成真实状态机时才比较 `pydantic-graph`；增加第二个 Agent 本身不触发 LangGraph、
数据库或 durable runtime。Runner、产品 verifier、固定 oracle 和用户问答继续按职责判断是否为确定性组件，
不为了形成“多 Agent”数量而改成 Agent。

## 固定任务边界

from-scratch plate/bar/B31 与 verified-seed plate/beam change 都是独立固定合同。变更任务重新验证源 run
和产物 hash 后缓存种子 bytes；具体 Case 决定哪些脚本可改、哪些必须逐字节保留，独立验收新需求和回归。
suite 按各 Case 的重复 gate 通过，不能用总成功数抵消失败 Case，也不能推导任意 `.cae` 修改能力。
具体规格、结果和局限见 [验证证据入口](verification-evidence.md)。

实现与回归：[Agent 装配](../../src/abaqus_sim_agent/coding/agent.py)、
[Agent 测试](../../tests/test_coding_agent.py)、[问答测试](../../tests/test_user_questions.py)、
[ACI 测试](../../tests/test_aci.py)、[纯编辑测试](../../tests/test_edit_scope.py)。
