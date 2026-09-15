# 官方悬臂梁脚本：阅读提示

参考来源为 Abaqus Scripting Guide 的 “The cantilever beam example script”，页面标识 `simacmd-c-intexabeamscript`。代码从本机文档提取，具体来源版本与 hash 由导出目录的 source.json 记录。官方原稿仅保留在本机资料目录，不作为开源包中的厂商代码发布。

本项目另做过一次最小适配验证：移除视口显示，把脚本内提交改成生成 CAE/INP，再通过独立 Solver 和 ODB 读取运行。Abaqus 2024 完成建模与求解，压力合力和固定端反力平衡；它支持下面列出的核心模型路径，不等于原始 GUI/提交脚本已原样验证或任意悬臂梁已经验收。详细范围见 `benchmarks/coding/knowledge_reference_resources_20260915.md`。

需要参考实体草图/挤出、材料/截面、装配区域、静力步、实例网格等组合方式时，可按需读 `beamExample.py`。文件已有阶段注释，可直接定位，不必加载无关段落。

知识资料保留官方原稿，包括 GUI、Viewport、提交和显示代码；不要求为了入库先删除。Agent 只在当前交付需要时把相关部分适配到工作区：显示操作可以忽略或留给需要 session/Visualization 的任务，Solver 提交必须改走宿主 `run_solver_job`。保存原文与限制当前运行脚本是两个边界。

## 移用前最需要注意的差异

- 这是实体悬臂梁：左端固支、顶部整面压力，采用 C3D8I。不是梁单元或端部集中力示例；解析比较必须匹配其载荷分布、几何与目标量。
- 几何、材料、压力和网格值是示例输入；当前任务使用自己的已知输入。原脚本没有完整单位声明，不能替用户据此猜单位。
- 原稿创建 `dependent=OFF` 实例，并在 Assembly 层播种和划网格。它不能证明当前 Part 网格或 dependent instance 应照用相同调用。
- 原稿启用 `backwardCompatibility`，并使用旧式 tuple 区域。该组合在上述 2024 适配验证中可运行；本次没有单独消融兼容开关，不能据此判断开关是否必需，也不把旧式形式升级为通用推荐。
- 视口操作、`submit()`、`waitForCompletion()` 和打开结果显示属于官方教程流程。本项目适配脚本按三阶段工具执行：建模生成 CAE/INP，Solver 工具提交，独立提取脚本读取 ODB。原稿没有本项目要求的 JSON 提取与独立验收。当前 Skill 和工具说明已经表达该边界，但 CAE 脚本内容的确定性 `submit/wait` 阻断尚未实施，不能宣称提示本身构成硬限制。

这些说明用于识别示例与当前任务的差异，不要求每次阅读都重做一套审查，也不授予新的运行权限。
