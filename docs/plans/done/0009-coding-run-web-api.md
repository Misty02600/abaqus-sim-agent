# PLAN-0009：共享 CodingRun 入口与 FastAPI 任务接口

| 状态 | 最后更新 |
|---|---|
| 已完成：共享入口、任务API与生命周期离线验收通过 | 2026-09-14 |

## 背景与依赖

实现 [ADR-0025](../../adr/0025-local-web-workbench-over-coding-run.md) 的后端能力，让 CLI 和 Web 使用同一宿主规则，并提供可独立测试的任务接口。[PLAN-0008](../done/0008-assistant-ui-integration-qualification.md) 接线资格已通过，本计划已完成后端工程验收。独立交付物是任务 API、单项执行管理和只读 SSE，不依赖正式页面才能验收。

## 当前设计与缺口

实施前安装入口只有 `coding.cli:main`；`coding/cli.py::_run_cli_once` 混合参数解析、配置/绑定、模型/client/runner/Agent 装配和运行。实施前该文件约1016行；行数仅提示阅读范围，不用于推导修改量。已有 tests/test_coding_cli.py、test_api_catalog_members.py、test_document_policy_integration.py、test_edit_scope.py 导入 CLI 私有 helper，提取装配需要核对这些调用者。

`CodingRunService.run_step` 拿 state OS 锁执行主动工作段；`submit_answer` 已负责幂等、快照检查和晚答 revision，`inspect_question` 会修复/写入状态，不能挂到 GET。`coding.resume`/`amendments` 拥有持久恢复边界。现有 `ProgressSink` 仅是文本回调，不能解析它来形成 HTTP 状态或成功判定。

触发 Web 后新增的问题是：HTTP 请求结束或断开与后台工作段生命周期不同；不同请求可能重复提交；多个文件的读取可能跨过 revision 切换；文件下载可能碰到产物被改写。框架不会替本项目解决这些问题，需要接入已有所有者并补传输侧职责。

`take_gateway_api_key_from_environment` 会清空后续子进程可继承的模型凭据；长驻服务需启动时捕获到宿主内存，再逐段创建/关闭 client，不能第二次任务重新从已清空环境取值。

PLAN-0008新增实际证据：[三路比较](../../research/ui-three-way-comparison-20260913.md)的共用观察夹具两次遇到resume_state原子replace的WinError5，分别来自官方和ExternalStore页面路径；持有读句柄的独立检查也复现replace失败，单独FILE_SHARE_DELETE尝试仍失败。最终活动段只读settled展示缓存后通过比较。正式read_model必须验证观察者不干扰宿主原子写入；不能简单把Path.read_text放进高频GET，也不能将实验缓存粗粒度状态冒充完整生产事件接口。此处是新增接入风险，不回填为旧CLI运行缺陷或某UI库缺陷。

## 技术路线

### 所有权与实现位置（原拟定与落地）

| 位置 | 拟定变化 |
|---|---|
| 新 `coding/application.py`、`composition.py` | 提取 typed 新建/执行/回答/继续入口及装配上下文；不引用 FastAPI。沿用 request/launch effective 配置；CLI 只保留解析、Console 和输出。 |
| 新 `coding/read_model.py`、`progress.py` | 纯只读快照与结构化阶段观察；维持 existing progress_sink 兼容，观察失败不改变运行结果。 |
| 新 `web/app.py`、`routes.py`、`contracts.py` | FastAPI lifespan、请求/响应 DTO、错误码、同源入口；可选 `web` extra、独立 web 命令。 |
| 新 `web/dispatch.py`、`registry.py` | 单实例、一个专用执行线程、命令容量占位、持久回执与托管目录映射。 |
| 新 `web/events.py`、`artifacts.py` | SSE 失效通知和受控文件访问；不触发模型、runner 或 verifier。 |

实际落地将routes合并到web/app.py，将dispatch/registry分别命名为controller/storage；未增加空转分层。按 ADR-0017 保持组件依赖；不引入新的全局 services/repositories 分层，不整理与本次无关的 RAG/evaluation 债务。

### 请求、锁与生命周期

- 提供 ADR 定义的 create/start/answer/continue、list/detail/timeline/artifacts/events 接口；严格校验传输 DTO，不开放完整 CodingRunRequest 的任意路径/provider/tool 配置。
- 创建幂等键绑定请求摘要；启动/继续 command ID 绑定内容和回执。相同键同内容返回原结果，不同内容409；回执未保存不能执行。相同命令重放不再次提交工作段。
- 答案携带 question_id、expected_task_revision、command_id；复用产品函数写入。返回独立的 answer_saved/execution_accepted，保存成功但未接收执行时可以显式继续，不能覆盖答案或误报丢失。
- 命令回执与调度间的中断记录为不确定；不自动重放执行。新显式命令仍须经过现有 state/effect 复核；state 锁不等于外部 Job exactly-once。
- Web 执行槽采用非阻塞占用，忙时409，不排队。产品函数自行持 state 锁；Web 不在外层重复获取同一锁。与独立 CLI 的不同任务不做许可证协调。
- 专用线程中创建/使用/关闭异步模型 client 和 service loop；HTTP 与 SSE 生命周期不取消执行 Future。正常服务退出停止接单并等待工作段在原预算内 settled；强制退出后不自动继续，不按 PID 重附着。

### 读取、SSE 与产物

- 只读视图核对 run/revision/amendment/reference；跨边界有限重读后返回 updating/unavailable。GET 不调用 inspect_question 等可写恢复入口；当前待答及可用操作只作展示，命令锁内再校验。
- SSE 只发 sync/run.changed；先登记订阅再首次 sync，变化通知与约15秒 sync 都让页面重取快照。通知合并、断线清理，不回放 Last-Event-ID，不通过 SSE 接答案或推送 token，不让慢客户端拖住 runner。
- metadata 读取不重新扫描大型 workspace/ODB。原始 state 不作为静态目录发布；文件用 run/revision/path/hash 绑定，验证 Windows 路径、reparse/ADS 等边界。主动执行时不提供可变内容，settled 后以工作区外校验过的有限临时副本提供内容。
- 延续 ADR 的文本256 KiB、下载快照200 MiB边界；更大文件仅显示位置，不自动复制。PNG 检查格式/大小，HTML/SVG 不以活动内容执行。
- 仅 loopback、同源会话、Host/Origin/CSRF 检查；凭据不出宿主内存、不传子进程、不进访问日志。框架安装或模板本身不作为验证证据。

## 评估

| 项目 | 判断与依据 |
|---|---|
| 价值 | 高；任务身份、问答与恢复有真实服务边界，UI 可替换，CLI 继续可用。 |
| 技术难度 | 高；风险在锁、命令回执、资源生命周期和多文件一致性，路由语法不是主要成本。 |
| 最可能的回归 | 提取CLI时改变默认profile/header/tool schema、丢失凭据；重复命令重跑；GET修复状态；旧revision结果混入；退出后遗留未决runtime。 |
| 工作量估计 | 4–7个专注工程日，含定向回归、故障注入和独立API验收；基于单人熟悉现有Python代码的估计，需按实际接线结果修正。 |
| 与现有评测的关系 | 新依赖/源码改变后须按绑定规则新seal；不修改旧报告或把测试通过当作模型行为改善。PLAN-0006无硬前置依赖。 |

前端输出和 SSE 是展示层，不能增加新产品状态机。后端实现通过后可以独立验收本计划，但不能声称完整工作台已经交付。

## 完成标准与验证

| 场景 | 预期与验证位置 |
|---|---|
| CLI与新入口 parity | 有效配置、全部工具schema、Go header、预算/HTTP重试一致；既有 legacy/显式profile不变。复用 tests/test_coding_cli.py、test_task_baseline.py、test_execution_config.py。 |
| 已有问答与恢复 | 运行锁外等待、同答复幂等、异答复冲突、晚答revision、未决runtime优先不退化。复用 test_user_questions.py、test_coding_amendments.py、test_coding_resume.py、test_coding_run_lock.py。 |
| API与回执故障 | 新建/启动/回答/继续的重复、冲突、并发及记录后未执行等窗口有故障注入测试；缺回执不执行、未决effect不绕过。拟新增 tests/test_web_commands.py。 |
| GET/状态 | GET前后产品文件hash相同；revision切换、部分文件更新、损坏记录不能显示旧完成或调用模型。拟新增 tests/test_coding_read_model.py。 |
| 线程与连接 | fake runtime阻塞时API可响应；只有一个主动段，client在同一loop释放；断开SSE不取消，退出/崩溃不伪造完成。拟新增 tests/test_web_lifecycle.py，含真实子进程场景。 |
| SSE | 首次订阅竞态、遗漏/重复/慢客户端/重连、周期sync与资源清理可验证。拟新增 tests/test_web_events.py；不写逐字符格式镜像测试。 |
| 文件和网络入口 | traversal/UNC/ADS/junction/文件替换、超限/伪PNG、跨源请求和凭据泄露保护有负例。拟新增 tests/test_web_artifacts.py 与对应HTTP测试。 |
| 交付 | 默认CLI安装无web依赖仍可用；web extra的安装/API启动/OpenAPI可复现，Ruff/改动范围类型检查通过；完成相关回归，再运行全仓非持证测试，不冒充全源码原有类型问题已消失。 |

现有回归可用 `uv run --no-sync pytest -m "not abaqus"` 选择上述文件，新增文件在实现后加入命令。已完成全仓非持证回归：1080通过、20项因本工作树缺少固定abqpy/持证派生夹具跳过、27项持证未运行；Ruff检查/格式检查、BasedPyright均通过。

## 关联

- 后继：[PLAN-0010：工作台集成](../todo/0010-local-web-workbench.md)。
- 既有工程基础：[PLAN-0007](../done/0007-user-question-tool.md)、[ADR-0024](../../adr/0024-host-owned-user-question-waiting.md)。
- 本计划不包含任何真实Provider/Abaqus批次，不消费其他任务已有运行授权。

## 完成记录

实施工作树为`D:/Projects/abaqus-agent/abaqus-sim-agent-web`，分支`codex/web-workbench`，基线为
`54cfd51c87b9d40b65dce1b93b2b3ddc5fba4378`。原工作区保持独立。另定向带入原工作区已验证的Go推理
回传修复及其测试，未复制其其他未提交改动。

`tests/test_web_workbench.py`、`test_web_boundaries.py`、`test_web_lifecycle.py`共21项覆盖任务命令、
重复/冲突、已存答复后Provider失败、同根单实例/单槽、GET前后hash、损坏记录隔离、分页版本、PNG/链接/
HTML边界、观察失败、终态缺证据、模型无效工具参数，以及真实HTTP子进程下的SSE断开与正常退出drain。
旧CLI/问答/配置/恢复相关回归保留。Web的expected revision还会传入宿主，在保存答案的同一state锁内检查。

干净wheel环境确认普通CLI无FastAPI仍能运行，安装web依赖后由wheel独立提供HTML、哈希资源、API、
OpenAPI和依赖声明，不引用研究目录或源码checkout。前端构建/生成类型检查加入CI，但没有冒称远程CI已运行。
本机uv安装器两次发生Windows PE资源更新错误，干净环境最终使用同一uv导出锁定依赖经pip安装；失败日志保留。

证据在`var/audits/web-implementation/`：`full-tests-release.txt/xml`、`types-release.txt`、
`frontend-build-release.txt`和`wheel-smoke/`。这些是本机工程记录，0真实Provider、0Abaqus。
不将本计划完成解释为PLAN-0010真实主线完成或新增物理能力。
