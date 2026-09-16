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
