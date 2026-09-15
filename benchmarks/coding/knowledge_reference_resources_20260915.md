# 官方脚本参考与 Skill 整理：离线实施记录

2026-09-15。按用户要求直接读取本机官方资料，整理五个基础 Skill，并完成显式工厂的只读附件接线。没有调用真实模型、执行 Abaqus、重跑旧批次或提交 Git。

## 实际来源与本地导出

- 本机文档根：`D:/SIMULIA/Documentation/2026LE/English`；来源标签沿现有语料的 `2026 FD02`，不作为 2024 runtime 兼容证明。
- 页面：`SIMACAECMDRefMap/simacmd-c-intexabeamscript.htm`，正文含完整 `beamExample.py`。Connecting lug、frame 等附录页也存在，本轮只选悬臂梁，不批量导入。
- 页面 SHA-256：`415d3b8ff51a4447d17df96e6a7500ba90e3639ebb79de4faac00490e2ecc1c4`。
- 提取代码 SHA-256：`3aa84ff1e7815f69fb13294e5e157f70bb53c5382e045616e4c355bd31ba8755`。
- 最终本地资料：`var/knowledge/official-beam-20260915-final`。保留原 HTML、代码、项目编写的短说明、README 和来源/hash 清单；厂商全文未加入可提交源码。

代码仅提取 HTML codeblock 的文本，不清洗或执行。Python 3.10 语法解析通过；原稿的 backwardCompatibility、旧 tuple region、GUI、submit/wait 均保留并在阅读提示中指出。它是顶部面压力、独立实例网格和 C3D8I 的实体梁示例，不是端部集中力解析题，也未提供本项目的 JSON 提取与独立验收。

可重复导入命令（输出目录必须为新目录）：

```powershell
uv run --no-sync python -m abaqus_sim_agent.skills.import_official_example `
  --page D:/SIMULIA/Documentation/2026LE/English/SIMACAECMDRefMap/simacmd-c-intexabeamscript.htm `
  --source-release '2026 FD02 (local 2026LE documentation)' `
  --output var/knowledge/<new-directory>
```

## 已实现范围

五个 Skill 的名称/description 与显式开关保持原样。formulation 合并重复的退出条件；modeling 保留物理判断并补可选示例导航；discretization 去掉正文已覆盖的重复禁止清单；postprocess 保留位置/外推/平均区别并提示结果显示不等于数值提取；verification 补充参考解必须匹配载荷与约束的适用性提示。原理与必要条件没有迁成空的外链目录。

工厂增加可选 `knowledge_resources_dir`，默认 None。它复用 Harness 0.24.0 的 FileSystem，只暴露 `knowledge_list_directory`、`knowledge_read_file` 两个带命名区分的只读工具；仅允许 Markdown/Python 文件，单次最多 300 行，可按 offset 续读。资料根不得与工作区或私有 state 重叠，路径和 symlink 的访问检查复用 Harness。工具参数/返回和 effects 进入原生历史/StepPersistence，不增加知识状态机。

调用者可在已有工厂调用中传入最终导出目录；基础五个 Skill 仍通过 `foundation_skill_profile="linear_structural_fea_v1"` 单独选择。工具读取不执行示例，也不将案例关联 ID 记作完整 API Card 已读。

## 验证与界限

- 新导入器/读取测试与既有 Agent、CLI、resume、Foundation 合同测试共 **102 passed**；使用 FunctionModel，0 真实模型、0 Abaqus。覆盖提取/转义/hash、错误页面/代码与拒绝覆盖、默认不暴露、分页与调用历史、越界/非法文件类型/负 offset/过大 limit、符号链接逃逸和 alias 绕过、资料根重叠。
- 三个改动源码模块 BasedPyright 0 errors；相关 Ruff 通过。六个 Skill 经 quick_validate 检查通过（Windows 使用 `python -X utf8`）。这些检查不证明模型能够正确采用专业知识。
- 普通 CLI/LaunchManifest 尚无知识资料选择或内容快照绑定；本轮仅为显式工厂/评测读取入口。调用者必须冻结资料目录，不能据此声称同 run 资料漂移保护或完整知识包恢复。
- 没有默认启用、真实任务收益或官方脚本的 2024 执行结果。当前 Skill 文本已变化，后续比较必须绑定新内容，旧封存副本和历史成绩保留。
