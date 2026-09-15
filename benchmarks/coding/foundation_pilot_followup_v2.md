# Foundation pilot follow-up v2：题目证据条件与运行前封存要求

状态更新：2026-09-15，用户确认后三题两臂各两次已在独立快照执行，见[12 条结果](foundation_followup_v2_20260915.md)。2 胜、2 平、1 负、1 对 timeout 未配对，0 Abaqus；本页以下保留运行前的题目与封存要求，实际代码/配置以批次 seal/manifest 为准。原 `foundation_public_mechanics_*_v1`、原 24-trial batch 和 host annotations 不改写，本文不用于回填 v1。

## 已确定的题目调整

下一版将孔板题的主要目标改为**有限资料下的方法判断与声明边界**。不要求 Foundation 记忆特定出版物的参考常数，不向全局 Skill 写入题目答案。

候选 case id：`plate-with-hole-evidence-boundary-v2`。

### 精确的候选任务文本

> 分析一个线弹性、小变形静力孔板问题：矩形板长 15 m、宽 5 m、厚 1 m，中央贯穿圆孔半径 0.5 m；E=1000 Pa、nu=0。左端面固定，右端面施加大小 100 Pa 的均匀拉伸压力。关注圆孔表面的最大全局 X 向正应力。
>
> 请定义这个目标量需要保留的位置和分量含义，说明哪些区域需要离散化关注，以及后续应如何核对结果。讨论无限板 Kirsch 的 Kt=3 能支持什么、不能支持什么，明确所用名义应力的含义。
>
> 本任务没有提供此有限板的出版物参考数值，不允许文档检索或运行计算程序。无需猜测精确参考常数。请分别说明已能支持的判断和仍需外部参考或计算才能确定的量；如使用任何近似关系，标明近似及适用条件，不把它宣称为这个边界条件下已经核对的精确答案。
>
> 只写 analysis.json，包含 summary、findings 和 next_action。不写 Abaqus/Ansys API，不运行任何程序。不需要用户提供不存在的“物理缺参”来代替当前缺少的文献证据。

### 主评分项

| id | 要求 |
|---|---|
| qoi_scope | 明确孔表面、全局 X 正应力及取最大值的范围，不能替换成任意位置的 Mises 峰值。 |
| discretization_reason | 解释局部梯度与曲边几何对目标量解析的影响；不以总单元数或通用百分比替代目标量检查。 |
| reference_applicability | 将无限板远场理想化与当前有限板/端部条件区分；名义应力与集中系数对应清楚。 |
| evidence_boundary | 不把未提供、未核对的有限板数值或经验估算宣称成已验证精确目标；正确保留未知可通过。 |
| verification_scope | 提议适用且有前提的核对，并区分全局一致性、局部数值误差和物理模型适用性。 |
| no_runtime | 保持只分析交付，不运行或补造当前任务物理参数。 |

所有已作出的额外工程声明仍需审核。仅写“不知道”而不完成可回答的方法分析不能通过。额外误导性数值、量纲、条件泛化记入 severity，即使其他主项通过；severity 与任务完成分别报告。

本题已经受旧 development 题启发，不得描述为新 held-out 或通用泛化。主问题更明确地告诉模型如何处理资料不足，因此它测试的是该合同下的遵守，不是完全无提示的未知发现。

若以后另测**资料应用能力**，应单建不同 case/profile，向两臂提供同一份经过核对的可读来源片段，检查目标量、条件、近似与数值的正确使用。不得把该处理与无资料方法题合并为一个收益，也不把来源片段说成模型自行检索得到。

## 下一批的比较边界

- 当前建议仍为少量、明确 task 的 Foundation off/on 对照，不扩成 72 条。
- 简单建模与缺参澄清是当前产品验证重点；如使用原生 ask_user，应在两臂显式使用相同协议并冻结新的任务/验收，不套用旧 NeedsInputDecision 成绩。
- 旧 trial 的 timeout 不补跑替换；后续每条具有新 run id，使用新绑定。
- 真实建模题的参数、允许操作、交付物与 host oracle 必须在运行前独立确定。本文没有授权或构造这批物理实验。
- 重复次数、case 集合、调用/时间预算和有效配置须在实际准备时整体声明。不能在看到新结果后增删题目、提高预算或补跑直到通过。

## 必须在新调用前实际产生的封存

1. 保存所有 Agent 实际可见的 goal、seed files、task instructions、能力 metadata，以及独立的 host rubric/参考答案；记录传给模型的投影，保证私有参考不可见。
2. 绑定实际运行 checkout/snapshot 的包内源码（含 factory、model adapter、service、协议及 Harness 适配代码），不能只记录两个入口文件。
3. 绑定六个 Skill 的正文和可读取 references，记录实际 include 集合；source/provenance 数据与用户物理事实分开。
4. 绑定 pyproject.toml、uv.lock、Python 与已安装运行依赖版本；若包来自本地修改源码或 patch，绑定实际加载路径及对应字节。只记录 lockfile 不能证明安装内容完全一致。
5. 保存无凭据的有效运行配置：provider/model、reasoning 设置、单次 HTTP timeout、额外 retry、会话 ID 策略、question protocol、工具开关、Planning、全部预算及 verifier scope。省略值必须解释为已解析默认或 provider default，不能事后声称 high。
6. 当前 prepare 的题目/能力/配置/代码清单通过核对后才能生成新 seal；每条执行前和批次结束后检查绑定，或直接在固定隔离快照中运行。内容变化形成新绑定，原批次保留。
7. 尽可能记录请求开始/结束、HTTP 重试和最后可观察状态，凭据不入日志。缺少的 transport 数据保留 unknown；已有模型响应统计仅称 received responses。

本文件只明确必须取得的产物，未声称上述完整封存已实现或旧批次满足这些条件。不得用本文件 hash 代替将来实际源码、内容和配置的 seal。

## 已完成的离线统计修正

`foundation-skill-paired-pilot-audit-v2` 接受原 v1 输入和原 host annotations，保留原工程评分，增加：

- 中断情况下 `received_responses_only`/`unknown` 的 token 覆盖标记；
- 同 case/repetition 的有效配对与双方通过子集，逐指标缺失数；
- 同一响应中的工具分组、有效返回/失败/校验拒绝/其他重试反馈；
- trace 文件实际存在性与最后观察位置，不虚构超时 HTTP 原因；
- 新审计代码自身的 hash，明确这是事后纠正视图。

所有输出写入新文件，旧 audit/report/annotations 不覆盖。配对与 pass/pass 子集有选择条件，不将其称作普遍效果；记录的 token 也不是美元成本。
