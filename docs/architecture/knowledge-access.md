# 知识接入与证据边界

## 当前入口

| 入口 | 当前接线 | 返回什么 / 由谁使用 |
|---|---|---|
| `lookup_abaqus_api` | 新会话默认开启；可由通用工具选择排除 | qualified method、parameter、member 或 `Method.__doc__` 的精确证据 |
| `discover_abaqus_api` | 默认关闭；可由通用工具选择启用 | 已知 receiver 下的方法名称和 Card ID；供 Coder 选择后精确查证 |
| automatic Catalog | 固定保留报错后的自动提示，与主动工具开关独立 | 在可确定的错误上下文中附相关卡片及匹配的已验证说明；必要时给出受限替代名称 |
| `search_abaqus_docs` | 显式 Concept `tool`；默认 off | 本地官方文档候选；普通模式按需查询，允许零查询 |
| Solver diagnostic advisory | 独立、默认 off 的模式 | 非超时结构化 Solver warning/error 后附有界文档候选，不覆盖原运行结果 |
| `bounded_v1` document policy | 显式 policy spec + Concept tool，已接入同一 Coder；默认 off | need、receipt、assessment 与逐链预算；运行和 ready 前有宿主证据门禁 |
| 力学 Skill 与只读参考资料 | 显式 Agent 工厂 / 评测入口 | 基础层 `foundation_skill_profile="linear_structural_fea_v1"`；专题层 `structural_topic_skill_profile="structural_mechanics_v1"`；参考脚本仍由独立 `knowledge_resources_dir` 提供，普通 CLI/launch 尚无资料选择和快照绑定 |
| 本机私有领域知识包 | CLI `--knowledge-pack` / Web 新会话显式选择；默认 off | 从用户级应用数据根发现 strict manifest，launch v11 绑定 state 快照；模型只经两个只读文件工具按需读取 |

新普通 CLI/Web 会话自动读取随应用安装的 Catalog，不再接收 Catalog 目录配置，产品也不提供 `off`。
包内 `api_catalog/data` 保留 manifest、methods/members JSONL 的原始 bytes，locator 从安装包位置确定路径，
不依赖 cwd，也不复制到 AppData。launch v11 记录实际路径与工具集合，request 保存 schema、card/member hash 和
runtime release。创建和恢复均核对绑定；缺失、损坏或升级后内容与原会话不同，在模型调用前明确拒绝。
旧冻结报告和 evaluator 的显式 artifact 路径及 `off` 条件不回填。源码安装位于工程内时，包内资源也可能位于
workspace 下，因此不再用“必须在 workspace 外”判断 Catalog 可用性；此处不宣称隔离或不可被本机修改。

包内分发沿用提交 `85a57e9` 的资源 bytes：Abaqus 2026 FD02 文档、abqpy 2024.10 静态声明和项目 runtime notes
继续分别标来源；本次接线不重建卡片、不更新验证结论。公开再分发许可尚未确认，见
[来源与许可说明](../../src/abaqus_sim_agent/api_catalog/data/THIRD_PARTY_NOTICES.md)。

普通CLI/Web使用`tools`白名单和`exclude_tools`排除列表，保存解析后的`launch.tools`。
不提供普通产品的`api_catalog_mode`；工厂中该参数仅供显式评测继续解释原profile，
普通产品传入保存的工具集合，报错提示固定沿用最新测评的行为。旧产品v10显式拒绝，不自动迁移。
工具名称、依赖、协议工具和恢复边界见[配置合同](configuration-reference.md#通用工具选择)。

当前Catalog保留精确卡片与对象方法目录；旧BM25/E5意图搜索及Catalog多子命令CLI已删除。
两者不能仅因都叫discovery就视为同一产品接口。早期 [API 方案实验](../../benchmarks/api_catalog/api_discovery_loop_v1_20260915.md)
没有经过 CodingRunService；后续提交 `74fa6e3` 才加入下面的产品目录工具和恢复接线。
早期API开发实验和Foundation配对试验的专用实现已退出活动源码，历史证据与源码定位见
[测评入口](../../benchmarks/README.md#已退出维护的实验代码)。资源构建仅保留`abaqus-sim-agent-catalog`命令，
由`[project.scripts]`注册并直接调用[build.main](../../src/abaqus_sim_agent/api_catalog/build.py)，
来源摘要和不可变目录写入语义保持不变。
产品目录发现与精确查询、runtime notes构建输入、离线Catalog校验函数及两组Skill路由评测继续保留。

## API卡片返回中的多种官方定义

整卡查询直接按Catalog已有的官方记录组织说明。只有一条官方记录时沿用原来的顶层摘要和参数字段；
有多条时，顶层提示查看`official_variants`，不再把第一条记录的摘要、必选/可选参数及返回值作为整张卡片的唯一说明。
每条variant同时提供原文摘要、自己的参数名称与解释、返回说明、record_id和来源链接，不猜测或合并这些定义的含义。

相同的页级说明只返回一次，但内容、来源、源文件摘要或告警不同的说明仍分别保留；保留项的record_id引用首次记录，
所有官方定义仍列在variants中。卡片数据不重建，旧批次快照和原始记录不回填。参数精确查询与原始docstring读取沿用现有入口。

该规则适用于通用整卡返回，不是Region专用补丁；离线核查覆盖当前5224张方法卡，其中39张包含多条官方定义。
Region的完整返回因增加逐定义参数解释，从约13222字符变为15038字符，仍可能需要read_tool_result；
当前验证的是信息组织与来源保留，尚未通过真实模型对照证明减少误用。

## 产品 API discovery

新普通会话默认提供`lookup_abaqus_api`，不提供`discover_abaqus_api`。开启目录发现时必须同时保留精确查询，
因为候选本身不含卡片证据。关闭主动查询不关闭自动报错提示；Catalog资源仍然绑定到会话。
实际工具集合经Pydantic AI原生筛选，同时控制模型schema和可执行映射，提示同步反映选中的工具。

调用顺序：

1. Coder 确定对象类型，如 `Part`，调用 `discover_abaqus_api(receiver, query, limit)`。
2. 宿主在该 receiver 的方法目录中列候选；query 可选，limit 默认 20、范围 1–100。
3. Coder 用候选名称调用 `lookup_abaqus_api`，读取方法、参数或保留的 docstring 后再使用其证据。

工具会提示“候选不是 API 证据”，但没有通用机制证明模型实际遵守了这一推理顺序。确定性保证是：
discovery 的候选 ID 不进入已读卡片集合，也不会阻止随后 exact lookup。查到名字不证明签名、适用性或运行成功。

普通产品的 discovery 与 lookup 不设调用次数上限。有效、非重复且 Catalog 可用的查询进入累计 usage/journal；
无效或重复查询不增加计数。去重按规范化 receiver/query/limit，
不把候选误记成已交付的完整卡片或参数。

`api_discoveries.jsonl` 和 `api_lookups.jsonl` 保存在 workspace 外。恢复合并 journal 与 snapshot，继承
计数、查询去重、实际 delivered card/parameter 状态；旧 trace 没有 delivered 字段时不凭 card ID 猜造已读证据。
这些记录保证恢复后的工具状态，不保证历史文档知识始终正确。

源码与验证入口：[API 工具](../../src/abaqus_sim_agent/coding/api_lookup.py)、
[装配](../../src/abaqus_sim_agent/coding/agent.py)、[恢复](../../src/abaqus_sim_agent/coding/resume.py)、
[Agent 合同测试](../../tests/test_coding_agent.py)、[journal 恢复测试](../../tests/test_coding_resume.py)、
[CLI 测试](../../tests/test_coding_cli.py)。本次文档同步不新增真实模型或 Abaqus 收益结论。

## 本机私有领域知识包

产品使用 `platformdirs` 的 `user_data_path/knowledge/packs/` 作为当前用户的知识包根。只检查其直接非隐藏
子目录；每个目录必须包含 `domain-knowledge-pack-v1` 的 strict `pack.json`，目录名与 `pack_id` 一致，
入口必须属于显式资源清单。Markdown、纯文本、Python、INP、JSON 和 YAML 只作为 UTF-8 参考文本；链接、
junction、路径越界、未列出文件、重复 ID、单文件/整包超限和不支持的后缀在模型调用前拒绝。

发现不等于启用。CLI 用可重复的 `--knowledge-pack <pack_id>`，Web 在创建新会话时选择包；空列表保持原产品
行为。宿主按 `pack_id` 找到源目录，读取实际 bytes，生成逐文件 hash、pack digest 和按 ID 排序的 bundle
digest，并把 `pack.json` 与所列资源复制到 state 的 `domain-knowledge/`。launch v11 保存完整 binding；普通
恢复、Web continuation 和 task revision 只校验原 state 快照，不回读已变化或已删除的用户级源目录，也不允许
通过 amendment 换包。

每个工作段把通过校验的资源物化到临时 bundle 根，按 `pack_id` 分目录，并生成宿主目录 `_catalog.md`。
Coder 仍只得到 `knowledge_list_directory` 和 `knowledge_read_file`，按需读取所选包；模型看不到未选择的已安装包，
也不能把包内 `SKILL.md` 注册为 instructions。读取记录沿用 Harness history，不新增逐条 receipt 状态机。
采用后的代码必须写入当前 workspace，执行与验收仍由原三个 runtime tools 和宿主 verifier 所有。

manifest 保存来源、作者 release、兼容声明和分项验证状态；这些声明与内容一起进入 digest。只读与 hash 证明
内容身份，不证明来源结论正确、Abaqus 2024 兼容、模型已经采用或任务通过。当前验证只有 schema/path/
snapshot/resume、CLI/Web 接线和确定性模型读取等离线回归，没有真实 Provider、持证 Abaqus 或通用任务收益
结论。源码与验证入口：[知识包](../../src/abaqus_sim_agent/coding/domain_knowledge.py)、
[launch](../../src/abaqus_sim_agent/coding/launch.py)、[核心测试](../../tests/test_domain_knowledge.py)、
[Web 测试](../../tests/test_web_domain_knowledge.py)。

## 文档和资料的使用边界

- 普通 Concept 工具接收 question/observed context，宿主做已有符号和产品 metadata 路由，沿用
  exact-symbol-augmented Dense。当前 schema v2 使用 SQLite 文档/metadata 与 NumPy exact Flat，固定
  MiniLM encoder 文件和索引矩阵均由manifest绑定；不使用ANN。固定 BM25/Dense/RRF 对照仍是独立
  检索能力，不自动替换产品策略。
- `bounded_v1` 通过运行时证据服务执行查询，重新核对来源/hash/范围/公式关系；receipt 与 assessment
  进入工作区外 ledger。工程已接线，模型行为资格未通过，继续默认 off。
- Foundation 五项 Skill 覆盖 formulation、modeling、discretization、postprocess 和 verification，
  是同一 Coder 可选读取的领域资源，不是五个 Agent。
- `structural_mechanics_v1` 当前暴露七个 **Capability Pack** 的 Skill 入口；Pack 是知识范围/维护边界，Skill 是
  可独立触发的任务入口，两者不再按一一对应理解。当前拆分为：hyperelasticity（`hyperelastic-fea`、
  `hyperelastic-calibration`）、metal-plasticity（`plasticity-fea`）、geometric-nonlinearity
  （`geometric-nonlinear-fea`）、structural-dynamics（`structural-dynamics-fea`、`modal-dynamics-fea`）、
  explicit-dynamics（`explicit-dynamics-fea`、`quasi-static-explicit-fea`）、contact（`contact-fea`、
  `contact-diagnostics-fea`）、interface-cohesive（`interface-cohesive-fea`）。只有存在独立任务入口时才拆 Skill；
  不因为 Pack 内有多个知识章节就机械拆分。
- 显式评测参考目录继续只暴露 `knowledge_list_directory`、`knowledge_read_file`；默认只读 Markdown/Python，
  分页有界，根目录不能与 workspace/state 重叠。调用者自行冻结该旧入口；它不替代上面的产品知识包绑定。
- 官方示例原稿与任务适配副本分开。保留 GUI、旧 API 或 submit/wait 原文便于理解，不等于允许原样执行。
  共享官方结构案例池把精选脚本分成 `basic-representative`、`topic-typical`、`complex-comprehensive` 三层；
  一个脚本只有一个 canonical 副本，以 `primary_topic` / `cross_topics` 交叉引用。Getting Started 页按内嵌
  `h2 + pre.codeblock` 提取，EXA/BMK/VER 页按其显式 `SIMAINPRefResources/*.py` 链接读取原资源。
  `inventory.json` 只索引 EXA/BMK/VER 的全部 Python 链接与来源/hash/语法状态，不把全部 vendor 脚本复制进
  默认上下文；精选 `scripts/` 与全量 inventory 分开。importer 不执行 Abaqus，也不把 2026 FD02 文档视为
  2024 兼容证据。
  官方文本使用独立的精选页面来源清单记录 release、页面、标题、章节标题和 hash，不复制手册整页正文；运行时
  Skill 只按需读取从这些页面整理的原创 `references/abaqus-official-notes.md`。教材、开放课程、NAFEMS 和其他
  求解器 verification case 只按各自证据角色进入来源图谱，不覆盖 Abaqus 官方实现语义。
  [官方梁示例记录](../../benchmarks/coding/knowledge_reference_resources_20260915.md)只验证一份适配脚本，
  没有证明 Agent 能正确采用所有示例。

知识来源区分 Abaqus 2026 FD02 文档、abqpy 2024.10 静态声明和 2024 licensed runtime note。
静态声明不是运行证据，found 不是物理验收；索引、Card、Skill 或参数变化后必须重新绑定新实验，旧报告保留。

Runtime note 的 `verified` 只覆盖说明中明确验证的行为。调用未抛异常不能替代状态赋值生效、INP 写出或
求解成功；例如 `Part.assignBeamSectionOrientation.region` 的最小探针仅验证参数接受情况，没有检查
保存的梁方向或写出 INP。当前卡片说明保留这一限制，不沿用历史观测中的通用用法推荐。

[当前 runtime notes](../../benchmarks/api_catalog/abaqus_2024_runtime_notes.jsonl) 是新 Catalog 的构建输入；
[final15 原始快照](../../benchmarks/api_catalog/abaqus_2024_runtime_notes_final15_20260828.jsonl) 保留旧报告绑定的
原始 bytes。修订当前说明后须构建并发布新的 Catalog 资源；普通新会话使用所安装版本，评测仍显式选择 artifact。
已有 run 的绑定不会自动改写，也不承诺替其保存已卸载程序中的旧 Catalog。

## 相关决定与证据

- [ADR-0009](../adr/0009-exact-abaqus-api-catalog.md)：Catalog 来源和证据合同。
- [ADR-0040](../adr/0040-require-api-catalog-for-product-runs.md)：产品必选 Catalog、默认 automatic verified-only 与历史 `off` 边界。
- [ADR-0010](../adr/0010-budgeted-official-concept-retrieval.md)：可选 Concept 接入；普通产品次数上限已由 ADR-0031 替代。
- [ADR-0022](../adr/0022-evaluate-bm25-dense-rrf-before-agent-adoption.md)：先独立对照再决定采用。
- [ADR-0028](../adr/0028-bind-user-domain-knowledge-as-read-only-snapshots.md)：私有包的只读内容和 run 快照边界。
- [ADR-0029](../adr/0029-discover-private-knowledge-from-user-data.md)：用户级存储、受限发现和显式启用。
- [验证证据入口](verification-evidence.md)：不同实验分别支持哪些声明。
- [SQLite/NumPy迁移回放](../../benchmarks/rag/sqlite_numpy_flat_storage_v1_20260916.md)：存储、依赖与排名边界。
