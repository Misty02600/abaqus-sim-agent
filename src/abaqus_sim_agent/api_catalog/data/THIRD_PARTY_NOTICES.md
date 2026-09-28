# API Catalog 来源与许可状态

本目录的 `manifest.json`、`methods.jsonl`、`members.jsonl` 来自项目本地构建产物
`abaqus-2024-reference-context-v2`，构建时间为 2026-09-14T13:45:48.886079Z。
2026-09-27 纳入应用包时按原始 bytes 复制，没有重新构建、修改卡片或回填历史评测。
原始输入摘要、版本、卡片摘要与数量保留在 manifest 中。

## 来源

- Abaqus 2026 FD02 官方 Scripting Reference（`SIMACAEKERRefMap`）：提取的说明、参数语义和引用。
  文档版权属于 Dassault Systèmes 或其相关权利人；公开再分发许可尚未确认。
- [abqpy](https://github.com/haiiliin/abqpy) 2024.10：从固定 wheel 静态提取声明及 docstring，
  未导入执行。wheel SHA-256 为
  `bdfe99139281489354c01f00b7ee8b96ee1d281b461441dcfe7fad5fda894504`。
  wheel 附带的 MIT 许可证原文见同目录 `ABQPY_LICENSE.txt`。
  该许可证不代替对其引用的第三方 Abaqus 文档内容的权利核验。
- 项目 Abaqus 2024 runtime notes：由本项目维护，原始输入摘要见 manifest 的 `runtime_notes`。

## 分发边界

项目根目录的 MIT 许可证适用于项目代码，不自动授予上述第三方文档内容的再分发权。
当前打包实现及本地安装验证不代表 Catalog 已获公开发布许可。
公开发布携带此目录的仓库、wheel 或 sdist 前，需要确认涉及的第三方内容许可。
本说明是来源与未决许可状态记录，不是权利人的授权书。
