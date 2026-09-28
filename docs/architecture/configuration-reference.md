# 配置逐字段合同

## Phoenix 宿主设置

本节记录显式启用的本机追踪配置；运行与恢复语义见[系统框架](system-framework.md#可选启用的遥测)。

| 字段 / 入口 | 缺省与来源 | 校验、持久化与恢复 |
|---|---|---|
| `phoenix` / `--phoenix` | False，必须显式启用 | 不从项目偏好自动开启；运行证据可记录观测元数据，但恢复不据此启用导出 |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://127.0.0.1:6006`；当前进程环境 | 只接受无鉴权本机 HTTP 地址，可指定端口；`--phoenix` 启用时校验，不进会话配置 |
| `PHOENIX_API_KEY`、`PHOENIX_CLIENT_HEADERS`、`OTEL_EXPORTER_OTLP*HEADERS` | 不作为产品配置输入 | 本机模式不需要密钥；清除以避免子进程继承 |
| `OTEL_EXPORTER_OTLP*ENDPOINT`、`OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*` | 不作为产品配置输入 | SDK 会话忽略其他导出地址及请求头采集设置，退出时恢复；不发现工作区 `.env.phoenix` |

以上均为本次宿主设置，不写入会话合同，也不随恢复自动启用。遥测元数据仅记录本次有效设置。
