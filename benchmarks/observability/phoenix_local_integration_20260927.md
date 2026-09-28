# Phoenix 本机产品接入验收（2026-09-27）

## 结论与证据级别

产品已通过官方 Phoenix SDK 向真实本机服务发送追踪，完整 Logfire SDK 已移除。同 5 个现有确定性场景产生
38 spans、5 traces、5 sessions 和 5 条已有断言的 annotation；服务重启后 API/数据库读回数量一致。
网页能进入 Case，查看业务调用树、评分，以及流式 typed output 的工具参数。

这是真实后端接收验证，模型和进程使用离线 fixture；没有真实 Provider、Abaqus 许可证执行或物理验收。
结果不代表真实模型成功率、高吞吐性能或成本准确性。冻结摘要见
[JSON](phoenix_local_integration_20260927.json)，替换前的
[Phoenix/MLflow 同数据对照](phoenix_mlflow_poc_20260927.md)保持独立，不回填其 49-span 结果。

## 环境与方法

Windows / Python 3.12，当前 main 工作树含既有未提交修改；不是发布版本验收。核心文件 hash 随摘要保存。
Pydantic AI/Evals 2.47.0、Phoenix SDK 0.17.1、client 3.5.0、OpenInference Pydantic AI 0.1.27、
OTel SDK 1.44.0；Phoenix 服务端 20.16.0 在隔离环境运行。

独立数据目录、回环 HTTP 16007 / gRPC 14318，关闭服务自身使用遥测和外部页面资源。使用产品真正的
`telemetry_session`、`PhoenixCaseLifecycle` 和 `publish_evaluation_feedback`；没有替换 exporter 或
provider。外部 socket 连接禁止，运行记录为零外部连接尝试。测试服务启动/重启由验证脚本负责，不属于产品
自动启动能力。

| 场景 | 复用的现有断言入口 | 覆盖路径 |
|---|---|---|
| response | `tests/test_open_requests.py::test_text_question_finishes_without_files_or_verification_and_restores` | 一次文字答复、恢复且无文件执行 |
| tool-loop | `tests/test_open_requests.py::test_one_script_needs_no_solver_or_full_model` | 多轮工具、交付与宿主 verifier |
| stream | `tests/test_web_streaming.py::test_actual_incremental_summary_and_final_replacement` | `Agent.iter()` / `node.stream()` 增量与最终替换 |
| background-stop | `tests/test_web_stop.py::test_stop_current_task_keeps_background_solver_running` | 主工作段停止，独立 Solver 继续与 execution ID |
| failure-resume | `tests/test_web_conversation.py::test_provider_failure_keeps_new_message_and_explicit_continue` | 注入 Provider 失败、保留消息、显式继续 |

外层 Dataset 一次执行以上 5 Case，保留原断言。后台场景只将受控进程 fixture 接到真实
`Abaqus2024Runner`，生成 fixture ODB；没有启动 vendor launcher。评测断言名为 `fixture_assertions_passed`，
通过现有 report 复制为 annotation；它不是物理成功评分。

## 接收、界面与重启结果

- REST span 数据包含全部 38 spans；5 个 report Case 的 trace/span ID 均指向对应 `evaluation.case`。
- 每 Case 一条独立 Trace，原有 run ID 映射为 5 个 Session；修复了原对照批次的 Dataset 大 Trace 只能归到
  一个 Session 的组织问题，不额外建立持久会话 ID。
- 官方 OpenInference processor 转换后，`chat function::stream` 的专用工具输出为
  `final_result_ResponseDecision({"summary":"第一段，第二段。"})`，网页工具卡可见；不是只存在 raw 属性。
- Case 列表显示 5 条评分，stream Case 树显示 `evaluation.case → coding.work_segment → Agent → model`。
  后台 Solver 的业务身份通过属性检索，产品仍负责独立执行与恢复语义。
- 停止并重新启动同一数据目录的服务后，SQLite 保留 38 spans、5 traces、5 project_sessions、
  5 span_annotations，REST 返回 38 spans；网页在重启后读回 5 traces / 5 sessions。
- 比原 PoC 少的 span 来自去除 Logfire 的 experiment/task/evaluator 包装。当前保留 Case、业务、模型和
  工具调用，不声称每个原生 evaluator 都单独追踪，也不接管 Phoenix Dataset/Experiment。

## 离线与分发验证

`pytest -q`：1272 passed、41 deselected；Ruff 检查、559 个文件格式检查和 BasedPyright 通过。
遥测专项 25 项覆盖默认关闭、非法出口、环境和工作区配置隔离、已知凭据过滤、300K 字符输入、原始日志、
失败与取消、后台执行、Web continuation、并发重复 Case、span_tree、评分复制与有界关闭。
大输入仅在本地 exporter 中验证，没有据此宣称后端最大体积。

构建 wheel，并在新 Python 3.12 环境中按 `uv.lock` 导出的运行时/web 约束安装。包从独立 site-packages
导入，两个 CLI help 显示 `--phoenix`、不含旧开关；包含 Web 静态 index。环境没有完整 `logfire`、
`arize-phoenix` 服务端、`pydantic-evals` 或评测 client，只有产品所需官方 SDK。Pydantic 上游仍间接安装
`logfire-api` 空操作 shim。

## 复查与限制

复查离线行为可运行 `uv run --no-sync pytest tests/test_telemetry.py -q`。接收复查时按 README 启动隔离
Phoenix 服务，使用上述固定函数与 Case 生命周期跑同一 Dataset，核对报告 span ID、对应 annotation、
`llm.output_messages` 和业务属性，再重启原数据目录查询数量。摘要提供本轮所有 Case ID 和源码 hash。

本机完整材料位于 `var/audits/phoenix-migration`，包含服务参数、验证脚本、report、重启结果和 wheel
安装证据；页面截图在 `output/playwright/phoenix-migration-*.png`。这些路径为本机辅助材料，本报告与 JSON
已经保存方法和必要结论，不要求读者拥有该目录才能理解范围。

发送仍是尽力而为，没有离线队列；后端关闭期间的内容不承诺补传。服务数据库独立于产品 state，工作台
删除不联动删追踪。已知凭据过滤不是任意正文匿名化；本次没有高并发容量、原生实验管理迁移或真实模型成本
验证。当前用法与数据边界见 [ADR-0044](../../docs/adr/0044-local-phoenix-observability.md)。
