# 公开力学评测基础设施 v3

2026-09-15，用户授权解决本批基础设施问题。范围是评测驱动和故障边界，不扩大知识包、RAG、物理容差或普通 CLI 默认配置。

## 处置与验证

| 已复现问题 | v3 处置 | 验证 |
|---|---|---|
| 实体梁 `0...2/0...1` 题面歧义 | 从 typed contract 生成完整坐标不等式、材料与合力；goals.json 与 source/cases 一起封存、回验 | 尺寸变更测试验证文本同步；最终 preflight 通过 |
| 同一故障60秒×11尝试，下一题继续消耗 | 本评测新配置180秒、额外1次 SDK retry；Provider/宿主中断后停止批次并使健康检查失效 | SDK 503 与 ReadTimeout 重试测试、批次停止测试 |
| 失败 HTTP 与返回 usage 混淆 | 注入可关闭的 HTTP transport；记录实际尝试、retry index、请求体 SHA、响应头状态、正文完成/异常类型、耗时；不记录凭据或正文 | 503→200、ReadTimeout→200、正文半途超时、未结束尝试 unknown |
| 中断后缺汇总，依赖协调对话收尾 | 每题及 finally 原子更新 report.json/report.md；report 命令离线重建；损坏证据显式 invalid_evidence | 无 outcome、强制中断痕迹、损坏 JSON、setup error 测试 |
| 只能重新开空工作区 | 显式 resume --case 复用 CodingRunService 恢复；预算、会话ID、历史和文件不重置；新 profile 最多一次显式续跑 | 原生 Agent/Service 中断→续跑集成，累计 usage/文件/history/turn 保留 |
| 续跑覆盖中断快照 | 每次 attempt 保存开始/结束、异常类型、结束时 resume/outcome snapshot；HTTP journal 追加 | 第一 attempt 的 interrupted/usage 在第二次结束后仍保留 |
| 协调 Web 对话断连 | launch 以隐藏独立进程启动冻结 worker，stdout/stderr/launcher metadata 落盘；worker 自己产报告 | Windows 实际独立进程取得 OS 锁并交付 marker，无模型/Abaqus调用 |
| taskkill 暂停留下 running | pause 请求标记触发 service cooperative cancellation；服务保存 interrupted 并清理当前 typed runtime | 取消等待 cleanup 测试；已有未决外部作用仍由产品 reconcile 门禁阻断 |
| 覆盖旧成绩/误用配置风险 | 旧 root 不由新 worker 更新；run 拒绝重开；continue 只选择从未启动的题目；runtime 参数仅 prepare 接收 | 原报告 bytes 不变、terminal/重复run/续跑上限与静默参数覆盖测试 |

先前 v2 已修复的显式零载荷误判、同字节重写 provenance 继续保留并通过回归。旧轨迹缺少 witness 不补造，旧失败不回填。

75 项相关测试通过，覆盖 public_mechanics/infrastructure、coding model/service/resume；修改的四个源码模块 BasedPyright 为0错误，Ruff通过。没有运行全仓全部测试，不将这些离线结果描述成实际六题全通过。

## 配置与证据

最终冻结根目录：`D:/abaqus-runs/public-mechanics-infra-v3-release-20260915`，版本 `public-mechanics-e2e-v3-recoverable`。同目录保存 manifest、seal、goals、preflight、health receipts、transport 和派生 report。两道待测题已经封存，但本次只验证基础设施，未运行新的力学任务。

先前两个中间快照的真实小请求检查也保留在 `public-mechanics-infra-v3-20260915` 和 `public-mechanics-infra-v3-final-20260915`，不是额外力学 trial，也不合并为能力通过率。

普通 CLI、旧 Gold 和原批次的60秒/10次重试配置不变。本评测的180秒/1次重试是显式工程取舍：给长响应更多单次等待，同时限制重复故障等待；并非已经证明旧超时由60秒设置单独造成。沿用相同 Go 模型、enabled_high、Planning、Foundation、full Catalog、docs off 和原物理预算。

SDK 允许注入宿主 HTTP client，由返回的 SDK client 及驱动清理；客户端关闭失败也记录，不影响 attempt 保存。runtime seal 额外绑定当前 SDK 使用的 httpx2 bytes。

## 执行方式

下面命令均使用项目 `.venv/Scripts/python.exe -I <root>/worker.py`：

- `preflight --root <root>`：真实工厂/schema检查，不联网或求解；新 root 只需一次。
- `doctor --root <root>`：实际 Provider 小请求。成功结果绑定 seal、有效10分钟，失败不启动任务；不验证复杂任务能力。
- `launch --root <root>`：隐藏后台执行首次批次；子进程自己持有批次 OS 锁，协调窗口退出不影响它。
- `pause --root <root>`：协作暂停；不能据此声称外部 Job exactly-once 或自动 reconciliation。
- `doctor --root <root>` 后 `launch --root <root> --action resume --case <case-id>`：明确恢复指定非终态任务；未决 runtime、workspace变化、损坏ledger、耗尽预算及超出续跑上限继续拒绝。
- `doctor --root <root>` 后 `launch --root <root> --action continue`：只运行尚未启动的题目，不隐式恢复或重跑旧题。
- `report --root <root>`：进程退出后，在OS锁保护下从已落盘状态重建汇总，不调用模型/Abaqus；不会将 running/unknown 或 Agent 自写 verification.json 判成物理通过。

run/continue/resume 同样可直接前台运行。运行中不可并发执行 report；worker 本身逐题更新报告。强制结束进程无法承诺完整最后一次 checkpoint，应先离线重建状态再按产品恢复门禁处理。批次锁不是跨所有项目进程的许可证协调器。

## 仍然必须诚实区分

真实短请求成功不能保证长请求或后续时段可用。Web/Go 服务端断连无法由本仓库永久消除；本轮解决的是可观测、有限等待、停止后续消耗、独立持久交付和受控恢复。官方 DeepSeek 文档也说明请求可能等待并通过 keep-alive 保持连接，但那是官方入口说明，不能直接断定 Go 网关的行为或旧故障根因：[Rate Limit & Isolation](https://api-docs.deepseek.com/quick_start/rate_limit/)。

HTTP completed 表示完整收到响应体，包含503等非成功状态，须结合对应 status 判断；failed 指传输异常。进程退出前只有 started 的请求保持 unknown。实际账单及失败调用的服务端生成 token 无法从客户端可靠重建，继续置 unknown，不把缺统计当0。

本次没有改模型脚本补分，也没有重跑旧失败；所有旧成绩、人工中断和已耗资源保留。后续力学批次必须使用新 seal，不能沿用旧题面样本作为有效比较。
