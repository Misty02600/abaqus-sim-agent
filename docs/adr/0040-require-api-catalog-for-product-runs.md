# ADR-0040：普通产品运行必须绑定 API Catalog

## 状态

已采纳（2026-09-23）。用户确认取消普通产品的 `off` 选项，由 Codex 实施。

## 背景

旧入口把 Catalog 作为可选能力：未传目录时使用 `off`，传目录时默认 `full`。这同时混合了两个问题：
是否绑定知识源，以及是否把主动查询工具暴露给模型。实际上 `automatic_verified_only` 不暴露查询工具，
只在可确定的运行时报错后附加已经过 Abaqus 2024 验证的短提示，适合作为普通产品的低干扰默认值。

CLI 与 Web 对 Catalog 的接线也不一致：CLI 可选配置，Web 新会话没有配置入口。继续保留 `off` 会增加
产品组合和评测解释成本，并允许不同入口在没有明确理由时使用不同知识基线。

## 决定

- 新普通 CLI 和 Web 会话必须提供工作区外的不可变 Catalog 目录。宿主在模型调用前校验 artifact、保存
  request binding，并在恢复时重新核对相同 hash；缺失、损坏或绑定不一致时拒绝运行。
- 产品默认模式为 `automatic_verified_only`。该模式不注册 `lookup_abaqus_api` 或
  `discover_abaqus_api`；只有符合条件的非超时运行失败才可能收到 verified runtime note。
- 产品仍可显式选择 `automatic_exact_signature`、`full` 或 `full_with_discovery`。后两者分别暴露精确
  查询工具及精确查询加 receiver 方法目录。候选名称仍须继续 exact lookup 才构成卡片证据。
- `off` 从 CLI/Web 的产品参数中移除。launch 升到 v10，并要求非空 Catalog 路径和已启用模式；旧开发期
  launch 不恢复。
- 新的静态反馈配对评测在 control/treatment 两臂绑定同一 Catalog，并固定为
  `automatic_verified_only`，只改变静态检查变量。已冻结报告、manifest 和为复现实验对照而保留的旧 evaluator
  `off` 字样不改写，也不重新解释为当前产品选项。
- Catalog 只提供 API 证据，不能补造材料、尺寸、载荷等物理输入；静态声明、verified note、实际执行成功
  和物理验收仍是不同证据。

## 取舍与验证

没有把完整主动查询设为默认，因为它会增加模型工具选择和上下文成本。没有把某个本机构建目录写死进包，
因为 Catalog 的来源、版本和 hash 必须由部署者明确选择；安装包也不应悄悄替换已绑定 artifact。

代价是新运行多一个必需路径，并且 Catalog artifact 不可用时产品 fail closed。离线测试覆盖 CLI 参数、Web
配置、默认工具可见性、binding、恢复和 launch v9 拒绝；这不证明 Catalog 会让所有任务成功，也不回填旧评测成绩。

本决定更新 [ADR-0009](0009-exact-abaqus-api-catalog.md) 的普通产品默认启用策略，沿用其来源分层、trace 和
证据边界；旧评测合同继续按 [ADR-0030](0030-drop-pre-release-coding-run-state-compatibility.md) 保留。
