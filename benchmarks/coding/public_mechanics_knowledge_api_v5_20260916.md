# 公开材料力学 v5：知识资料与 API discovery 资格审计

2026-09-16。用户要求在 Web worktree 继续施工期间并行启动新一轮材料力学审计，显式使用新接入的只读知识资料和 API receiver 方法目录。旧批次、旧结果和旧 seal 不改写；本批只判断组合 profile 能否完成选定任务，不作单因素因果归因。

## 冻结范围

- Case：`plate-with-hole-tension`、`solid-cantilever-directional-bending`。它们是最近正式运行中仍未完成、并且实际暴露区域/载荷/提取 API 与完整脚本组织问题的两题。已通过的两道轴向杆不重复运行；经典 B31 梁不属于本轮 Workbench/VM 目标。
- 单一 `abaqus_2024_coder`；Foundation profile `linear_structural_fea_v1` 开启。
- Catalog：`abaqus-2024-reference-context-v2`，模式 `full_with_discovery`。Discovery 只给 receiver 方法候选，模型仍须 exact lookup 才获得 Card 证据；共享原每题 8 次 API 预算。
- 知识资料：本机官方悬臂梁示例导出目录。原稿包含 GUI/submit 等教程代码；Agent 只能按项目三阶段边界适配。资料只读、随批次复制并由 seal 绑定，不作为工作区事实、物理输入或已执行证据。
- Go/deepseek-v4-flash，thinking enabled/high、Planning 开、HTTP timeout 180 秒、SDK 额外 retry 1；其余逐题模型/Abaqus/Solver/wall 预算沿用原 Case。
- 两题 Agent 并发 2，Abaqus runtime 并发上限 2。工作区、state、conversation 和 Job cwd 各自隔离；同批 semaphore 仅限制本进程，不宣称系统级许可证协调。
- results-first task oracle 与独立脚本审查边界沿用 v4。每题一次，不补跑替换失败；显式 resume 仍按冻结上限和同一 run 累计状态处理。

## 需要观察

- 完整任务是否形成 fresh CAE/INP/ODB/JSON/report 与三阶段 provenance，并通过对应 task oracle。
- Foundation、知识文件、discovery、exact lookup 的实际调用；候选不能按完整 Card 或任务采用计数。
- 建模、载荷/区域、网格、ODB 字段与序列化错误；是否复制案例物理参数或脚本内提交 Job。
- 模型/HTTP/tool/API/Abaqus/Solver 调用、累计 token、wall 与 runtime queue wait。两个并发任务的失败和未知 usage 分别保留。
- 人工读取最终脚本、实际 INP 与独立结果，签署或拒绝 script review；自动结果通过不替代这一步。

## 解释边界

本批同时改变 Catalog 模式、可见资料、Skill 快照、源码/依赖时间点和并发方式，不能与旧 `full` 批次作严格 A/B，也不能把通过或调用下降单因果归给知识库、discovery 或并发。旧失败可用于说明选题动机，不能作为新 profile 的 control 分母。若需要净收益结论，后续再用同一新 seal 增加不带资料/discovery的配对 control。

## 实际结果

批次目录：`D:/abaqus-runs/public-mechanics-knowledge-api-v5-20260916`；seal SHA-256 为 `90a6f4fb3c027e7817d055a3d991f4dd1e91c26dc30e09413e208880489e140d`。工厂预检确认 Foundation 六项 capability、`full_with_discovery`、exact lookup、两个只读知识工具和三个 runtime 工具同时可见，0 模型/0 Abaqus。一次小型 Provider doctor 成功。

两题随后并发启动，各使用一次新 run attempt 和冻结合同允许的唯一一次显式 resume；没有额外补跑、提高预算或新建替换批次。最终均为 `provider_interrupted`：

| Case | 完整模型响应 | HTTP attempts / completed / failed | API / Abaqus / Solver | 可见行为 |
|---|---:|---:|---:|---|
| plate-with-hole-tension | 1 | 5 / 1 / 4 | 0 / 0 / 0 | 加载基础 `abaqus-2024-coding` Skill，并列出空工作区；第二请求及 resume 均在 retry 后超时 |
| solid-cantilever-directional-bending | 0 | 4 / 0 / 4 | 0 / 0 / 0 | 初始请求和 resume 均在 retry 后超时 |

plate 保存的 received usage 为1 request、6940 input、543 output、2 tool calls；实体梁没有完整响应，记录 usage 为0只能解释为“无收到的完整响应”，不是服务端零生成或零费用。两题累计 active wall 分别约729.23秒与723.97秒，runtime queue wait 都为0，因为没有进入 Abaqus。

并发基础设施达到预声明行为：两个独立 task/conversation 同时开始，单批并发上限2，失败证据、HTTP attempt、resume和报告分别保留；没有 runtime 并发可供观察。知识文件、API discovery 和 exact Card 全部未调用，因此本批对它们的任务采用与收益没有行为资格；也没有最终脚本可作 script review。该结论不回填旧批次，不把 Provider 阻断记成工程任务失败或知识系统失败。

## 用户授权的事后恢复观察

用户随后明确要求验证中断恢复。该操作不改变上面的正式批次结果：它绕过冻结合同中“每题最多一次显式 resume”的评测上限，在原 run、原 workspace、原 state 和原累计预算上各增加一次 `003` resume attempt。没有修改 manifest、seal、任务、预算、模型配置或输入资料；控制记录位于批次目录 `control/posthoc-resume-003.json`。续跑前的同一冻结环境 doctor 再次成功。

| Case | 本次 HTTP attempts / completed / failed | 恢复后行为 | 累计状态 |
|---|---:|---|---|
| plate-with-hole-tension | 4 / 1 / 3 | 第一个请求首试超时、retry 在约83.64秒后成功；模型保持原会话并再次检查空工作区。下一请求及其 retry 均超时 | 2个完整模型响应、4个工具调用、16447 input、783 output、9472 cache-read tokens；active wall 约1355.38秒 |
| solid-cantilever-directional-bending | 2 / 1 / 1 | 首试超时，retry 在约81.35秒后返回 HTTP 500；没有完整模型响应 | received usage 仍为0，含义仅是未收到完整响应；active wall 约986.49秒 |

两题工作区最终仍为空，知识资料、API discovery、exact Card、Abaqus 和 Solver 调用均为0，也没有遗留 Abaqus/Standard 进程。plate 的成功中间响应证明 `CodingRunService` 能从持久状态恢复同一会话并继续 tool loop；历史、累计 usage、active wall 和工具计数均未清零。实验同时表明本次 OpenCode 长上下文服务不稳定：短 doctor 可成功，但长请求多次在180秒超时或返回500。它仍不能用于判断知识包、Catalog 或工程任务能力。

冻结快照的派生报告只把 `ModelAPIError` 归为 `provider_interrupted`，所以 solid 的 `ModelHTTPError` 行显示为较宽泛的 `interrupted`。原始 attempt 和 resume state 明确记录 HTTP 500；当前源码已把两种 Provider 错误统一归类，冻结快照和本批证据不回填。该次授权执行一次后结束，当时没有创建第4次 attempt。

用户之后再次明确授权继续重试，因此在同一状态上各增加一次 `004` resume attempt；这是第二次事后恢复授权，不改变正式批次和第一次事后恢复的记录。续跑前 doctor 再次成功，manifest、seal、合同和预算仍未修改。

- `plate-with-hole-tension`：本次首个请求在180秒超时，retry 约144.78秒后返回 HTTP 500，没有新增完整模型响应。累计 received usage 保持2 requests、4 tool calls；active wall 增至约1681.61秒。
- `solid-cantilever-directional-bending`：恢复后的首个请求约11.55秒成功，模型加载 `abaqus-2024-coding` 并检查空工作区；下一请求及 retry 均在180秒超时。累计新增1个完整模型响应、2个 tool calls、7086 input、780 output、6912 cache-read tokens；active wall 增至约1359.38秒。

两题仍没有工作区文件、知识读取、API 查询或 Abaqus/Solver 调用，也没有遗留运行进程。第二次观察再次证明同一 run 可以从持久状态继续并累计 usage，但单个成功响应之后的下一轮仍可能中断；当前主要限制仍是 OpenCode 长请求可用性，未进入足以评价 Agent、知识包或 API Catalog 的行为阶段。
