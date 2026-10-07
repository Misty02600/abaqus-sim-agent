# ADR-0044：使用官方 Phoenix SDK 与独立本机追踪服务

## 状态

2026-10-07：[ADR-0051](0051-manage-phoenix-lifecycle-for-evaluation.md) 将显式测评改为直接启停独立 Phoenix 服务、使用原生默认数据库。普通 CLI/Web 的可选追踪和数据所有权保持原合同。

2026-10-06：[ADR-0050](0050-record-evals-as-phoenix-experiments.md) 为显式启用的评测增加原生题集与实验记录、执行前服务检查；部分替代下述评测仅上传评分注释的范围，其余业务状态与观测副本分离原则继续成立。

已采纳；替代 [ADR-0042](0042-opt-in-logfire-observability.md) 的 Logfire SDK、云端出口与启用契约。
原有运行状态、验收和追踪副本分离的原则继续成立。

## 日期

2026-09-27

## 背景

项目需要在可分发的本机应用中查看 Agent、工具、后台 Solver 和评测证据，并在没有云端账号或 Token 时
保存、重启后回查。现有业务埋点已经独立于后端；为选择平台而迁移 Case、oracle 和本地报告没有实际收益。

[同任务 PoC](../../benchmarks/observability/phoenix_mlflow_poc_20260927.md)确认 Phoenix 和 MLflow 都能接收
原有数据并本地持久化。Phoenix 的属性筛选和官方 Pydantic AI 消息转换适合现有排障需求；MLflow 的额外
实验管理能力尚未减少本项目代码或操作。用户明确选择 Phoenix，并授权引入及移除 Logfire。

## 决定

- 产品直接使用官方 `arize-phoenix-otel`，Pydantic AI 原生 instrumentation 加官方
  `openinference-instrumentation-pydantic-ai` processor。保留已有业务 span，不建立通用多后端适配框架，
  不用两套自动 instrumentation 重复包装 Agent。HTTP 和线程上下文使用官方 OTel instrumentation。
- 移除完整 `logfire` SDK、web extra、`--logfire` 和 Token 配置。上游 Pydantic 的 `logfire-api` 间接依赖
  保留；它在没有 SDK 时是空操作 shim，不负责云端发送。历史 ADR 和冻结报告不改写为新平台结果。
- 统一分发追踪 SDK；Phoenix 服务端独立安装/运行，不加入产品运行依赖，不由产品自动启动。服务自己拥有
  SQLite 和查询页面；README 提供固定持久目录与回环启动命令。评测专用 client 只加入 eval 依赖组。
- 唯一启用开关为 `--phoenix`，默认不初始化导出；启用直接采集消息、工具和 Case 正文，没有 full 模式或
  额外内容开关。默认地址 `http://127.0.0.1:6006`，只通过官方 `PHOENIX_COLLECTOR_ENDPOINT` 改本机地址。
  当前限定无鉴权回环 HTTP；不采纳任意远端、代理、跳转、workspace `.env.phoenix` 或其他 OTLP 出口。
  该设置属于宿主进程，不写入项目偏好，不随旧 run 恢复自动启用。
- 一次进程初始化一次 provider，Agent 使用实例级设置；复用 run/revision/continuation/invocation/
  execution ID。`session.id` 对应现有 run ID。人工等待和恢复分段，后台 Solver 独立记录；不把 span
  当成可跨进程恢复的业务执行。
- Pydantic Evals 2.47 的内置 Case spans 在移除完整 Logfire SDK 后不再产生。因此使用公开
  `CaseLifecycle` 为每次 Case/重复建立 span，写回公开 report 的 trace/span ID；保留原有 span_tree
  评测。通过 Phoenix client 复制已有 assertion/score/label 为 span annotation；不重算、不改变 gate，
  不把现有评测迁移为 Phoenix Dataset/Experiment。
- 已知模型凭据在导出副本中过滤，不采集 HTTP body/header、二进制内容或额外整文件；正文仍可包含用户
  私有文本，不能宣称任意匿名化。凭据环境不继承给 Shell/vendor。没有 Console exporter，原日志保持。
- 本机服务不可用、导出和评分发送失败不修改 outcome、不重跑业务；入口有界关闭。该出口没有磁盘离线
  队列或可靠补传保证。Phoenix 副本可缺失，不能补写执行 ledger、覆盖 oracle 或替代冻结报告。
- 工作台删除只处理原会话记录；Phoenix 中的副本由 Phoenix 单独管理。此决定不改变 ADR-0035 的工程文件
  保留和执行收尾合同。

## 替代方向与代价

保留 Logfire 云端可减少 Case 生命周期接线，但不能满足本次无需账号的本地持久查看目标。直接组装 OTel
虽能发送相同数据，却需自行维护配置与 Phoenix 转换；选官方 SDK，项目仅补身份、凭据和 Evals 公开钩子。
迁移 MLflow 的实验体系会扩大数据与报告改造面，本轮证据没有证明收益。捆绑服务端会增加普通用户安装体积
和后台服务生命周期，因此服务独立运行；启用追踪的使用者需要另开一个进程并管理数据保留。

详细追踪增加序列化与异步导出开销，未做高吞吐性能结论。SDK、Pydantic AI 和 Evals 升级时必须回归消息
转换、span_tree、并发 Case、后台执行与关闭行为。平台 UI 可以展示成本字段，但本轮合成模型不能证明真实
Provider 的价格覆盖或成本准确性。

## 接受依据与落实

用户于 2026-09-27 明确说“就这样决定了，你可以引入并移除logfire”。实施与成功标准沿用并修订
[PLAN-0014](../plans/done/0014-phoenix-local-observability.md)，当前合同见
[系统框架](../architecture/system-framework.md)和 [README](../../README.md)。
[本地接入验收](../../benchmarks/observability/phoenix_local_integration_20260927.md)覆盖真实服务接收、消息
映射、Case 评分、重启读回和独立 wheel 安装；无真实模型调用、无持证 Abaqus、无物理收益声明。

官方接线参考：[Phoenix Pydantic AI](https://arize.com/docs/phoenix/integrations/python/pydantic/pydantic-tracing)。
原状态和执行边界继续遵循 [ADR-0019](0019-separate-product-execution-evidence-from-evaluation-oracles.md)、
[ADR-0036](0036-interrupt-web-work-segments-and-manage-background-jobs-separately.md)及
[宿主配置合同](../architecture/configuration-reference.md)。
