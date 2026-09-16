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

用户之后多次明确要求在原状态继续，不新建替换 trial。所有操作均保留原 run、conversation、workspace、history、usage、freshness baseline 和 execution journal；manifest、seal、任务与输入资料未修改。原冻结批次只允许一次显式 resume，因此下面结果全部是 `formal_batch=false` 的 post-hoc 观察，不能回填上面的正式分数。

两题先在原预算内分别累计到8次和8次 attempt，并由2700秒 wall门禁终止。随后使用产品已有的 operational amendment 继续：plate 依次把总 wall 上限调到3600秒、模型请求上限调到60；solid 依次把总 wall上限调到3600、5400、7200秒，最后同时把模型请求上限调到60。amendment 没有建立新 task revision，也没有清零消耗或重新划定 evidence scope。最终 plate 共8次 attempt，solid 共15次 attempt；逐次快照和授权控制记录保留在各题 `state/attempts/` 与批次 `control/`。

### Post-hoc 最终结果

| Case | 自动结果 | Requests / tools | API / Abaqus / Solver | Active wall | 主要数值 |
|---|---|---:|---:|---:|---|
| plate-with-hole-tension | `formal_pass` | 51 / 60 | 7 / 16 / 4 | 3198.53 s | `hole_max_s11=317.19 Pa`，`reaction_x=-499.9999996 N`，粗细变化0.731% |
| solid-cantilever-directional-bending | `formal_pass` | 59 / 68 | 8 / 16 / 3 | 6156.26 s | `U1=2.997642e-5 in`、`U2=0.107916 in`、`U3=0.430986 in`，对应反力均约-1 lbf，粗细变化0.00%–0.27% |

两题均生成 fresh CAE、粗细INP/ODB、独立提取脚本、结果JSON和报告，并由 results-first 宿主 verifier 接受。plate 累计收到3,609,944 input、68,827 output tokens；solid 累计收到5,285,278 input、97,010 output tokens。失败调用可能还有服务端消耗，客户端无法计费重建。如此高的累计开销说明恢复路径有效，但当前配置下并不高效或稳定。

Catalog 最终被实际采用，但只有 exact lookup：plate 和 solid 分别计入7次和8次 API lookup；两题都没有调用 `discover_abaqus_api`，也没有读取随批次绑定的官方知识资料。因此本实验支持“Exact Card 与 runtime feedback 可进入完整修复闭环”，不支持 discovery 或脚本知识包带来任务收益的结论。

### 180秒超时诊断

solid 在一个固定模型轮次反复出现 ReadTimeout。同期短 doctor 和 plate 请求能够成功，solid 当时的 history 约75 KB，明显小于 plate 后期约966 KB，不能归因于全局服务不可用或上下文规模过大。增加仅记录安全形状信息的 post-hoc transport 后，待发送请求为42,300 bytes、14条messages、21个tools、非流式；相同 body SHA 在180秒配置下反复超时。

诊断性地保持同一 run/session/history，将单次 timeout 临时改为600秒并关闭 SDK retry。相同请求在287.42秒收到HTTP 200，完整响应59,509 bytes，随后 Agent 继续完成建模、API查证、CAE修复、两次求解、ODB提取和验收。由此可以确认：180秒阈值会取消仍可能成功的长生成；现有客户端证据不能继续区分模型推理耗时与服务端排队。该 timeout override 没有写入产品默认配置，也不是当前 operational amendment schema 支持的字段。

### 声明边界

post-hoc 派生报告当前两题均为 `formal_pass`，含义是自动 results-first verifier 接受了最终产物和独立数值证据。最终脚本尚未完成独立 script review，不能把 Agent 报告中的全部实现解释直接视为已审事实。正式冻结批次的 Provider 中断结论、原尝试、原 seal 和原报告时点继续保留；post-hoc 成功证明同一 CodingRun 可恢复并完成，不证明原 profile 的一次成功率或知识/discovery净收益。
