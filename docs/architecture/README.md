# 架构文档

本目录描述当前首轮产品调用链和已经验证的边界。历史 Protocol、Graph State、Mock、被替代 ADR 与
scratch 可以保留研究价值，但不能据此宣称当前已经采用 LangGraph、多 Agent、数据库或持久 Job Service。
当前另有 Harness/file-backed CodingRun 会话续跑；它不改变上述外部 Job 边界。

## 从哪里开始

- [项目架构](overview.md)：当前能力、运行拓扑、状态所有权、Gold 和独立 retrieval 基线。
- [当前系统框架](system-framework.md)：宿主 Python 与 Abaqus vendor Python、组件和执行边界。
- [首轮 Agent 与实现范围](agent-implementation-scope.md)：Pydantic AI Harness Coder、typed decision、Skill 和三个 runtime tools。
- [一次 CodingRun](flows/simulation-run.md)：澄清、编写、分阶段执行、宿主验证、有限修复和中断续跑。
- [当前 Solver 生命周期](flows/job-lifecycle.md)：同步 solver 调用、进程证据和当前没有的 durable 语义。
- [技术栈与工程讲解点](technology-stack.md)：每项依赖的真实职责、证据和秋招讲解边界。
- [架构决定](../adr/README.md)：当前有效和已替代的决定。

## 阅读口径

首轮分为四个实际边界：模型可读写的 workspace、Pydantic AI/CodingRun 宿主控制、Abaqus 2024 外部
进程，以及模型不可见的 verifier/state evidence。官方文档 retrieval 是独立能力，默认 Gold 保持
B0 no-RAG；Exact API Catalog 是显式开启的非向量能力，旧 B1 从零建板 smoke 未观察到收益，已有脚本 repair
holdout 小样本观察到 9/9 对 8/9 的增量；修正旧 B1 机制后，从零建板 Skill/full/automatic 为
2/3、2/3、3/3。当前 final15 为5,480 cards/48 conflicts、16条 verified notes，method/parameter
benchmark为27/27；API repair小样本只对Assembly.Set观察到强长尾下降，对Surface是mixed positive，
setElementType和BeamSection fixture未观察到调用节省。final13在真实B31 Gold为1/3，唯一task-started
attempt采用FieldOutput note并通过；无allowlist的final15 treatment为0/3。当前v7增加task-owned runtime
allowlist后为2/3，两个pass只用3/4次Abaqus且无diagnostic脚本；不作单因果声明。Product Coder 已按
[ADR-0015](../adr/0015-disable-general-shell-for-product-coder.md)移除通用Shell。Concept文档查询已经作为
显式、受预算工具接入，但默认off且任务收益待评测。另有默认关闭的Solver diagnostic advisory把结构化
warning/error机械组query后以固定RRF附回同一tool return；RF3 3×2对照两臂均3/3，未观察到成功率或
Abaqus/Solver调用收益。文档中
提到未来技术时只表示重新评估条件，不构成路线图承诺。

早期advisory preflight被真实B31模型逐次原样绕过，已由
[ADR-0018](../adr/0018-enforce-verified-abaqus-aci.md)替代为显式分级ACI：production edit先过Python 3.10
候选语法门，运行请求再按权限、当前hash、verified API blocker和预算进入Abaqus；warning不阻断，模型
不能自行解除blocker。离线21正例0 blocker/16 warning、5 licensed负例5/5 blocker；首次正式批次3/3
pre-file timeout，尚无Product adoption，因此默认off且不替代v7/runtime/verifier。

三个固定 from-scratch Gold 当前都已有 Product gate：plate 3/3、3D bar 2/3、B31 current frozen v7 2/3。
B31 两个 task-started attempt 均通过 oracle，但还保留一次文件前 timeout 和一条10次Abaqus的高成本成功；
三条 smoke 都不构成三个合同之外的通用 CAE 能力。

L1 model change 另有 verified-source 边界：source plate run 先由宿主核验并缓存两份脚本 bytes，新的
workspace 只从缓存建立；前后 snapshot、patch 与 case-specific INP/ODB oracle 留在 workspace 外。suite
覆盖中心孔、双材料和 traction 三类变更：双材料为2/3，载荷 corrected off/full Catalog 为0/3、2/3，
no-Shell/精细 verifier follow-up 为3/3；中心孔 v2 保持原 extractor 并由 host oracle 验收新指标，结果为
1/3。尚无全套 suite pass，详见
[ADR-0011](../adr/0011-verified-seed-model-change.md) 与
[ADR-0012](../adr/0012-per-case-model-change-suite.md)、
[ADR-0013](../adr/0013-host-owned-model-change-acceptance.md)。

verified-source 边界也已扩展到 B31，但不并入 plate 三Case总gate。B31 source loader 重新验证 Gold 与六个
artifact hash；20×10 section change 只修改 build script、保持 source extractor bytes，deterministic为1/1、
Product为3/3，并由独立oracle验收±42 kN与原合同回归。它仍只代表一个线弹性轴向需求变更。
正交的 tip-load change 同时修改 build 与 extractor，把右端U1改为 +Y 1000 N，并由独立INP/ODB合同验收
U2/RF2/RM3/SM、精确U/RF/SF输出、近零轴向响应和原模型回归。初始Product为3/3但使用8/9/6次
Abaqus；公开原先隐藏的结果定义与作者/读取边界并修正subset verifier后，当前v2正式与严格回放均3/3，
三次都为3次Abaqus/1次Solver、无diagnostic脚本。它只证明一个线性悬臂梁合同闭环和小样本收敛改善。
