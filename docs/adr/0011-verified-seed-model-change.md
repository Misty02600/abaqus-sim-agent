# ADR-0011：以已验证 CodingRun 作为现有模型变更的种子

## 状态

已采纳

## 日期

2026-08-25

## 当时遇到了什么

首个二维板 Gold 已经证明从空工作区完成“澄清 → 建模 → 求解 → ODB 提取 → 独立验收”的 L0 能力，
但项目仍容易被理解为只为一个教学算例搭建了复杂评测。与此同时，围绕 Skill、API Catalog、runtime note
和 ODB 容器继续逐项补语义没有自然终点，也没有直接证明 Agent 能理解并修改一份已有工程脚本。

现有 `CodingRun`、seeded API-repair workspace、初始 snapshot、三个 typed Abaqus 工具和宿主 verifier 已经
具备实现 L1 需求变更的基础。需要增加的不是多 Agent 或更多领域提示，而是一条把“已验证基础模型”作为
前置证据，并同时验收新需求与旧合同的真实调用链。

## 最后决定

保留现有空工作区 plate Gold 作为 L0，不用“修改已有脚本”替代从零建模。新增独立的 L1
`plate-hole-change-v1` evaluator，并固定以下边界：

- source 必须是已完成、通过 private oracle、正确澄清 `plate_thickness=1 mm`，且六个关键 artifact hash
  与 outcome 一致的 `plate-tension-v4` CodingRun；任意脚本目录不能冒充 verified seed。
- 新 attempt 创建空评测目录，只复制 source 的 `build_model.py` 与 `extract_results.py`，随后捕获 initial
  snapshot。旧 CAE/INP/ODB/results、source state、oracle 和隐藏容差不进入 Agent workspace。
- 首个变更固定为在 100×20×1 mm 完整平面应力板中心 `(50, 10)` 增加半径 3 mm 的贯穿孔；材料、单
  Static step、`NLGEOM=OFF`、三个位移约束、U/S/RF、文件名与 CAE→Solver→odbAccess 阶段合同必须保留。
  原 20×4 网格不再是回归不变量；新网格只允许 CPS4R，孔边至少 24 条单元边。
- Agent 仍使用通用 Abaqus Skill；这个 case 不向 Skill、Catalog 或 task instructions 注入建孔 API、正确
  脚本、ODB 容器配方或 oracle 数值。首轮评测显式关闭 API Catalog、Concept retrieval 和 Planning。
- 宿主从 INP 检查材料、厚度、step、输出、边界记录和禁止额外载荷；private ODB oracle 重建网格边界环、
  孔心/半径、网格面积、CPS4R、位移、反力平衡和孔边应力集中，并与 Agent `results.json` 交叉检查。
- state 额外保存 source provenance、initial/final snapshot、created/modified/deleted 清单和两份源码 unified
  diff。diff 用于展示和失败分析，不用修改行数替代语义验收。
- `CodingRunService.verify_on_budget_exhaustion` 默认关闭；L1 evaluator 显式启用。模型或运行预算耗尽时，
  宿主可对终态 artifact 做一次独立验收：完整且正确才形成 completed outcome，缺文件或合同错误仍失败。
- 语义扩展改为任务失败驱动：只有同一知识缺口在正式任务中重复阻断并值得作为稳定通用规则时，才重新
  评估 Skill/Catalog；不再按模型类型主动填充答案。当前 `OdbMeshElement.nodeLabels` 失败保留为已知限制。

## 为什么这样选

source run 的 request、outcome、verification 与 artifact hash 把 L0 和 L1 串成可追溯能力阶梯；只复制
脚本又避免旧 ODB 冒充当前结果。INP/ODB 双重宿主验收让“增加孔洞”不是文本 diff 或模型自报，而是可
执行、可复核的需求变更。终态验收符合既有所有权：Agent 只能生产 artifact 和 decision，最终成功始终由
宿主 verifier 决定；它也避免模型在完成产物后因多做一次 probe 而机械丢弃正确结果。

## 没有采用的方案

- 继续为 Beam、Shell、ODB 容器逐项扩写 Skill：没有退出条件，并会把 case 答案变成提示词知识。
- 删除 L0，只展示已有脚本修改：无法证明 Agent 能从明确需求独立完成基础模型。
- 把 host reference script 作为 seed：会泄漏目标实现，不能测试真正的需求理解与迁移。
- 复制 source 的 CAE/INP/ODB/results 到新 workspace：增加 stale artifact 和旧结果复用路径；当前脚本可
  重建场景不需要二进制 seed。
- 用源码相似度或最小修改行数判成功：无法证明几何、物理和未要求改变的合同正确。
- 预算耗尽直接无条件接受当前文件：会把缺少 `results.json`、错误 ODB 或未完成 Solver 误标为成功。

## 带来的影响

- 有利：项目首次形成 L0 基础建模 → L1 已有模型需求变更 → runtime failure repair 的完整展示层级。
- 有利：每个 change attempt 都有 verified source、源码 diff、执行 provenance、INP contract 和 ODB oracle。
- 代价：首个 L1 verifier 是中心孔专用合同，不代表任意 CAE 变更。
- 代价：孔洞不再有均匀应力解析解，需要网格拓扑、平衡和相对物理不变量共同验收。
- 风险：Go provider 首轮 timeout 和模型在 ODB probe 上的长尾仍显著影响稳定成功率。

## 落实与确认

- 实施情况：seed validation、`plate_change` evaluator、INP/ODB verifier、host-only oracle、workspace diff、
  opt-in budget terminal verification、纯单元测试和 licensed reference integration 已进入代码。
- deterministic reference：真实 Abaqus 2024 为 1/1，通过 250 个 CPS4R、两个边界环、孔边 24 条边、
  孔半径、面积、位移、反力平衡和孔边应力集中检查。
- Product-Agent：一次独立 smoke 完整通过，期间根据真实 CAE traceback 删除无效 `Sketch.cut`，并修复
  Abaqus `float32` JSON 序列化；19 次模型请求、7 次 Abaqus 调用、1 次 Solver。
- 稳定性：初始预算三重复为 0/3；唯一一次预算 treatment 三重复也为 0/3。两批合计出现 transport、
  建模长尾和 ODB extraction 长尾；不能声称 L1 已通过 2/3 gate。
- 冻结证据：`benchmarks/coding/plate_hole_change_evaluation_20260825.json`。
- 后续扩展：ADR-0012 在不改变本决定的 verified-seed 边界下增加双材料和载荷模式 Case，并把 source、
  runtime/evidence 与 case-specific physics verifier 分离。
- B31 落实：相同 verified-seed 边界已抽成结构化 seed Protocol，并新增 beam source loader；它重验对应
  B31 Gold request、completed outcome、passing verification、零澄清和六个artifact hash，只复制两份脚本。
  首个beam Case把10×10截面改为20×10，只允许build patch、要求extractor patch为空；共享provenance通过
  `beam_tension` artifact stem复用，case oracle验收±42 kN和原合同。deterministic为1/1，Catalog/Concept/
  Planning关闭的Product批次为3/3，三次均3次Abaqus/1次Solver。该结果扩展实现范围，不改变“每个变更
  需要独立物理合同”的决定，也不构成任意梁修改。
- 第二个beam Case验证“结果消费者也受需求影响”的边界：右端轴向规定位移改为 +Y 1000 N 集中力时，
  build与extractor都必须有patch。独立INP合同验收左端Encastre、右端DOF2 CLOAD和无其他载荷，ODB oracle
  验收U2、RF2、RM3、SF/SM及近零轴向响应，并保持geometry/10×10 section/material/orientation/step/mesh
  回归。初始Product批次为3/3但使用8/9/6次Abaqus；轨迹审计发现结果字段含义和作者/读取边界未公开，
  还发现INP verifier只做U/RF/SF子集判断。修正为精确输出集合后，中间read-card treatment从原判3/3回放为
  1/3；最终`declared_odb_contract_v2`明确作者侧U/RF/SF、读取侧伴随RM/SM与最大SM向量模，保持Skill、模型、
  预算和oracle不变，正式与严格回放均3/3，三次都只用3次Abaqus/1次Solver、无diagnostic脚本。该落实证据
  支持“按变更影响范围公开消费合同”，不改变逐Case独立验收和小样本边界，也不把B31解法晋升为全局Skill。

## 相关文档

- [项目架构](../architecture/overview.md)
- [一次 CodingRun 的工作流](../architecture/flows/simulation-run.md)
- [B31 bending 收敛与合同证据](../../benchmarks/coding/beam_bending_convergence_evaluation_20260827.json)
- [ADR-0004：以证据和物理验证定义任务成功](0004-evidence-first-physics-verification.md)
- [ADR-0008：首轮采用 Pydantic AI Harness 单 Coder](0008-pydantic-ai-harness-abaqus-coding-run.md)
