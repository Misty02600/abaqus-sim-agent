# 知识接入与证据边界

## 当前入口

| 入口 | 当前接线 | 返回什么 / 由谁使用 |
|---|---|---|
| `lookup_abaqus_api` | CLI 显式 `full` / `full_with_discovery` | qualified method、parameter、member 或 `Method.__doc__` 的精确证据 |
| `discover_abaqus_api` | CLI 显式 `full_with_discovery` | 已知 receiver 下的方法名称和 Card ID；供 Coder 选择后精确查证 |
| automatic Catalog | `automatic_verified_only` / `automatic_exact_signature` | 在可确定的错误上下文中附 verified note 或紧凑签名，不暴露主动 discovery |
| `search_abaqus_docs` | 显式 Concept `tool`；默认 off | 本地官方文档候选；普通模式按需查询，允许零查询 |
| Solver diagnostic advisory | 独立、默认 off 的模式 | 非超时结构化 Solver warning/error 后附有界文档候选，不覆盖原运行结果 |
| `bounded_v1` document policy | 显式 policy spec + Concept tool，已接入同一 Coder；默认 off | need、receipt、assessment 与逐链预算；运行和 ready 前有宿主证据门禁 |
| 基础力学 Skill 与只读参考资料 | 显式 Agent 工厂 / 评测入口 | `foundation_skill_profile="linear_structural_fea_v1"` 与独立 `knowledge_resources_dir`；普通 CLI/launch 尚无资料选择和快照绑定 |

当前 Catalog 是精确卡片与对象方法目录，独立检索实验另有 BM25/E5 意图搜索。两者不能仅因都叫
discovery 就视为同一产品接口。早期 [API 方案实验](../../benchmarks/api_catalog/api_discovery_loop_v1_20260915.md)
没有经过 CodingRunService；后续提交 `74fa6e3` 才加入下面的产品目录工具和恢复接线。

## 产品 API discovery

已有显式 Catalog 目录时，CLI 参数 `--api-catalog-mode full_with_discovery` 同时注册 discovery 和 exact
lookup。不提供 Catalog 时不能启用该模式；默认 run 不自动加载候选索引。该模式可以和新普通 CLI 的
`ask_user_v1` 一起使用，旧 run 仍按自己的 launch 恢复。

调用顺序：

1. Coder 确定对象类型，如 `Part`，调用 `discover_abaqus_api(receiver, query, limit)`。
2. 宿主在该 receiver 的方法目录中列候选；query 可选，limit 默认 20、范围 1–100。
3. Coder 用候选名称调用 `lookup_abaqus_api`，读取方法、参数或保留的 docstring 后再使用其证据。

工具会提示“候选不是 API 证据”，但没有通用机制证明模型实际遵守了这一推理顺序。确定性保证是：
discovery 的候选 ID 不进入已读卡片集合，也不会阻止随后 exact lookup。查到名字不证明签名、适用性或运行成功。

discovery 与 lookup 共用 `max_api_lookup_calls`。有效、非重复且 Catalog 可用的目录查询先预留一次额度，
即使无候选也计数；无效、重复、未配置或预算不足不消耗新额度。去重按规范化 receiver/query/limit，
不把候选误记成已交付的完整卡片或参数。

`api_discoveries.jsonl` 和 `api_lookups.jsonl` 保存在 workspace 外。恢复合并 journal 与 snapshot，继承
计数、查询去重、实际 delivered card/parameter 状态；旧 trace 没有 delivered 字段时不凭 card ID 猜造已读证据。
这些记录保证恢复后的工具状态，不保证历史文档知识始终正确。

源码与验证入口：[API 工具](../../src/abaqus_sim_agent/coding/api_lookup.py)、
[装配](../../src/abaqus_sim_agent/coding/agent.py)、[恢复](../../src/abaqus_sim_agent/coding/resume.py)、
[Agent 合同测试](../../tests/test_coding_agent.py)、[journal 恢复测试](../../tests/test_coding_resume.py)、
[CLI 测试](../../tests/test_coding_cli.py)。本次文档同步不新增真实模型或 Abaqus 收益结论。

## 文档和资料的使用边界

- 普通 Concept 工具接收 question/observed context，宿主做已有符号和产品 metadata 路由，沿用
  exact-symbol-augmented Dense。当前 schema v2 使用 SQLite 文档/metadata 与 NumPy exact Flat，固定
  MiniLM encoder 文件和索引矩阵均由manifest绑定；不使用ANN。固定 BM25/Dense/RRF 对照仍是独立
  检索能力，不自动替换产品策略。
- `bounded_v1` 通过运行时证据服务执行查询，重新核对来源/hash/范围/公式关系；receipt 与 assessment
  进入工作区外 ledger。工程已接线，模型行为资格未通过，继续默认 off。
- Foundation 五项 Skill 覆盖 formulation、modeling、discretization、postprocess 和 verification，
  是同一 Coder 可选读取的领域资源，不是五个 Agent。
- 显式参考目录只暴露 `knowledge_list_directory`、`knowledge_read_file`；只读 Markdown/Python，
  分页有界，根目录不能与 workspace/state 重叠。调用者负责冻结资料；当前没有普通 CLI 的内容绑定恢复保证。
- 官方示例原稿与任务适配副本分开。保留 GUI、旧 API 或 submit/wait 原文便于理解，不等于允许原样执行。
  [官方梁示例记录](../../benchmarks/coding/knowledge_reference_resources_20260915.md)只验证一份适配脚本，
  没有证明 Agent 能正确采用所有示例。

知识来源区分 Abaqus 2026 FD02 文档、abqpy 2024.10 静态声明和 2024 licensed runtime note。
静态声明不是运行证据，found 不是物理验收；索引、Card、Skill 或参数变化后必须重新绑定新实验，旧报告保留。

## 相关决定与证据

- [ADR-0009](../adr/0009-exact-abaqus-api-catalog.md)：Catalog 来源和证据合同。
- [ADR-0010](../adr/0010-budgeted-official-concept-retrieval.md)：可选、有预算的 Concept 接入。
- [ADR-0022](../adr/0022-evaluate-bm25-dense-rrf-before-agent-adoption.md)：先独立对照再决定采用。
- [验证证据入口](verification-evidence.md)：不同实验分别支持哪些声明。
- [SQLite/NumPy迁移回放](../../benchmarks/rag/sqlite_numpy_flat_storage_v1_20260916.md)：存储、依赖与排名边界。
