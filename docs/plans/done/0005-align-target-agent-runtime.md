# PLAN-0005：对齐目标运行配置与任务交互合同

| 状态 | 最后更新 |
|---|---|
| 已完成：运行配置与交互合同离线实现；未启动外部调用 | 2026-09-12 |

## 背景

用户要求依据Coding Agent/科学计算Agent评测调研制定方案并自审，已明确“模型推理要打开，而且设置为高”。本计划解决产品与评测装配不一致，使同一目标配置能由两者实际执行；验收完全离线完成。真实任务基线由[PLAN-0006](../todo/0006-domain-task-evaluation-baseline.md)单独拥有，不把实施本计划等同于付费调用或持证运行授权。

保留单一abaqus_2024_coder、Pydantic AI/Harness原生循环、三个typed运行工具、工作区/state隔离和宿主独立验收。继续遵守[ADR-0019](../../adr/0019-separate-product-execution-evidence-from-evaluation-oracles.md)、[ADR-0020](../../adr/0020-resume-coding-runs-with-harness-step-persistence.md)、[ADR-0021](../../adr/0021-use-amendments-and-task-revisions-for-continuation.md)。不新增编排框架、配置库、数据库或通用配置平台。

## 实施前设计与问题

- `coding/model.py`默认HTTP超时60秒、SDK额外重试10次；`coding/cli.py`常规路径沿用`create_abaqus_coding_agent`的Planning=True，未额外指定thinking。
- `evaluation/document_policy_behavior.py`固定零重试、Planning=False、12请求/360秒；`document_behavior_profile.py`显式发送thinking disabled。之前的HTTP对齐测试比较legacy与calibrated两个评测profile，未比较产品装配。
- [阶段B](../../../benchmarks/coding/document_behavior_v2_phase_b_20260912.json)为14通过、2确定失败、8Provider超时未知；最大9次请求、174.26秒，未耗尽12/360。79/94个响应含reasoning_content，不能从请求参数推断实际关闭推理。
- `coding/instructions.py`要求澄清前不检查工作区，新语义评分却允许独立准备；旧plate Gold仍检查pre_clarification_tool_names。这不是仅改评分就能消除的产品指令冲突。
- `document_policy_state.py`将无效chain、尚未回评和已有补查合成同一句拒绝。已有轨迹将其误读为额度耗尽；动态状态同时给出了计数，因此模型错误与宿主反馈问题都应保留。
- `_LaunchManifest`现为v1/v2，只保存provider/model/http_retries等字段，未保存推理设置和HTTP超时。`CodingRunConfigPatch`也不包含推理设置，不能只改工厂默认值后宣称旧resume语义不变。

## 目标配置与所有权

本轮针对已选定的Go / deepseek-v4-flash使用配置，不静默切换官方网关、升级模型或让所有可选能力全开。

| 项目 | 目标 | 决定状态 |
|---|---|---|
| 推理 | enabled，effort=high | 已实施；真实服务有效性仍待校准 |
| HTTP额外重试 | 10，与目标产品策略一致 | 既定恢复方向；不是行业统一最佳值 |
| Planning | 暴露write_plan，由Agent决定是否使用 | 建议与常规产品一致，不强制调用 |
| 输出上限 | 不额外设置低单响应上限；保留provider/native限制 | 保留已采用边界 |
| HTTP超时 | 60秒仅作现有起点；可显式配置并持久化 | 服务端有效性/延迟校准由PLAN-0006处理 |
| 任务请求/wall/runtime预算 | 由调用方明确profile提供 | 不把30/2700自动复制到所有评测 |
| 自动provider同run续跑 | 不新增 | SDK重试与显式resume分开 |
| 外部Job重试 | 不新增 | 未决effect仍needs_reconcile，不能因网络错误重提Solver |

产品与评测共用一个小型、strict、无凭据的有效运行配置以及同一映射函数，包含provider/model、推理策略、HTTP retry/timeout、Planning；预算继续归CodingRunRequest拥有，避免同一字段两处写入。typed配置沿用Pydantic，不让Agent填写任意model_settings字典。

目标参数映射：

```python
{
    "extra_body": {"thinking": {"type": "enabled"}},
    "openai_reasoning_effort": "high",
}
```

经现有Go适配器的MockTransport已确认线上字段为thinking.type=enabled与reasoning_effort=high；这只是发送验证，不是服务端有效强度证明。[DeepSeek官方字段说明](https://api-docs.deepseek.com/guides/thinking_mode/)。Go已有历史thinking不回放的兼容适配保持，不凭官方直连文档直接改掉它。

### 持久化与兼容

1. 新run按“产品目标默认→显式CLI/profile覆盖→完整strict验证”解析一次；凭据只来自既有凭据入口，不进入配置、seal或日志。暂不新增环境变量配置层。
2. effective配置写入新的launch格式（预计v3，实施时核对未占用版本）；实际发送前与该快照核对。禁止下游agent.model_settings再次覆盖成disabled。
3. resume读取原launch快照并按现有amendment序列应用已有允许字段；不重新套用新建run默认。保留同state锁及原子写入，不另建ledger。
4. 旧v1/v2解码为兼容视图：普通旧CLI缺省推理仍按provider-default发送，不伪称已知是high或disabled；旧字段保留，原文件不原地改写。历史evaluator以自己的seal为准，不能从旧CLI默认猜其参数。
5. 首版新推理策略在run内固定；本计划不顺便扩张amendment为任意model_settings修改接口。更换provider/model时校验与固定策略兼容，不支持则明确拒绝，不偷偷降级。需要改变推理策略时建立新run；以后确需同run变更再单独设计。
6. 缺失字段、显式null和不支持的组合分别处理；坏文件保留并报错。旧读者无需承诺读取新格式，回退需要使用匹配版本和原快照。

## 任务交互边界（D-001确认后实施）

建议改为：先允许读取用户提供的工作区事实，确认必需物理输入仍缺失或冲突后澄清；允许不依赖缺参、未被用户禁止的准备工作。仍禁止默认取值、依据检索补造用户物理参数，以及使用缺参假设推进建模/执行。用户明确“先问、不做其他操作”时遵守更严格指令。

同步产品提示、Planning引导与新语义评分；旧Gold不改，新增版本区分语义。first-tool严格规则和恢复前的格式错误作为诊断项，终态协议遵循与修复成功另报，不用一次可恢复schema错误否定正确最终任务。

拒绝信息分为尚需回评、已补查一次、无效chain引用；提供真实剩余额度与可行下一步，不暴露隐藏答案。权限判断、预留预算与副作用边界保持原样。

## 实施与验收

| 顺序 | 范围 | 主要落点 | 验收 |
|---|---|---|---|
| 1 | 共用有效配置与映射 | coding/model.py、agent.py、cli.py；evaluation/document_behavior_profile.py/spec.py及后续新profile | 实际产品与评测HTTP字段、工具清单除预声明差异外一致；不是再比较两个评测变体 |
| 2 | launch与resume兼容 | coding/cli.py、resume.py、现有amendments/persistence路径 | 新run快照稳定；旧run不被升级；字段覆盖/类型错误和并发行为有测试 |
| 3 | 澄清与拒绝语义 | coding/instructions.py、document_policy_state.py/tools.py；保持旧Gold版本 | 输入文件中已有值时能先读；确缺值时询问；三类拒绝原因准确且不消耗错误的预算 |
| 4 | 离线装配回归 | tests/test_coding_model.py、test_coding_cli.py、test_coding_resume.py、test_coding_agent.py、test_document_policy_integration.py、test_document_behavior_profile.py | Mock完整CLI/目标评测装配，含503后成功、多轮工具、typed输出和same-run重启，0真实调用 |

必须覆盖：enabled/high不被覆盖；旧launch缺省参数不变；明示null/错字段拒绝；SDK一次重试产生2次HTTP但1次逻辑请求；总wall终止能够中断后续重试；缺参只读与缺参执行分离；false first-tool失败不进入新任务验收；已用补查仍拒绝；旧Gold与既有verifier回归。

本计划完成条件是配置/交互合同一致并通过离线验证，不要求先跑出某个模型成功率。旧JSON报告、baseline/source/index hash和旧seal保留；源码改变只使旧seal不再适用于新运行，不重写历史。`uv.lock`被当前candidate绑定，不顺手清理依赖。

## 已确认事项

### D-001 · P0：是否采用允许读取既有事实的澄清语义

用户“实施”批准推荐边界：允许读取既有事实，确认缺参后澄清，继续禁止补造和依赖缺参的执行。旧Gold和旧run保持legacy指令。

### D-002 · P1：新推理策略的首次落地范围

按批准方案仅对新建目标Go/V4-Flash采用enabled/high；旧launch兼容读取，推理策略run内固定，不新增动态推理amendment。

## 实施结果与验证

- `ModelExecutionConfig`统一新run推理、HTTP retry/timeout、Planning与交互策略；`ConfiguredModel`拒绝下游disabled、low、超时冲突和额外response cap覆盖。新目标Go/V4-Flash为enabled/high，其他网关provider-default，未切换默认网关。
- `coding/launch.py::LaunchManifest`接管CLI启动快照合同：v3要求有效配置完整；v1/v2缺省、显式null和错误字段分别处理。旧launch不原地升级；model/provider/http_retries仍按既有amendment叠加，不兼容固定high策略的修改在同一state锁内且写ledger/归档outcome前失败。
- 新run允许读取workspace facts，旧run/旧Gold保持legacy；physical-input禁止补造和依赖缺参执行未放宽。文档拒绝细分invalid_chain、assessment_required、follow_up_exhausted、chain_not_follow_up和真实预算耗尽，返回next_action及真实剩余额度。预算优先于chain条件时仍先报预算，不改变许可或去重语义。
- 实际MockTransport经完整CLI执行write_file与typed ready，并与新候选`compose_baseline_agent`比较完整工具schema、enabled/high、无额外cap及SDK配置。该测试抓出并修复候选入口遗漏包内Skill的问题。注入503后成功证明2次HTTP仍为1次逻辑请求；总wall超时测试证明SDK退避不会绕过任务deadline；新Go与旧provider-default均覆盖same-run恢复和累计用量。
- 新增`tests/test_execution_config.py`、`tests/test_task_baseline.py`，扩展CLI、文档状态和集成测试。修改的10个产品/候选Python模块BasedPyright为0 error/0 warning；Ruff通过。`uv run --no-sync pytest -q -m 'not abaqus'`为959通过、27持证测试未运行；不把Mock解释为licensed通过。
- 旧阶段B报告SHA-256仍为`f9ba2df08d9e1cf42652e5d3325a3ff7935613fd376da4b0def2900b3d62db2f`，旧spec仍为`1a91e87852414ed54b699cf9b9bb53020643fa9336cff24eb828f937956bf322`。本轮未修改依赖锁、检索绑定或旧评测报告；外部校准/持证运行未启动。
- 新任务基线仅完成候选准备，完整plate INP/几何/边界验收扩展与主runner仍由PLAN-0006拥有。PLAN-0005完成不等于PLAN-0006离线就绪或整个评测方案完成。

## 自审

| 风险 | 本方案的修正 / 残余限制 |
|---|---|
| 只在评测中打开high，产品仍未对齐 | 同一配置来源，并比较实际两条装配路径 |
| 改默认导致旧resume换模型行为 | 新格式保存effective快照，旧格式兼容视图不套新默认 |
| “对齐产品”被误解为开所有工具 | 保留禁Shell，optional功能由明确profile选择 |
| 读取前放宽成缺参也可建模 | 分开读取已知事实与依赖缺参的推进，保留硬约束并加反例 |
| 拒绝信息改善被说成模型收益 | 先证明确定性原因准确，真实收益须PLAN-0006的新对照 |
| 一次Mock就证明Go真的执行high | 只证明发出正确参数；服务有效性留给独立校准 |
| 配置改造无限扩大 | 复用Pydantic、launch、锁和原子写；不新增通用配置平台或任意覆盖字典 |

这是Codex自审，不是独立专家审查。若兼容fixture或实际装配揭示未覆盖的历史行为，先收窄承诺，不用猜测迁移旧数据。
