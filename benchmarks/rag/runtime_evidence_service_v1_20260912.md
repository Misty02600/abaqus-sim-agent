# RAG 运行时证据服务：工程验收

2026-09-12。`doc-evidence-result-v1` 的完整回放16/16通过，未接入Coder、未调用生成模型或Abaqus。
结果和范围见[机器可读摘要](runtime_evidence_service_v1_20260912.json)。

## 本次实现的边界

独立strict合同返回 `found / not_found / unavailable / rejected`，最多4个完整证据单元，带正文hash、源hash、
真实XPath、文档版本、适用性来源及公式关联。`found`只是候选交付，不表示相关、充分或物理验收。
共享类型不依赖evaluation，应用只消费 `DocumentSearchPort`；具体适配器留在evaluation，薄封装原冻结组合，
不复制召回、重排或正文生成算法。

一次调用最多一次原组合搜索，硬上限60秒且受宿主剩余wall-clock约束；不自动重试、下载模型、改变来源、
降低检索模式或补查。query只作为JSON数据，解释器、模块、路径和环境由宿主确定，Provider凭据不传入。
caller取消与worker超时会清理专用Job；父进程硬退出也会触发Job清理，不按python/Abaqus进程名查杀。

Windows使用创建时的 `PROC_THREAD_ATTRIBUTE_JOB_LIST` 和不可继承的kill-on-close句柄；不先运行再附着。
同时等待成员进程对象退出，因为测试发现Job计数归零可能早于退出对象变为signaled。
API依据：[创建时属性](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)、
[Job生命周期](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。这不是OS沙箱。

原型直接打开Chroma后，候选库 `chroma.sqlite3` 的bytes发生漂移，完整性检查拒绝了结果；不把失败原型写成
原库未变。修正后复制已绑定文件到state内普通文件副本，仅打开副本；交付前重新核对原库全部依赖。副本不是
hardlink，不创建新collection、不重算向量，正常/超时/取消后清理；请求、receipt与原始日志保留。
硬杀宿主可能留下磁盘副本，不能等同于仍有活跃worker。非空SQLite WAL拒绝复制，不支持并发更新源库。

父进程独立解析receipt、核对原请求和实际绑定，重读完整源单元/符号卡及源文件，检查正文、版本、范围、
公式关联和序列化预算。坏JSON、漂移、加载失败或超时一律不给本次部分证据。
旧符号卡只有章节锚点；新XPath来自官方页唯一锚点，不编造旧metadata，`indexed_symbol_card`仍区分于原始段落。

## 16题工程回放

使用原[16题输入](rag_evidence_baseline_v1.json)，与原基线 `run-1` 的实际输出比较；不是新held-out或调参。
完整原始receipt/日志位于 `var/audits/runtime-evidence-v1/replay-2`，摘要SHA-256为
`5e2b10d01e155e9d0aadb8b572cd53b6be06a0374f5828a0f64d2574c47f0f61`。

| 检查 | 结果 |
|---|---|
| 正文、来源、交付顺序和公式关联 | 16/16等价 |
| 原始候选重排顺序及score | 16/16等价 |
| 原预算结果及新状态映射 | 16/16等价 |
| 新序列化证据预算（含新增hash/XPath） | 最大2846/4000 proxy tokens，无新增淘汰 |
| 状态分布 | 12 found、3 not_found、1 rejected、0 unavailable |
| 单次端到端时限 | 16/16低于60秒，无临时上调 |
| 实际绑定 | 16次依赖snapshot digest相同；原库前后bytes检查通过 |
| 临时副本/审计记录 | 完整批次无副本残留；保留129个文件、4,734,449 bytes |

两个显式2024文档请求返回 `requested_release_not_indexed`；未知符号返回 `no_candidates`；超长输入返回
`rejected/query_too_long`。不把“我的模型最大应力”查询的通用文档候选解释为已有模型结果。
正文预算数值没有假装与旧信封完全相同：旧payload token数保持等价，新hash/XPath/空关联字段也计入
同一4000-token上限；两套计数分别保存。

总耗时653.98秒；单题35.56–45.61秒，平均40.87秒。worker加载平均23.80秒（含私有复制和校验）、
搜索平均3.78秒、worker验后检查平均8.29秒；宿主准备、验证、进程启动与清理另由host日志/总时间覆盖。
每次复制约0.84 GB，成本明显；这是可取消的短生命周期接口，不是低延迟常驻服务或已验证SLO。

原质量结论不变：旧10项必要条件交付6/10，完整题4/7；不采用BGE的决定不变。
旧基线与BGE seal中的28项文件hash全部一致，没有修改旧hash绕过约束。

## 验证与未做的事

- 43项专项测试通过，覆盖四类返回、预算、来源与关系篡改、无法加载、坏收据、无重试、真实进程树清理、
  父进程硬退出、取消、旁路进程存活、不继承CodingRun锁、环境最小化和私有副本。
- 最终常规回归1024通过、27项持证跳过（79.63秒）；全仓BasedPyright零错误/警告。
- 早期直接打开源库的原型失败、单题成功原型和一次主动停止的整批启动均保留，不计为完整16题回放。
- 两个旧原型的约1.67 GB临时副本仍保留：手动清理被执行策略阻止；完整验收批次的副本已正常自动清理。
- 全仓Ruff规则检查通过；本次9个Python文件格式检查通过。全仓格式检查仍有4个既有问题：
  `benchmarks/api_catalog/odb_member_adoption_v1_20260910.md`、`coding/edit_scope.py`、
  `retrieval/reproduction.py`及其测试；本次未改动这些既有/冻结文件。
- PLAN-0003的要点登记、充分性回评、补查、应用预算和resume接线未实施。API Catalog、默认文档工具、
  Coder Prompt/Skill及Abaqus执行权限均不变。

独立使用方式见[README](../../README.md#独立运行时证据端口不接入-coder)。
