# 开发与评测

[返回项目首页](../README.md) · [使用指南](usage.md)

本页说明离线检查、真实任务评测和维护发布。首次启动见[快速开始](../README.md#快速开始)，
前端构建和开发服务见 [webui/README.md](../webui/README.md)。

## 安装与开发

宿主使用 Python 3.12，`uv` 将环境放在项目内 `.venv`。实际仿真还需要 Windows、本机 Abaqus 2024
和可用许可证；Abaqus vendor Python 3.10 与宿主解释器分开。

```console
uv sync --locked --all-groups --extra web
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync basedpyright
uv run --no-sync abaqus-sim-agent --help
```

Windows 的 Just recipes 需要 PowerShell 7.4+（`pwsh`）；直接运行上述 uv 命令没有该要求。
持证集成测试需要先显式设置 `ABAQUS_LICENSED_TESTS=1`，再选择 `pytest -m abaqus`；这会使用本机许可证。
不要把普通离线回归通过当作真实 Abaqus 闭环通过。

## 测试与真实任务评测

默认 `pytest`／`just test` 运行全部适用的离线工程测试，包括测评器、评分、数据加载和批次管理的代码回归。
按被测行为维护用例，不再由文件名清单划分默认、component 和 evaluation 集合。需要局部反馈时直接选择文件：

```console
uv run --no-sync pytest -q
uv run --no-sync pytest -q tests/test_web_workbench.py
uv run --no-sync pytest -q tests/test_task_baseline_service.py
```

持证集成测试需要先设置 `ABAQUS_LICENSED_TESTS=1`，再运行 `pytest -m abaqus`；这会使用本机许可证。
依赖本机私有 abqpy 源码等资产的用例标记 `local_assets`，可显式选择
`pytest -m "local_assets and not abaqus and not live_model"`；`live_model` 标记的用例也不进入默认集。
覆盖默认 `-m` 表达式时，应写全本次需要的资源排除条件。
CI 和发布检查使用同一默认离线集合。真实 Agent 能力／检索效果测评继续使用各自的显式入口。
历史报告与 seal 保留原 bytes；仅复述旧批次成绩的断言不再约束日常代码回归。
调用当前代码验证容差、格式兼容、篡改拒绝和失败处理的历史样本仍可作为工程测试输入。

测试默认设置 `ALLOW_MODEL_REQUESTS=False`，清除继承的 Provider 密钥，并将默认私有知识包位置指向临时目录。
HTTPX 的真实网络传输只放行本机回环地址。使用 `MockTransport` 验证真实 SDK 协议的测试须显式启用
`mock_model_requests` fixture；该 fixture 放行模型接口但保留外部 HTTP 拦截。子进程不自动继承 Python
monkeypatch，相关测试须显式使用临时工作区、测试环境与假模型／固定进程脚本。
共享的假进程、Web 应用工厂与 HTTP 操作放在 `tests/support/`，新测试从这里复用，避免从其他测试模块
导入 helper。只用于单个场景的辅助函数留在对应测试文件；公共隔离 fixture 位于 `tests/conftest.py`。
Web 测试优先覆盖 HTTP 接线、会话持久化和执行生命周期；纯宿主条件直接在宿主测试中验证。
同一正常流程的前置步骤可并入完整场景，停止、删除、取消等不同副作用仍分别覆盖。
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
原产品结果与后置评测分开，live入口及人工物理审查仍见[PLAN-0006](../docs/plans/todo/0006-domain-task-evaluation-baseline.md)。

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

领域仿真统一使用20个公开力学Case，按物理问题计数，输入变体不增加题目权重。旧plate/bar/B31 Gold独立评测已删除。
案例、数值口径与运行条件见[案例清单](../benchmarks/coding/domain_mechanics_inventory.md)；参考脚本、真实Agent与事后修复分别报告。

已有模型变更必须使用一个经当前合同重新校验的成功source run，仅从缓存复制两份脚本：

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.model_change_suite `
  --run-root D:\abaqus-runs\plate-model-change `
  --source-run-dir D:\abaqus-runs\plate-eval\<completed-run-id> `
  --case-id dual_material --model-provider opencode-go
```

完整运行命令、每个profile的真实结果、失败样本和限制统一保存在以下入口，不在根README重复实验流水：

- [领域仿真 / verified-seed / 修复评测](../benchmarks/coding/README.md)：当前运行入口与独立证据。
- [RAG / BM25 / Dense / RRF评测](../benchmarks/rag/README.md)：含开发集、已消费held-out、复现与当前证据基线。
- [文档行为阶段A](../benchmarks/coding/behavior_measurement_phase_a_20260912.json)：离线校准；不代表阶段B/C已执行。
- [API Catalog证据](../benchmarks/api_catalog/abaqus_api_catalog_final15_20260828.json)：固定构建来源、runtime notes与benchmark。
- [架构决定索引](../docs/adr/README.md)、[当前实现范围](../docs/architecture/agent-implementation-scope.md)、
  [技术栈与讲解边界](../docs/architecture/technology-stack.md)。

保留原始失败报告、版本绑定、withdrawal/consumption记录和独立oracle。新的评分解释应另立版本；
历史seal不因当前源码清理而被改写，也不能继续作为当前源码的封存证明。

## 维护与发布

模板更新：在干净工作树运行 `just update-template`；出现Copier冲突后先解决冲突并删除已处理的
`.rej`，再运行 `just finish-template-update`，不要重新启动更新。后者刷新lock并执行检查；
模板PR也应执行它并携带相应lock变更。模板身份/profile变化需单独迁移。

本仓库维护并提交 `uv.lock`；Renovate配置不等于Hosted App已启用。
CI执行Ruff、BasedPyright和pytest，具体规则以[工作流](../.github/workflows/ci.yml)与[Just recipes](../justfile)为准。

发布须在源码通过CI后于 `main` 显式运行 `just bump`：该命令创建版本提交和annotated tag，并原子推送到
`origin`，不是只读检查。tag触发[Release工作流](../.github/workflows/release.yml)，检查并构建wheel/sdist，
附到GitHub Release，不发布PyPI；发行包不包含Abaqus、许可证、模型凭据或本机知识索引。

本项目使用[MIT许可证](../LICENSE)，保持独立Git历史。借鉴或改编上游内容须记录仓库、基线、原文件和修改，
并履行第三方许可证义务，见[来源与复用边界](../docs/adr/0005-traceable-upstream-reuse.md)。
