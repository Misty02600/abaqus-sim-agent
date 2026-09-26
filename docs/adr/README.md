# 架构决定

先用 [架构总览](../architecture/overview.md)了解当前事实，再用本索引查原始取舍。根目录保留当前仍约束
维护的已采纳决定；六份已替代记录见 [历史索引](history/README.md)。编号是稳定身份，不表示实现顺序。

**已采纳不等于已实施。** ADR 的当时背景、后果和实验记录保留原意；当前入口、兼容组合、实施状态与
支持结论由 architecture 和 [验证证据](../architecture/verification-evidence.md)维护。
不因一次接口接线、普通修复或实验结束就新增 ADR，也不把后来状态回填到旧决定。

## 产品、组件与权限

| 决定 | 状态与作用域 |
|---|---|
| [0008：Pydantic AI Harness 单 Coder](0008-pydantic-ai-harness-abaqus-coding-run.md) | 已采纳；替代旧 LangGraph、多角色及首轮控制面路线，后续 scope/恢复/问答按下列决定扩展 |
| [0017：按活动组件组织产品包](0017-package-by-component-coding-run.md) | 已采纳；组件职责与消费方端口 |
| [0015：产品 Coder 不暴露通用 Shell](0015-disable-general-shell-for-product-coder.md) | 普通产品决定已由 0032 替代；旧冻结 profile 与原失败依据保留 |
| [0018：分级 ACI 与已验证 API 合同](0018-enforce-verified-abaqus-aci.md) | 原双模式与编辑/API 阻断由 0034 替代；原证据保留 |
| [0038：执行前语法与非阻断静态反馈](0038-unify-runtime-syntax-and-static-feedback.md) | 已采纳；运行前语法必检，可选静态诊断随执行结果返回 |
| [0039：保留工作区文本编码](0039-preserve-workspace-text-encoding.md) | 已采纳；UTF-8/CP936 严格解码、原编码写回与 raw-byte hash |
| [0023：调用方声明的纯编辑范围](0023-constrain-explicit-reference-edits-before-writing.md) | 已采纳为默认关闭的单表达式编辑 profile |
| [0025：CodingRun 上的本地 Web 工作台](0025-local-web-workbench-over-coding-run.md) | 已采纳；FastAPI + assistant-ui 的工程实现已完成，真实 Web 主线仍由 PLAN-0010 验收 |
| [0026：按请求范围支持文字回答与局部执行](0026-scope-requests-and-text-responses.md) | 已采纳；新普通会话 launch v5 允许无文件文字交付，文件/执行验收边界保持不变 |
| [0032：为普通产品 Coder 恢复受限通用 Shell](0032-restore-restricted-shell-for-product-coder.md) | 已采纳；普通产品具备标准 Coding Shell，直接 Abaqus launcher 仍被拒绝 |
| [0033：用一个 Solver 工具分离提交与等待](0033-split-solver-submit-and-wait-actions.md) | 已采纳；普通产品 submit 返回 execution ID，wait 普通 await，Web/CLI 共用语义 |
| [0034：Web 会话绑定用户选择的已有工作区](0034-bind-selected-workspaces-for-web-runs.md) | 已采纳；保留空目录选项，所选目录原地读写，状态仍由宿主管理；局部替代 0025 的目录范围 |
| [0035：永久删除 Web 会话记录并保留工程文件](0035-delete-web-conversation-records-and-keep-workspaces.md) | 已采纳；确认后结束受控执行并删除记录，无回收站，保留工程与最小幂等回执 |
| [0036：中断 Web 当前工作段并单独管理后台作业](0036-interrupt-web-work-segments-and-manage-background-jobs-separately.md) | 已采纳；主停止取消模型与前台工具，后台 Solver 保留独立取消和状态入口 |
| [0037：用普通本机 URL 打开回环 Web 工作台](0037-open-loopback-web-with-plain-local-url.md) | 已采纳；服务就绪后默认打开普通 URL，浏览器无感建立本地 session，保留同源 CSRF 边界 |
| [0041：Web 新会话必须选择已有工作区](0041-require-selected-workspace-for-web-runs.md) | 已采纳；新会话不再自动创建托管空工作区，已有 managed 登记按原路径保留 |

## 验收、恢复与人机交接

| 决定 | 状态与作用域 |
|---|---|
| [0004：用证据和物理验证定义成功](0004-evidence-first-physics-verification.md) | 已采纳；证据与能力声明的共同原则 |
| [0019：产品执行证据与测评物理 oracle 分离](0019-separate-product-execution-evidence-from-evaluation-oracles.md) | 已采纳；开放产品完成不等于物理正确 |
| [0020：Harness StepPersistence 会话续跑](0020-resume-coding-runs-with-harness-step-persistence.md) | 已采纳；未决外部副作用停在 needs_reconcile，不等于 Job 恢复 |
| [0021：Amendment 与 Task Revision](0021-use-amendments-and-task-revisions-for-continuation.md) | 已采纳；部分替代 0020 的禁止修改配置/重开 terminal 限制 |
| [0024：ask_user 与宿主持久待答](0024-host-owned-user-question-waiting.md) | 已采纳；局部替代 0008/0020 的新默认交接，旧 profile 产品读取兼容由 0030 部分替代 |
| [0027：Web Thread 消息与 task revision 解耦](0027-decouple-web-thread-messages-from-task-revisions.md) | 已采纳；普通消息使用 continuation 与独立 evidence baseline，显式 task revision 合同保留 |
| [0030：开发期不保留 CodingRun state 向后兼容](0030-drop-pre-release-coding-run-state-compatibility.md) | 已采纳；旧开发期 state 显式拒绝，当前-schema 恢复与冻结评测证据保持 |

## 知识来源与检索

| 决定 | 状态与作用域 |
|---|---|
| [0005：可追溯地吸收与重构上游](0005-traceable-upstream-reuse.md) | 已采纳；替代 0001，保留独立仓库与许可证/来源义务 |
| [0009：精确 Abaqus API Catalog](0009-exact-abaqus-api-catalog.md) | 已采纳；来源与证据合同保留，普通产品默认由 0036 更新，调用次数上限由 0031 部分替代 |
| [0040：普通产品必须绑定 API Catalog](0040-require-api-catalog-for-product-runs.md) | 已采纳；更新 0009 的产品默认，移除产品 `off`，默认 automatic verified-only |
| [0010：显式受预算的官方概念检索](0010-budgeted-official-concept-retrieval.md) | 已采纳；可选证据、不补物理输入，普通产品次数上限由 0031 部分替代 |
| [0022：独立比较 BM25、Dense、RRF 后再采用](0022-evaluate-bm25-dense-rrf-before-agent-adoption.md) | 已采纳；保留算法对照与采用门槛，不代表 RRF 已替换默认 Concept 路径 |
| [0028：本机私有领域知识包绑定为只读内容快照](0028-bind-user-domain-knowledge-as-read-only-snapshots.md) | 已采纳；仅本机私有内容、run 快照与恢复绑定，不开放分发、任意代码插件或新权限；默认位置与发现方式由 0029 局部替代 |
| [0029：从用户级应用数据目录发现本机私有知识包](0029-discover-private-knowledge-from-user-data.md) | 已采纳；`platformdirs` 用户数据根、受限 manifest 发现、显式 run 启用与 launch v6 绑定 |
| [0031：普通产品不按低成本工具调用次数终止 CodingRun](0031-remove-product-tool-call-count-limits.md) | 已采纳；保留 usage/证据/单次边界，移除产品 lookup/preflight 次数终止 |

## 固定模型变更与诊断合同

| 决定 | 状态与作用域 |
|---|---|
| [0011：使用已验证 CodingRun 种子](0011-verified-seed-model-change.md) | 已采纳；源任务和 artifact 先验后用 |
| [0012：共享执行证据、逐 Case gate](0012-per-case-model-change-suite.md) | 已采纳；物理 verifier 保持独立；中心孔指标边界由 0013 局部替代 |
| [0013：新指标由宿主 oracle 验收](0013-host-owned-model-change-acceptance.md) | 已采纳；部分替代 0012 对中心孔 extractor 的要求，沿用 0011 的可信种子边界 |
| [0014：typed probe 与 production adoption](0014-typed-probe-convergence-protocol.md) | 已采纳为显式模式；诊断、目标和采用证据归宿主 |

## 如何维护

- 改变原架构取舍时新建 ADR，说明接受依据、替代范围和双向关系；普通实现演进进入当前文档和测试。
- 不删改既有 Accepted/Rejected 决定的背景、决定与后果。归档保持编号和原状态，只修链接、路径及关系。
- 现有较长 ADR 中的历史实验和当时实施步骤不作为今天的工作队列；不再向其中追加批次流水账。
- 只在获得相应文档治理授权后归档。部分被替代但仍约束当前系统的 ADR 继续保留在当前索引。

当前接口与知识组合见 [知识接入](../architecture/knowledge-access.md)，计划状态见
[计划索引](../plans/todo/README.md)，不在本页复制实验分数或计划进度。
