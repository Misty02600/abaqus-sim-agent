# ADR-0042：显式启用的 Logfire 观测与远端数据边界

## 状态

已替代，由 [ADR-0044](0044-local-phoenix-observability.md) 改为官方 Phoenix SDK 与独立本机服务。
以下保留当时的背景、决定和接受依据。

## 日期

2026-09-27

## 背景

产品已经用本地 history、execution journal、raw logs、usage 和 verifier 维护执行事实，固定评测使用
Pydantic Evals 与独立 oracle。跨模型请求、工具、后台 Solver 和验收的失败定位仍需手工拼接记录。
Logfire 可以提供原生 Trace 和 Case 关联，但引入远端副本会改变数据去向，不能成为运行或恢复的真值来源。

产品面向本机分发。使用者应能在没有 Logfire 账号的情况下运行，也应能按需启用详细追踪，而不需要维护
多种 SDK 安装形态、自定义追踪等级或另一套报告/状态系统。

## 决定

- SDK 随产品安装，FastAPI instrumentation 随 web extra 安装。CLI/Web 和首轮评测入口仅通过
  `--logfire` 启用，默认不初始化本应用的导出器；残留 SDK 发送环境变量不能自动启用。
- 凭据使用官方 `LOGFIRE_TOKEN`，不增加 token 命令行参数、不分发维护者凭据。宿主捕获后从子进程环境
  移除并显式传给 SDK；SDK 配置/数据目录位于用户级应用目录，不发现或写入模型工程中的 `.logfire`。
- 启用即采集原生模型消息、工具参数/返回和评测 Case 输入输出，不另建内容模式。帮助和文档说明可能
  包含脚本、用户问题和工具读入的私有文本；关闭二进制内容，不额外上传完整 ODB/CAE 或 raw log 文件。
- 保留 SDK 脱敏与请求头限制，补充宿主已知凭据的诊断/异常过滤；正文采集不提供任意用户文本的自动匿名化
  保证。读取内容和发送的权限不能由 workspace 内配置开启。这些措施不是 OS 沙箱。
- 原生 instrumentation 负责 Agent、模型、工具、HTTP 与 Evals。手工埋点仅补宿主工作段、真实 runtime
  和 verifier，复用现有业务 ID 与结果。遥测不改变 launch v10、权限、usage、工作区或副作用恢复语义。
- span 描述一次活动工作段或执行；人工等待结束工作段，恢复/continuation 通过业务 ID 关联新段。后台
  Solver 保持独立生命周期。Trace 缺失不阻止合法恢复，也不能用于修补 execution ledger。
- 关闭 Logfire 控制台 exporter，保留现有进度和本地原始日志。配置错误在副作用前报错；运行中导出失败
  不改变业务 outcome，不自动重跑。入口负责有界 shutdown，不按每个 Case 或 run 重配全局 SDK。
- 本地 state/oracle/冻结报告继续拥有原证据合同。Web 删除仅按 ADR-0035 清理本机记录；远端已导出副本
  按接收方保留策略管理，本机删除不承诺清空 Logfire。

## 替代方向与代价

- SDK 选装：依赖更轻，但增加安装/测试分支，用户需要排障时可能缺少组件；当前应用已固定 Pydantic AI，
  采用统一依赖更易维护。
- 有 token 就自动上传：配置更少，但继承环境或启动目录可意外改变数据去向；使用显式 CLI 参数。
- 自定义详细度等级与通用后端接口：可配置面更大，但当前没有对应需求；保留原生参数和小型入口接线。
- 全部替换本地报告和日志：界面统一，但采样、保留期和导出失败会削弱可恢复与冻结证据；维持原所有权。

新增依赖、序列化和网络开销需要测试；更换后端时标准 span 可复用，Logfire 配置和 UI 查询仍有平台成本。
脱敏并不覆盖任意用户主动写入的敏感文本；详细追踪只能在使用者理解数据去向后显式启用。

## 接受依据与验证入口

用户于 2026-09-27 明确确认 `--logfire` 加官方 Token 的交互、启用后直接记录内容，并授权实施
[PLAN-0014](../plans/done/0014-phoenix-local-observability.md)。当前实现与验证范围见
[系统框架](../architecture/system-framework.md)和[验证证据](../architecture/verification-evidence.md)。
离线 Trace、云端合成数据接收、真实模型观察和持证仿真必须分开声明；本决定不代表云端接收或物理收益通过。

本决定扩展遥测部署/数据边界，不替代 CodingRun 状态所有权；与
[ADR-0019](0019-separate-product-execution-evidence-from-evaluation-oracles.md)、
[ADR-0028](0028-bind-user-domain-knowledge-as-read-only-snapshots.md)、
[ADR-0035](0035-delete-web-conversation-records-and-keep-workspaces.md)及
[ADR-0036](0036-interrupt-web-work-segments-and-manage-background-jobs-separately.md)共同约束实现。
