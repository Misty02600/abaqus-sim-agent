# PLAN-0002：RAG 侧提供有界、可追溯的运行时证据服务

| 状态 | 完成时间 |
|---|---|
| 已完成 | 2026-09-12 |

## 问题与最终结果

原有组合已有完整证据交付能力，但产品旧工具的线程等待不能保证推理超时后真正退出，错误也未细分。
本计划新增独立 `doc-evidence-result-v1` 端口及Windows监督worker，负责一次查询的可信交付和技术失败，
不判断相关性或充分性、不改写问题、不自行补查。用户于2026-09-12授权实施本计划，未授权实施PLAN-0003。

具体适配器留在evaluation并薄封装冻结的 `rag-evidence-baseline-v1`；共享类型不反向依赖evaluation。
默认Coder、旧文档工具、Catalog、Skill及Abaqus权限不变，没有生成式模型或Abaqus调用。

## 关键改动

| 组件 | 最终行为 |
|---|---|
| [独立合同](../../../src/abaqus_sim_agent/retrieval/evidence_contracts.py) | strict/discriminated的found、not_found、unavailable、rejected；最多4个完整单元及来源、正文hash、XPath、版本、适用性、公式关联与成本 |
| [消费端口](../../../src/abaqus_sim_agent/coding/document_search_port.py) | 只定义宿主分配search_id与monotonic deadline的异步Protocol；不注册工具 |
| [显式适配器](../../../src/abaqus_sim_agent/evaluation/runtime_evidence_service.py) | 固定模块/解释器、最小环境、独立state、最多一次搜索、60秒及调用方剩余时限；无重试或降级 |
| [进程监督](../../../src/abaqus_sim_agent/evaluation/evidence_process.py) | 创建时进入不可继承句柄的kill-on-close Job，准确终止并等待成员退出；覆盖取消和父进程硬退出，不是OS沙箱 |
| [worker](../../../src/abaqus_sim_agent/evaluation/evidence_worker.py) | 只打开已绑定原库的私有文件副本，不用hardlink；不重建索引；查询后再次核对原始依赖 |
| [宿主校验](../../../src/abaqus_sim_agent/evaluation/evidence_receipt.py) | 独立核对receipt、原请求、依赖bytes、来源正文、范围、版本、公式关联、排序与真实预算；失败无部分证据 |
| 回放入口（已完成，结果见[验收报告](../../../benchmarks/rag/runtime_evidence_service_v1_20260912.md)） | 使用原16题/旧实际结果做工程等价回放（16/16），不另设质量门槛；独立回放模块已随计划完成删除，结论由报告封存 |

`source_release` 是显式文档版本硬限制，不由runtime 2024推导；不具备所要求版本时不回退2026。
allowed_sources只收窄固定Top4的交付，不额外召回或补位。observed_context始终标为Agent提供、未独立验证。
调用次数、去重、run/revision、补查与receipt复用由后续应用适配拥有，本端口不伪装成已完成这些功能。

## 验证结果

| 成功标准 | 证据或结果 |
|---|---|
| 原16题等价 | 16/16正文、来源、顺序、公式关联、原始排名/score、旧预算及状态映射一致 |
| 完整交付预算 | 64候选、4单元、1200正文/4000序列化proxy tokens；新增hash/XPath亦计入，实际最大2846，无新增淘汰 |
| 状态区分 | 12 found、3 not_found、1 rejected；缺2024版本不回退，未知符号与超长输入分别标记 |
| 进程与信任边界 | 43项专项测试通过；超时/取消/父进程硬退出清理准确子树，旁路控制进程存活；不继承CodingRun锁 |
| 故障无部分交付 | 源/索引/模型/关系漂移、坏JSON/绑定、加载失败、来源篡改和预算边界有确定性覆盖 |
| 原库只读 | 完整16题共享同一依赖snapshot digest，原库前后bytes一致；验收批次无私有索引副本残留 |
| 常规回归 | 1024通过、27持证跳过；全仓BasedPyright与Ruff规则检查通过；本次9个Python文件格式检查通过 |
| 保留旧实验 | 旧基线和BGE seal中的28项文件hash全部一致；未改冻结算法、内容绑定或依赖 |

完整回放耗时653.98秒，单次35.56–45.61秒（平均40.87秒），无超时、无事后提高60秒上限。
这是冷启动/复制/校验成本的实际观察，不是SLO或吞吐承诺；正文仍存在旧基线的质量缺口。
完整事实、原型记录、成本分解与摘要hash见[验收报告](../../../benchmarks/rag/runtime_evidence_service_v1_20260912.md)。

## 已知缺口与后续事项

- 后续[PLAN-0003](0003-bounded-evidence-use-in-coder.md)已完成接线与行为评测；profile行为gate未通过，保持默认off。
  found不等于充分、引文相关性/支持性不由机械校验保证；不能将回放等价描述成准确率或Agent收益。
- 本机Chroma直接打开原库会产生SQLite bytes漂移，首个原型因此被拒绝；已改为每次约0.84 GB的私有复制。
  当前源库须静止，非空WAL拒绝；没有常驻池。宿主硬退出可能留下磁盘副本，但不会留下该Job中的活跃worker。
- 原型1失败、原型2单题成功及一次因符号locator缺口主动停止的批次均保留，不抹除或充当完整验收。
  两个旧原型的约1.67 GB副本因手动清理被执行策略阻止仍保留；完整验收批次副本均已自动清理。
- 全仓格式检查仍报4个既有/冻结文件不符合当前formatter；本次未修改无关文件或破坏旧seal。
- 本地来源仍为Abaqus 2026 FD02，不是2024 API兼容或物理验证证据。

边界继续遵循[ADR-0010](../../adr/0010-budgeted-official-concept-retrieval.md)与
[ADR-0022](../../adr/0022-evaluate-bm25-dense-rrf-before-agent-adoption.md)，未改写已采纳决定。
