# Phoenix / MLflow 本地 OTel 对照：2026-09-27

## 结论与适用范围

本轮支持继续把 Phoenix 作为本地查看后端的默认候选，但需要官方 OpenInference 消息适配器和明确的评分/
业务身份映射。MLflow 同样能够接收并持久保存现有 spans；本轮没有证明其额外平台功能能够减少现有
benchmark、oracle 或 report 代码，因此没有据此切换平台。

这是 Windows 单机、5 个现有确定性测试场景的接线与查看 PoC，不是真实模型、Abaqus 或物理成功率评测。
产品当前的 --logfire 接口、ADR-0042、Case 定义和验收逻辑均未改变；PoC 的本地导出配置没有进入产品。

机器可读结果和源码绑定见 [JSON 摘要](phoenix_mlflow_poc_20260927.json)。本文包含判断所需的合同、数据和
限制；本机 raw 文件与截图用于补充检查，不是理解结论的前置依赖。

## 合同与方法

- 生产代码依赖：Pydantic AI/Evals 2.47.0、Harness 0.33.0、Logfire 4.41.0、OTel SDK 1.44.0，Python 3.12.11。
- 后端独立环境：Phoenix 20.16.0、MLflow 3.16.1；补充适配探针使用
  openinference-instrumentation-pydantic-ai 0.1.27。后端自己的依赖不安装到项目 .venv。
- 基于当前 main 的未提交工作树，不能指称为某个干净 commit 的结果；JSON 保存相关源码 SHA256，末尾复核
  所有这些文件未因 PoC 改变。
- 用现有 Pydantic Evals 为 5 个测试场景建立外层 fixture Dataset，按顺序各执行一次。原有测试断言与
  产品 verifier 不变；仅生成 fixture_assertions_passed，不能把这个分数写成物理正确性。
- 一份原生 OTel 数据经同一个分发器发送到两家 HTTP collector，过滤 Logfire 的临时 pending spans；
  原始 final spans 分成 3 + 46 两批，保存 OTLP protobuf/hash。没有 MLflow autolog，也没有重跑 Agent
  来生成另一家的数据。采集客户端禁止非回环连接，清除模型凭据；没有真实模型或 vendor subprocess。
- Phoenix 使用独立 working directory/SQLite，MLflow 使用显式 SQLite URI/artifact directory，均位于
  Agent 工程之外，监听 127.0.0.1。关闭后端使用统计，MLflow 网页另关闭其独立 UI telemetry 开关。
- UI 使用独立 Edge 测试浏览器；重启后通过两家的读取 API 核查持久化。SQLite 只读检查用于独立核对
  span ID、parent、原始属性和反馈条数，不替代网页检查。

## 场景与结果

| 场景 | 原有实现/断言 | 结果 | 带 Case ID 的业务 spans |
|---|---|---|---:|
| 普通文字答复与读取恢复 | tests/test_open_requests.py::test_text_question_finishes_without_files_or_verification_and_restores | 通过 | 3 |
| 写文件、多步工具循环与 verifier | tests/test_open_requests.py::test_one_script_needs_no_solver_or_full_model | 通过 | 6 |
| Agent.iter + node.stream 增量输出 | tests/test_web_streaming.py::test_actual_incremental_summary_and_final_replacement | 通过 | 3 |
| 主工作段停止、后台 Solver 继续、受控继续 | tests/test_web_stop.py::test_stop_current_task_keeps_background_solver_running | 通过；Solver 仅执行一次 | 12 |
| Provider 注入失败、保存消息与显式继续 | tests/test_web_conversation.py::test_provider_failure_keeps_new_message_and_explicit_continue | 通过；原失败保留 | 9 |

其余 16 个是 Evals 的 dataset/case/task/evaluator spans。总计 49 spans、8 个产品工作段、5 个业务 run ID，
但 Dataset 本身把本轮组织在 **1 条 Trace** 中。Token 是 FunctionModel 的测试计数：input 820、output 171；
不是真实账单，也不据此比较平台价格或任务成本。

后台 Solver 场景只替换 PoC 中的依赖构造：使用产品 Abaqus2024Runner 加等待事件控制的假 ProcessStrategy，
写入 fixture ODB bytes，以覆盖真实 runtime 埋点；未启动 Abaqus，原测试断言不改。runtime 的结束时间
晚于被停止的第一个工作段，并保留原 execution ID。其后继续没有重复提交 Solver。

## 同一数据的接收与持久化

| 检查 | Phoenix | MLflow |
|---|---|---|
| 原始 span 数与 ID | 49/49，一致 | 49/49，一致 |
| parent ID 与名称 | 无缺失或变化 | 无缺失或变化 |
| trace ID | 原始值保留 | 编码规范化后与原值一致 |
| 模型 Token 总数 | 820 / 171 | 820 / 171 |
| runtime 的 run/case/execution ID | 保留 | 保留 |
| 原生 Evals Case assertions 属性 | 5/5 保留 | 5/5 保留 |
| 仅接收 OTel 后的原生评分记录 | 0 | 0 |
| 复制现有断言后的原生评分记录 | 5 个 span annotations | 5 个 span assessments |
| 服务重启后的 API 读取 | 原始 49 spans、5 反馈均在 | 原始 49 spans、5 反馈均在 |

补挂反馈使用 Phoenix /v1/span_annotations 与 MLflow log_feedback(span_id=...)，标注 CODE/offline_fixture，
保留 Case ID，仅复制已有断言，不调用模型或重新评分。它证明结果可以轻量挂接，**不证明已有 Evals Dataset
自动变成平台的原生 Dataset/Experiment**，也没有同步人工审查或替换原报告。

## 展示与查询观察

### Phoenix

- 原始数据自动识别 8 个 AGENT、11 个 LLM、3 个 TOOL；27 个框架/业务节点以 UNKNOWN 展示，原始字段仍在。
- span 表达式 attributes['case_id'] == '04-background-stop' 可直接筛出 12 个相关业务 spans；runtime
  Attributes 面板可看到 run_id、invocation_id、execution_id、stage_succeeded 等字段。
- 原生 Pydantic AI v5 消息的普通文本输入可以识别，但本轮流式 typed tool output 在专用 Output 面板只
  显示 model 字段；完整回复仍在 gen_ai.output.messages 中。这是展示转换缺口，不是数据丢失。
- 对已保存的那一个流式 span 使用官方 OpenInferenceSpanProcessor 后，能够显示用户输入和
  final_result_ResponseDecision({"summary":"第一段，第二段。"}) 工具卡。未改消息、模型调用或 Case。

这个适配探针是**独立的显示测试**：新 trace/span ID，保留原始时间，增加 OI 属性；同样发送给两家另建的
mapping-probe 项目/实验。不是重新执行 Agent，也不计入 49 个原始 span。处理器还会把非 ERROR 状态改成
OK，因此不能声称转换前后所有字段字节相同。当前只验证了该流式样本的专用渲染，不能外推完整场景兼容。

### MLflow

- 不用 autolog，直接接收相同原生 spans；span 属性中生成 CHAT_MODEL/AGENT/TOOL 等 MLflow 映射，Token
  总数正确。后端 spans.type 列本轮为空，但 content 内的 mlflow.spanType 和 UI 类型存在，不能只看一个
  数据库列断言类型丢失。
- 原始流式输出已能在 Inputs / Outputs 中读取完整 parts/tool_call/arguments，主要呈 YAML/结构化文本；
  所检查面板中用户输入有重复展示。保留了原数据，不把 UI 表现视为原始 span 重复。
- Trace 面板可展开 runtime，Attributes 可查 execution ID。实测 Span name = abaqus.runtime 可以筛到
  对应 trace；查看过的 Trace filter 提供 Metadata/Tag/Span name 等，但没有任意子 span 业务属性入口。
  没有据此断言 SDK/API 不支持更多筛选。
- 此 Windows 环境首次打开页面空白：系统 MIME 关联使 .js 返回 text/plain。PoC 用仅作用于测试服务的
  sitecustomize 设置 JavaScript MIME，用新的测试浏览器会话避开旧响应缓存后正常；未修改系统注册表或产品代码。
- 服务启动提示 job execution backend 不支持 Windows。Trace 接收、查询和反馈正常；此限制不能扩大为
  “MLflow 在 Windows 不可用”，也不能忽略后直接承诺其服务端评测/后台任务功能可用。

## 四层身份与评分的共同限制

原始 trace ID、case 名/ID、5 个 run ID、invocation 和 Solver execution ID 均可回查，父子关系完整。
但本次外层 Dataset 把多个业务会话放进一个 Trace，两家的原生 Session 页都只归到其中一个会话；它们
没有自动变成“5 Case → 5 会话 → 多工作段 → Solver”的领域导航。这与本次源数据组织有关，不是后端丢 span。

当前可用业务字段筛选和 Case span 的反馈解决基本回查。若产品需要原生会话/Case 导航，仍需单独验证身份
映射与批次 Trace 组织；不能把本次 annotation 成功等同于完整实验管理接管。两家 Case 节点还沿用原生
模板名 case: {case_name}，具体案例名在属性里，也属于后续显示适配的观察项。

## 取舍与剩余边界

Phoenix 的任意 span 属性筛选和补上官方处理器后的工具卡更贴合本机排障；MLflow 的实验容器和已有 Trace
反馈同样可用，但本轮既没有减少报告代码，也没有需要它接管的新评测需求。继续 Phoenix 默认候选、MLflow
保留对照，比立即迁移平台更符合当前证据。

尚未验证真实 Provider、真实 Solver、高吞吐性能、全部场景经 OI 适配后的专用渲染、原生 Dataset/Experiment
迁移和完整四层导航。首次导入/请求的耗时受缓存、系统和批次影响，不据此排名性能；原始 span 数据保留，
本轮没有为了云端时限修改 timestamp。

后续若实施本地出口，应复用现有埋点，采用明确的本地配置和官方转换器，另行明确业务身份/评分映射。
本报告不自动修改已采纳的云端接口决定，不把 Phoenix/MLflow 作为产品必装依赖，也不关闭 PLAN-0014 的
Logfire 云端接收验收项。

## 复现与证据

本机审计目录为 var/audits/otel-poc-20260927：独立服务启动参数、capture.py、原始 OTLP、source-spans、
反馈映射脚本、mapping-probe 和重启核查均保留。UI 截图在 output/playwright/otel-poc。摘要保存全部关键
结果、相关源码 hash 和两批 OTLP hash；没有仅靠本机文件才能读懂的结论。

复现时使用上述精确后端版本与当前源代码绑定，在独立数据目录启动回环服务；按表中测试函数构造外层
Dataset，保留原断言；一次采集同时导出给两家，核对 span ID/parent/Token/反馈，最后停止并重启服务读回。
Phoenix 原生消息映射不足时，用官方处理器作独立补充测试并单独标明数据转换，不能混入原始基线。

官方接线参照：[Phoenix Pydantic AI](https://arize.com/docs/phoenix/integrations/python/pydantic/pydantic-tracing)、
[MLflow OTLP 接收](https://mlflow.org/docs/latest/genai/tracing/opentelemetry/ingest/)。实际结果以本次锁定版本
和本报告为准，滚动文档不替代回归。
