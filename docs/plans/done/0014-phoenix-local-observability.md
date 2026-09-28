# PLAN-0014：Phoenix 本机追踪与评测关联

| 状态 | 最后更新 |
|---|---|
| 已完成 | 2026-09-27 |

## 目标与范围

以明确的 CLI 参数启用 Agent、工具、Abaqus runtime、verifier 和评测 Case 追踪，并在本机保存和查看。
普通产品使用、会话恢复、权限、oracle 和冻结报告保持原有所有权。用户此前明确要求本项加入 plan 并授权
实施；随后明确改选 Phoenix、移除 Logfire，本记录沿用同一计划身份，按最终批准范围验收。

原 Logfire 工程接线已完成离线验证，但未提供 Token、未做云端接收验收。后续同任务 PoC 和正式平台选择
替代该云端目标，没有将“缺少 Token”算作验收通过。原决定保留在 [ADR-0042](../../adr/0042-opt-in-logfire-observability.md)，
最终部署和数据边界由 [ADR-0044](../../adr/0044-local-phoenix-observability.md) 约束。

## 最终实现

- 使用官方 Phoenix SDK，Pydantic AI 原生采集及官方 OpenInference processor。完整 Logfire SDK、旧 CLI
  参数和云端 Token 接线已移除；上游 `logfire-api` shim 仍为间接依赖。
- CLI/Web 与 plate tension、task baseline runner/pilot、simple modeling 入口统一使用 `--phoenix`。
  默认关闭，只接收无鉴权本机 HTTP collector 地址；服务独立运行，SQLite 独立保存，产品不捆绑或自动
  管理服务。只在 eval 组安装 Phoenix client，普通产品无需 Pydantic Evals。
- 保留既有工作段、runtime、verifier 和后台 Solver 埋点；模型/工具/HTTP 使用原生采集。通过现有业务
  ID 关联执行、会话与 Case；`session.id` 复用 run ID，不新增持久状态体系。
- 公开 `CaseLifecycle` 建立每次评测的 span，并写入报告 trace/span ID；并发和重复 Case 独立记录。
  原 assertion/score/label 复制为 Phoenix annotation，原 span_tree evaluator 继续有效。不迁移
  Case/oracle/report，不接管 Dataset/Experiment，不回填历史成绩。
- 不读取工作区 `.env.phoenix`，不通过偏好自动启用；已知凭据过滤只作用于导出副本。退出有界关闭，
  发送失败不重跑业务；现有 raw logs 和恢复账本保留，没有离线可靠补传队列。

实现入口：[宿主 telemetry](../../../src/abaqus_sim_agent/telemetry.py)、
[评测生命周期](../../../src/abaqus_sim_agent/evaluation/telemetry.py)。用法见 [README](../../../README.md)，
当前稳定语义见 [系统框架](../../architecture/system-framework.md)和
[配置参考](../../architecture/configuration-reference.md)。

## 完成标准与验证

| 标准 | 验证结果 |
|---|---|
| 默认不连接，显式参数只接本机，不发现工程配置 | 启用/禁用、非法地址、SDK 环境和 `.env.phoenix` 测试通过 |
| 保留消息、工具、失败、业务身份及流式输出 | 真实本机 Phoenix 接收 5 个确定性场景；网页显示 stream 工具参数与完整工作段树 |
| 保留原业务与评测语义 | 离线覆盖 Web 停止/后台 Solver/取消/恢复、Case 并发重复、span_tree、失败和评分复制 |
| 数据在本机持久保存 | Phoenix 服务重启后，API/数据库读回 38 spans、5 traces、5 sessions、5 annotations |
| 可分发，普通产品无需完整评测或服务端 | wheel 独立 Python 3.12 环境按锁文件约束安装 web extra；CLI/Web help 和静态资源通过，未装完整 Logfire/Phoenix server/Evals/client |
| 相关回归与当前文档一致 | 1272 passed、41 deselected；Ruff、格式、BasedPyright 通过；README、architecture、ADR 已同步 |

固定方法、Case 来源、版本与结果见 [本机接入验收](../../../benchmarks/observability/phoenix_local_integration_20260927.md)。
替换前的 [Phoenix/MLflow 对照](../../../benchmarks/observability/phoenix_mlflow_poc_20260927.md)保持原值。

## 保留边界

本轮没有真实模型调用、持证 Abaqus 或物理收益验证。5 个确定性场景证明接线与数据留存，不能推出模型
成功率、性能、价格覆盖或所有 Case 的通用支持。300K 字符测试只验证本地 SDK 序列化。

Phoenix 服务需要使用者独立启动；未送达追踪没有补传保证，工作台删除也不删除 Phoenix 副本。正文仍可能
包含用户私有数据。跨进程运行恢复继续依赖原 state/evidence，不能靠追踪重建在途 Solver。

本计划已按最终范围闭环，没有云端 Token 待办；后续真实模型观察或迁移实验平台均需独立范围和证据。
