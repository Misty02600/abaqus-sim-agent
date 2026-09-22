# ADR-0028：将本机私有领域知识包作为只读内容快照绑定到 CodingRun

## 状态

已采纳

## 日期

2026-09-16

## 当时遇到了什么

现有产品已经有包内 Abaqus 编码 Skill、可选力学 Skill、Exact API Catalog、官方文档检索，以及一个只供
显式工厂/评测使用的 `knowledge_resources_dir`。但这些入口没有形成“用户提供一份专业知识内容，并让
普通 CodingRun 可恢复地使用”的公开合同：`--skills-dir` 只替换整个 Skill 库，Agent 仍按固定名称选择
Skill；只读资料目录没有进入 launch，也不保证恢复时内容不漂移。

用户希望把柔性电子等专业资料整理为本机领域包，并允许当前机器的维护者自行添加。这里存在两个不同方向：把它做成
任意可执行插件，或者只允许用户提供受控内容。前者会让包内代码、安装钩子或自由文本间接改变 Agent 权限、
预算和验收；后者可以满足知识扩展，同时保持当前单 Coder、三个 runtime tools 和宿主 verifier 的边界。

## 最后决定

产品支持本机维护者在新 CodingRun 启动时显式选择一个或多个私有 `DomainKnowledgePack`。领域包是只读、
数据型、可绑定内容身份的专业资料集合，不是可分发插件、Agent、工具提供者或跨 run Memory。

- 首版只接受维护者显式给出的本机目录，不自动扫描工作区、用户目录或网络位置，也不提供导入/导出协议、
  远程注册表、市场、依赖求解、安装钩子或包内自注册代码。目录内容留在本机，不作为项目发布物。
- 仓库内作者目录默认放在已忽略的 `var/knowledge/private/<pack_id>/`，也可以使用维护者显式指定的仓库外
  本机路径。包正文不进入 `src/abaqus_sim_agent`、wheel、Git 跟踪文档或公开 benchmark。
- 每个包使用 strict `pack.json` 声明稳定 `pack_id`、作者 release、用途摘要、入口、兼容范围和明确列出的
  文本资源。作者不手填文件 hash；宿主按实际读取的 bytes 生成逐文件 hash、包 digest 和 bundle digest。
- 新 run 在任何模型调用前校验并把所选内容复制到模型不可写的 state 目录。每个工作段从该私有快照校验后
  物化到临时只读根目录，继续复用有界文件读取工具；模型不直接读取 state。源目录在启动后改变或消失，不
  改变当前 run；私有快照缺失或损坏时在模型调用前失败关闭。
- launch 绑定包列表、release、digest 和 state 内相对快照位置。恢复、普通 continuation 和显式 task
  revision 继续使用原绑定；首版不通过 amendment 切换知识包。改变包集合或内容时创建新 CodingRun。
- 私有包的 `SKILL.md`（若存在）也只是可读资料，不注册为 Harness instructions。模型只接收宿主生成的短
  导航和通用读取约束，避免包正文把自身指令提升为系统行为。随产品发布、由项目维护的既有 Skills 继续走
  原受信任装配，两类入口不合并。
- 包内 `.py`、`.inp`、YAML/JSON 等文件只能作为参考文本读取。采用后的普通脚本必须写入当前 workspace，
  所有 Abaqus 副作用仍只经三个 typed runtime tools；领域包不能增加工具、Shell、预算、路径权限、
  verifier 或完成语义。
- 多包按 `pack_id` 命名空间并存，不定义静默覆盖或“后加载者优先”。有冲突时保留各自来源、范围和内容
  身份，由 Coder 结合当前任务事实判断；用户/工作区事实不能由案例值补造。
- API Catalog、Concept 文档检索和领域包继续拥有不同证据语义。领域包可以引用它们，但不复制成一个无
  边界真值库；成功读取资料不等于 API 已精确查证、脚本已运行或任务已通过验收。

首版由 `coding` 拥有 pack schema、快照、launch binding、临时物化和模型可见适配，不新增顶层 Python
组件。state 拥有当前 run 的不可变内容快照；原作者目录仍由本机维护者拥有。本决定不为将来的跨机器消费、
公开发布或第三方分发预设格式兼容承诺。

## 为什么这样选

显式本机选择加 run 快照已经满足私有资料扩展、来源隔离和可恢复复现，不需要建设通用插件生态。把内容
视为数据还保留了当前最重要的可信边界：Agent 可以阅读和适配专业资料，但不能借资料改变宿主权限或宣告
物理正确。

快照放在 state 而模型只读取临时物化副本，既能延续 state 对 launch/恢复证据的所有权，也不必放宽模型
读取私有 state 的规则。调用历史已经记录实际文件读取及返回内容；首版不再增加第二套逐条 receipt 状态机。

## 没有采用的方案

- 直接把任意用户目录加入 Harness `Skills`：附件不会自动读取，且包作者正文会进入 instructions，混淆
  内容与权限。
- 只在 launch 保存源目录路径并在恢复时重读：实现较少，但源内容漂移或离线会使同一 run 不可复现。
- 把包复制到 workspace：读取方便，但 Coder 可以修改资料，旧案例还可能被误当成当前 fresh artifact。
- 建设全局安装仓库、签名、依赖和远程市场：不属于当前本机私有资料需求，也不能改善专业内容质量。
- 允许包执行导入脚本或提供新 tools：会绕过 ADR-0015 的 no-Shell、三个 runtime 工具和宿主验收边界。

## 带来的影响

- 有利：本机维护者可以增加专业领域内容，而不需要修改产品包或恢复第二套 Agent/runtime。
- 有利：每个 run 的知识输入可按 digest 复现，源目录更新不会静默改写进行中任务或旧成绩。
- 有利：用户包与受信任产品 Skill、API 真值、官方文档和执行证据的责任边界可直接说明和测试。
- 代价：每个 run 保存一份所选文本快照，并在工作段开始时校验和临时物化；首版没有跨 run 去重仓库。
- 代价：用户需要维护明确资源清单；未列出的文件不会被模型读取。
- 风险：只读和 hash 只能证明内容身份，不能证明专业结论正确；内容审查与任务收益仍需独立证据。
- 风险：本机维护者仍可能选择低质量或恶意文本；宿主权限隔离降低执行风险，但不能保证模型不受
  错误内容影响。

## 落实与确认

- 接受依据：用户于 2026-09-16 认可“允许自由添加只读内容包，而不是任意可执行插件”，并要求明确具体
  方案；随后进一步明确该包只在本机私有使用，不进入仓库 plan，也不面向分发。
- 该能力如后续实现，只做本地 schema/path/resume 测试、模型行为观察和按需的本机持证 Abaqus 检查；这些
  包专属任务、模型输出和检查记录保存在已忽略的 `var/audits/`，不形成可分发测试夹具或公开 benchmark，
  也不作为第三方兼容、可分发性或通用任务收益声明。若通用加载器进入产品源码，其不含领域内容的边界/
  恢复回归仍按普通产品代码维护。
- 本 ADR 记录已接受的产品边界，不表示普通 CLI、恢复或 Web 已经实现该能力。

## 相关文档

- [知识接入与证据边界](../architecture/knowledge-access.md)
- [领域知识包调研与设计修正](../scratch/domain-knowledge-packs-and-progressive-disclosure.md)
- [ADR-0005：可追溯地吸收与重构上游](0005-traceable-upstream-reuse.md)
- [ADR-0015：产品 Coder 不暴露通用 Shell](0015-disable-general-shell-for-product-coder.md)
- [ADR-0017：以扁平组件图组织 CodingRun 产品包](0017-package-by-component-coding-run.md)
- [ADR-0020：使用 Harness StepPersistence 续跑 CodingRun](0020-resume-coding-runs-with-harness-step-persistence.md)
