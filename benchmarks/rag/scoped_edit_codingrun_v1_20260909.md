# 受保护编辑的真实 CodingRun 三条件验收：本批无效

## 结果

三条初始请求均被 OpenCode Go 以 HTTP 400 `MissingSessionID` 拒绝。本次评测驱动漏传
`x-opencode-session` 请求头；此前 proposal 探针已经传入该 header，因此这是本次新增驱动的配置遗漏，
不能归因于模型、编辑 guard 或服务不可用。

| 条件 | 网络请求尝试 | 完整模型响应 | 文件工具调用 | 代码变化 | 行为验收 |
|---|---:|---:|---:|---|---|
| 证据充分 | 1 | 0 | 0 | 无 | 不可评价 |
| 证据缺失 | 1 | 0 | 0 | 无 | 不可评价 |
| 合成证据冲突 | 1 | 0 | 0 | 无 | 不可评价 |

三条都落为 `interrupted`，Abaqus/Solver 调用均为 0，没有完成 outcome/verification。
工作区未越界只是“没有执行文件工具”，不能算 guard 采用成功，也不能把本批写为模型任务成功率 0/3。
有效行为样本为 0。Harness usage 的 requests=0 表示没有完成的模型计量，实际有三次失败网络请求；
未返回 token/cost 计量，不能据此断言财务成本为零。

## 已冻结的协议

- 原充分/缺失/冲突三组证据和初始脚本直接复用 `knowledge-scope-v1` 已冻结 bytes/hash。
- 原题要求新增 `analysis.json`，与单文件纯编辑合同冲突；本批统一把说明交付位置改为最终 typed
  decision 的 summary，未改变 API 题目或证据内容。
- 使用真实 `CodingRunService`、Harness 文件工具、当前包内 Abaqus Skill 和 `single-reference-edit-v1`；
  Catalog/Concept/Planning 关闭。三个 runtime tools 不注册，Abaqus/Solver budget 均为 0，另加进程 trap。
- Go/`deepseek-v4-flash`，非 thinking、temperature=0、每响应 2400 output token 上限、HTTP retry=0、
  HTTP timeout=60 秒。每条件一个 CodingRun，最多 12 次模型请求、240 秒；允许 run 内正常工具修复，
  不允许补跑、resume、人工文件修补或依据输出改 Prompt。
- 分别判定：应用后的编辑范围、固定 region 表达式正确性、依据及不确定性表达。宿主
  `execution_evidence` completed 不能替代后两者。被拒绝的扩大编辑须另记，不能当作已落盘越界。
- 冲突组的两条资料均为合成且未验证，不是实际厂商矛盾。模型若依据已有知识选对 API，也必须说明
  来源性质与本次尚未验证的范围；不能仅因选对 API 就认定它无视冲突，或认定冲突已经可靠解决。

冻结 manifest SHA-256：`49029fd45895835cffa7795fd9a86b2083f430426fc2476170e3f3d57ef2b5fb`。
协议还预先规定只有凭据/余额失败才停止后续条件，故 HTTP 400 配置错误的三条均原样保留。

## 处置与限制

遵守三条结束后不补跑的协议：没有第四次调用、替换样本或同 run resume，也没有改动已冻结驱动、
产品源代码或历史证据。三项行为验收仍未完成；已完成的 221 项确定性回归仍是上一轮独立证据，不能
填补本批真实模型证据缺口。

若启动下一批，应在独立新目录修正并冻结 session header 的生成方式，先用不联网的请求配置检查确认，
再运行同三条件。新批必须单列，不回填本批，不能描述成提示词或 guard 收益。

## 证据

- [机器报告](scoped_edit_codingrun_v1_20260909.json) 包含冻结协议、输入、各 run 状态与原始证据 hash。
- 原始文件位于 `var/audits/scoped-edit-run-v1/`；三个 workspace 的初始脚本 hash 完全保持。
- 最终离线核对：冻结输入、实现文件和所有封存证据 hash 一致，三条均只有一个 request 记录、没有
  response 记录或工具调用。
