# Foundation 离线 follow-up v2：12 条运行结果

2026-09-15，已按用户确认执行三类题、两臂、各两次，共 12 个独立 trial。11 条正常交付或停在澄清边界，1 条 wall timeout，全部 0 Abaqus/Solver/文档/Catalog 查询。结果是 **2 win / 2 tie / 1 loss / 1 unpaired_unknown**。保留 Foundation 为显式候选，不据本批默认启用或扩跑。

## 冻结配置与试验性质

- 单一 `abaqus_2024_coder`，Control 仅暴露原 Abaqus Skill；Foundation 额外暴露五个 Foundation deferred Skills。六份 SKILL.md 与 v1 pilot hash 一致。
- Go/deepseek-v4-flash；reasoning 明确为 provider_default，非 enabled_high；HTTP timeout 60 秒，SDK 额外重试 10。Planning/Catalog/docs/advisory 关闭。
- 每条模型请求上限 10，wall 360 秒，Abaqus/Solver 预算 0；旧 `legacy_decision` 澄清协议，两臂一致。不是新 CLI ask_user 交互验证。
- 同 case/repetition 相邻运行，第二轮反转 profile 顺序。12 条执行一次，没有额外 trial、续跑替换或补跑到通过。
- 前两题沿用旧 goal/seed/rubric；孔板更换为已讨论的方法与证据边界题，不要求闭卷给出 312.5 Pa。v1 与 v2 不合并为同一效果统计。
- 这是 development/offline engineering inspection，不是 held-out、实际 CAE 闭环或物理验收。

## 实现、预检和封存

新增 `foundation_followup_v2` worker 和独立 prepare 脚本；复用工厂、宿主服务、配对安排与观察格式。prepare 不调用模型；worker 用 `-I` 并显式优先导入复制的 source snapshot，避免 live checkout 后续变更影响批次。

正式目录为 `output/foundation-followup-v2-20260915-b`。178 个快照文件包含包内源码/数据/Skill 及引用、输入/rubric、pyproject/uv.lock 和 worker。另绑定 2,193 个关键运行文件，覆盖 Python 可执行文件及 Pydantic AI/Harness、Pydantic/core、OpenAI、httpx/httpcore、anyio、sniffio 和 typing-extensions 的 Python/本地扩展文件；其他依赖记录安装版本，不声称冻结 OS 或 provider 内部。

每条之前、批次结束及评分后复核绑定通过。预检确认普通工具 schema 和非 Foundation metadata 的提示一致，能力列表差异正是实验处理；没有通用 Shell。原 24-trial 批次的 367 份数据文件再次核对未变。

第一次准备目录 `output/foundation-followup-v2-20260915` 的本地预检误把预期的 Foundation metadata 增量当成非能力提示差异，失败于真实调用前；两条 FunctionModel 检查不计真实 trial。修正比较范围后另建 b 快照并通过预检，未修改题目或 Skill，未覆盖旧准备目录。

相关离线测试最终 13 通过；新 driver/prepare 的 BasedPyright 0 errors，Ruff 通过；两臂 FunctionModel 集成预检通过。没有因测试而调用模型或 Abaqus。

## 按原冻结标准评分

评分为本任务助手逐条读取完整 analysis.json 的非盲语义审查，所有主项理由写入 host_annotations；不是独立专家双标或另一个隐藏 judge。主任务通过与最高错误严重度分别保留。严重度沿用 S0–S3 定义，不把所有格式/依赖边界问题一律升级为 S2。

| case | repetition | Control | Foundation | 配对结果 |
|---|---:|---|---|---|
| 缺少用户物理输入 | 1 | fail / S2 | pass / S0，零正文加载 | win |
| 缺少用户物理输入 | 2 | fail / S1 | fail / S1，加载 formulation | tie |
| 结果语义 | 1 | pass / S1 | fail / S2，加载 postprocess | loss |
| 结果语义 | 2 | fail / S2，加载原 Abaqus Skill | fail / S2，加载 postprocess | tie |
| 孔板方法/证据边界 | 1 | fail / S2 | pass / S1，加载 postprocess/verification/discretization | win |
| 孔板方法/证据边界 | 2 | pass / S1 | timeout，加载四个 Foundation Skill，无 analysis.json | unpaired_unknown |

全批有效样本的 S2/S3 为 Control 3/6、Foundation 2/5；只看五个有效配对，两臂分别 3/5、2/5。不得把分母不同的数量比较写成稳定错误率改善。

### 主要采用与失败证据

**缺参停止**：四条都识别到板厚并进入旧澄清停止。但 Control 两轮都计算假设 t 下的反力示例；Foundation 第一轮没有、第二轮加载 formulation 后仍计算 t=1/5 mm 的反力。示例没有被采用成模型输入，因此 `no_fabricated_thickness` 可通过，但违反冻结的 `stop_before_dependent_work`。第一轮 Control 还错误称面内刚度矩阵与厚度无关，记 S2；第二轮两边的反力估算标为近似且含截面高度，记 S1 边界违规。这里有正向信号，也有加载后仍违反的直接反例。

**结果语义**：Foundation 第一轮把题目中的 Viewer 平均节点峰值解释成显示分辨率网格上的峰值，甚至声称不受节点位置约束，改变了该项结果含义；第二轮则在正确介绍 ELEMENT_NODAL 后又称它包含跨单元/显示平均。Control 第二轮同样用“写出时已启用平均”的例外混淆 ELEMENT_NODAL 与单独的 averaged-at-nodes 路径，按相同主项判 fail。三者都提供了不少正确合同字段，但这不能抵消核心语义矛盾。Control 第一轮的普遍禁止跨口径比较等过度表述另记 S1。

该语义判定依据冻结 rubric 对 IP/逐单元外推/Viewer 节点平均的区分，并辅核本机 2026 FD02 `simacae-c-resconceptaverage.htm` 的节点平均说明，不冒充本轮持证的 2024 API 行为验证。2024 对应网络页面直接读取返回 403，本轮未据此推断额外版本能力。

**孔板方法**：三条完成答案都保留了有限板精确数值未知；新题已不再强迫模型记忆文献常数。但是 Control 第一轮把孔壁法向沿 X 点的非零环向压应力误写为全局 sigma_xx（自由面该分量应为零），混淆净/毛截面集中系数趋势，还写出与外载同向的 +X 支反力检查。Foundation 第一轮较好保持分量、反力方向与未知边界，但仍有把孔边距离写成孔心距离等 S1 问题。Control 第二轮方法主项通过，额外的单调从下方收敛/节点平均系统性偏低等断言记 S1，不能直接提升成通用指南。

所有主项理由与严重度都可在 annotations 中逐条复核；这批结果不作为对整个 Foundation 文本科学性的全面审计。

## 五个有效配对的成本

| 指标 | Control | Foundation | 差异 |
|---|---:|---:|---:|
| 已完整收到的模型响应 | 20 | 20 | 0 |
| 输入 token | 181,791 | 200,231 | +10.1% |
| 输出 token | 38,751 | 30,464 | -21.4% |
| 总 token | 220,542 | 230,695 | **+4.6%** |
| outcome 工具调用 | 20 | 20 | 0 |
| 累计 wall 秒 | 368.9 | 214.1 | -42.0% |

没有双方均通过的配对，因此不能给出同等质量交付的成本估计。相同预算下有效配对的 wall 更低也不能归因 Skill：Control 有重试，而最慢的 Foundation timeout 配对被排除了。

全批记录到的 token 是 Control 326,576、Foundation 242,889，不能用它宣称节省。后者包含未完成任务，仅记录已收到响应；未返回响应的计费未知。缓存命中与其他 usage 保留在每条 history/审计里，token 不直接等于费用。

## HTTP 与 timeout

新增 transport_events 记录开始、响应头及 trial 终止，不记录凭据或请求正文。全批有 **56 次 HTTP attempt、47 次 HTTP 200 响应头、47 个完整模型响应**；其中 8 次 attempt 带 SDK retry 标记，所以共启动 48 个逻辑请求，最后一个未完成。响应头本身不等于完整响应，此处另与保存 history 的 47 个响应交叉核对。

唯一 timeout 是 r2 孔板 Foundation：8 次 HTTP attempt、5 次 retry，只有两个完整模型响应；已完成文件/加载工具反馈，第三个逻辑请求未在 wall 上限内完成，没有 analysis.json。其他两个孔板 Control 分别发生 2 次、1 次 retry。没有证据表明这是 Abaqus、脚本执行或 Skill 文件读取 timeout。

## 结论与后续边界

保留五个 Foundation Skill 的显式可选定位，暂不默认启用，不扩展题量或领域包。新题让“方法/证据边界”更可评估，但实际结果混合：2 胜、1 负、两组双方失败，不能将小样本净胜一对当作稳定收益。

值得优先审查的是**正确规则已读到后，模型又在额外解释中改变语义或越过停止边界**。不能把这些输出全部归结为资料缺失，也不应把每个错句都变成全局 Skill 补丁。当前没有必要再扩大同类离线重复；后续是否转到真实简单建模对照，另按明确任务与宿主验收边界决定。

## 记录入口

- 实际批次：[foundation-followup-v2-20260915-b](../../output/foundation-followup-v2-20260915-b)。
- [seal](../../output/foundation-followup-v2-20260915-b/seal.json)、[preflight](../../output/foundation-followup-v2-20260915-b/preflight.json)、[manifest](../../output/foundation-followup-v2-20260915-b/manifest.json)、[report](../../output/foundation-followup-v2-20260915-b/report.json)。
- [host annotations](../../output/foundation-followup-v2-20260915-b/host_annotations.json)、[audit](../../output/foundation-followup-v2-20260915-b/audit.json)、[transport summary](../../output/foundation-followup-v2-20260915-b/transport_summary.json)。
- seal SHA-256：`239222e446013d80241af57a9b763421f107b0c65784e86dcb52edf76758e172`。
- manifest SHA-256：`75203096e33de9ee3606be4d419ba5c12c7bc89a555021ab578bec8dd16604b5`。
- report SHA-256：`0ec20f554bdc0d938ac2ccefb00c90bcd2a5112d511e3a3a085223c20b13f7ee`。
- annotations SHA-256：`7bb80b983d25c12c06f48b00ae2b334dc982879e74e3bcb14187e2dada922ad8`。
- audit SHA-256：`64527295efbc48152ffc9778fb217002ee48eb62ad5cfd853b19423d63be2e00`。

这是评测报告，不作为模型可读知识条目或独立工程验证证书。
