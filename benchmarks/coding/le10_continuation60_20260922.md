# LE10：两组从50次继续到最多60次

2026-09-22。用户批准两组分别从自己的第50次停止点复制到新目录，再各允许最多10次请求，不加入任何新提示，
时间、Abaqus、Solver和API上限保持原值，累计消耗不清零。仓库基线为 `d0e5d55368d7668dbd3a00510d50fa777e790633`。

**指导组执行了自己写出的检查，定位并修正SYM_Y，实际得到通过原物理指标的解；随后选择加密，
最终留下未求解的新输入并在60次处失败。自然组在53次就误判题设与参考矛盾，请求改变U3支承，
触发旧澄清协议失败；它没有耗尽本轮请求或时间额度。** 两组均没有正式完成，但工程进展明显不同。

前序记录分别是[自然续跑到50次](le10_natural_continuation_20260922.md)、
[方法指导到50次](le10_method_guidance_20260922.md)、[人工单变量支承实验](le10_sym_y_isolation_20260922.md)。
原v11正式trial及前序分支的失败、seal和成绩保留，不回填本轮结果。

## 条件与环境

目录：`D:/abaqus-runs/le10-continuation60-20260922`。`arm-a`为指导组，`arm-b`为自然组；
预先固定执行顺序为指导组→自然组，真实任务/runtime并发均为1。

| 条件 | 指导组起点 | 自然组起点 |
|---|---:|---:|
| 已消耗模型请求 | 50 | 50 |
| 本轮累计请求上限 | 60 | 60 |
| 已消耗active wall | 2051.40秒 | 1777.49秒 |
| 原2700秒上限下剩余时间 | 648.60秒 | 922.51秒 |
| 已消耗Abaqus / Solver | 13 / 1 | 13 / 1 |
| 保留的累计Abaqus / Solver上限 | 18 / 6 | 18 / 6 |
| 保留的用户消息 | 原题设+前轮方法指导 | 原题设 |
| 本轮新增提示 | 无 | 无 |

仍使用冻结 `opencode-go / deepseek-v4-flash`、`enabled_high`、HTTP timeout600秒/retry0、原18个工具、
原Skill/Catalog/知识快照、文件allowlist和 `results_first_v1` Case verifier。
没有把“528个错位节点”、研究者结论、人工修正结果或对照组轨迹注入任何模型上下文。

复制与恢复沿用前轮诊断机制：原父分支完整保留，副本重定位必要路径，保留工作区字节、原始freshness基线、
execution provenance和全部usage，仅激活诊断副本、将50上限提高到60，并抑制默认的基础设施恢复提示。
指导组旧hash冲突的edit_file未结算记录保持原样，history已有对应拒绝结果，workspace匹配最新checkpoint；
没有未决runtime，未猜测补写effect状态。

预检捕获的有序消息parts与各自父history完全一致，无新增user-prompt；原生合并相邻ModelRequest可能改变
message数量，但没有删改parts。两组工具schema/instructions及累计用量分别核对通过，预检不调用真实模型或vendor。

准备时发现共享虚拟环境已升级：Harness0.31.0→0.33.0、Pydantic AI2.43.0→2.47.0，93个绑定文件不同。
因此最初准备在真实调用前停止。使用冻结pyproject/uv.lock，经uv缓存离线安装到本工作树独立 `.venv`，
重新验证 **2629个绑定runtime文件全部逐字节匹配**。共享环境未修改。
另以 `--no-deps --offline` 安装项目0.1.0的版本元数据；实际执行的项目模块路径明确断言来自复制的冻结snapshot。
上述准备失败及环境恢复单独记录，不计模型trial，不混入active wall。

## 指导组：从检查到修复，再进入未完成的加密

| 累计请求 | 动作及证据 |
|---|---|
| R51 | 改写上一轮检查中的条件循环，声称存在语法错误。上一轮离线3.10 AST已通过；这是模型主动改写，不是新vendor语法报错 |
| R52 | 实际运行检查，工具返回明确包含 `SYM_Y nodes=535 outside-expected=528`，其他几何集合检查为0 |
| R53 | 明确指出J是径向、J=0是内椭圆，y=0应为I=0；将set_sym_y改为I0==0筛选 |
| R54 | 增加保存CAE前删除旧model.cae的处理；不改变物理参数 |
| R55 | 运行CAE，生成修正后的CAE/INP |
| R56 | 读取生成日志并查询输入，确认SYM_Y现为183节点 |
| R57 | Solver按修正INP完成 |
| R58 | 提取成功，S22=−5.562129 MPa，反力正确，所有所写集合几何检查通过 |
| R59 | 已明确认识到应力误差约3.4%、在10%容差内，但又选择48×16×3→64×20×4加密 |
| R60 | 运行CAE生成加密输入；没有随后Solver/提取，下一模型请求超60次上限 |

这里首次形成了可观察的采用链：模型自写几何检查→真实tool return暴露528个不合条件成员→模型正确解释→
实际修改代码→CAE/Solver/提取结果改善。这个528不是研究者给它的提示。

冻结题设明说“不强制第二次求解，是否需要加密由实际误差决定”；case.refinement=false。
加密并非被禁止，但在当前已满足误差目标时，不是本次Case强制的后续步骤。
不推断模型知道所有剩余预算；事实是它在获得合格结果后选择了额外工作，并覆盖了最终输入。

## 指导组独立复核与最终状态必须分开

两组都结束后，研究者另外执行一次冻结trusted ODB读取器，无新Solver或Provider调用。
该读取没有向Agent反馈，也没有改变Agent usage、终态或workspace字节。

| 已完成求解阶段的独立指标 | 测量 | 冻结目标/容差 |
|---|---:|---:|
| D点全局ELEMENT_NODAL S22 | −5,562,129 Pa | −5,380,000 Pa；误差3.3853%，通过10% |
| 外椭圆中面RF3总和 | 5,448,699.73828125 N | 5,448,699.758569798 N；通过0.2% |
| 体积 | 3.2692198505974375 m³ | 与原几何一致 |
| 独立模型身份 / 对应输入检查 | `[]` / `[]` | 通过该已求解阶段的有界检查 |

R57实际Solver输入hash为 `420c7efb650c0a7f882c253267a300355b8d0c5a6ed71c0896ab7efc162d448b`；
R55的artifact记录也绑定这个hash。由于R60已覆盖最终INP，研究者从前序封存的人工单变量实验取得
**hash完全相同的输入字节**，仅用于与本组新ODB一起复核，不写回Agent工作区、不重新求解。
这不是推测修复旧state，而是按本组实际Solver fingerprint检索已保存的相同输入。

最终文件存在明确的阶段不一致：

- 当前ODB/JSON来自修正后的12231节点、2304单元网格；ODB hash为
  `5d2a320d5bed7a4e31f3c1cebf7e7edd141cc74bcc494eeb6e70f512142811cc`。
- 当前CAE/INP已是25505节点、5120单元的未求解加密模型；INP hash为
  `f939e881c2c19502d625b52637b0e3447c04456c19561e0e00dc452bbde90e97`，与最后Solver输入不匹配。
- 没有report.md、没有ready申请；正式诊断终态仍为failed/model_budget。Abaqus累计也已达18次上限。

所以只能记为“已完成阶段的独立物理检查通过”，不能记为最终交付通过或把现有ODB冒充新网格结果。

## 自然组：未纠错，错误归因为题设冲突

| 累计请求 | 动作及证据 |
|---|---|
| R51 | 执行上一轮最后编辑的应力路径诊断，原错误应力保持不变 |
| R52 | 根据应力分布断言外边支承与参考值不相容，搜索LE10相关文件、列知识脚本目录 |
| R53 | 返回NeedsInputDecision，要求确认U3支承应在内边还是外边，声称外边支承不可能达到参考应力 |

这不是实际物理缺参或已成立的题设矛盾。指导组保留原外边U3支承，只修正错误的Y对称集合，就得到了
独立通过的应力和反力；前序单变量实验也给出相同反证。
自然组将自己尚未确认的“实际模型等于字面题设”当作前提，把错误结果解释成了用户输入冲突。

旧service在已经调用Abaqus后收到required-input决定，返回failure_stage=`clarification`，summary为
`Agent invoked Abaqus before obtaining required input`。这不是model_budget或HTTP失败，也没有实际向用户索取或获得新物理输入。
原model.inp和model.odb均与v11错误模型字节相同，没有必要再启动vendor读取器；沿用相同hash对应的原独立measurement。

## 分阶段结果与成本

| 结果 | 指导组 | 自然组 |
|---|---|---|
| 实际执行集合几何检查 | 是，R52 | 否 |
| 正确定位并修复原支承错误 | 是，R53 | 否 |
| 重新求解、得到合格的修正结果 | 是，R57–58；事后独立确认 | 否 |
| 最终文件对应同一个已完成模型 | 否，随后加密覆盖输入 | 原错误模型未改 |
| 正式完成 | 否 | 否 |
| 终止 | R60 model_budget | R53 clarification |

| 本轮50→最多60的增量 | 指导组 | 自然组 |
|---|---:|---:|
| 完整模型响应 / HTTP attempts | 10 / 10 | 3 / 3 |
| HTTP失败 | 0 | 0 |
| 宿主tool_calls | 11 | 3（另有NeedsInput输出工具） |
| Abaqus调用 | 5：检查1、CAE2、Solver1、结果提取1 | 1：诊断提取 |
| 输入tokens | 1,545,559 | 457,681 |
| 输出tokens | 6,904 | 19,148 |
| active wall | 206.44秒 | 410.16秒 |
| 最终累计active wall | 2257.83秒 | 2187.65秒 |

两组本轮都未新增Foundation加载或主动API查询，仍无Foundation收益证据。
研究者最后的一次trusted读取另计，不计入上述Agent runtime使用量。
两组都低于2700秒累计wall上限；自然组还有7次请求额度，却主动进入了错误的澄清路径。
没有双方均正式成功的结果，不能据这些成本宣称同等质量下的效率改善。

## 解释范围

- 本例支持：原有工具可以承载模型自己生成的几何检查；明确的实际成员反馈被模型正确采用后，能够完成这个支承修复。
- 指导组最终修复，自然组错误澄清，是本次单例的行为差异。不能据此宣称方法指导稳定有效，或把新增用户提醒、
  提示内容、随机性和时间分离影响完全分离。
- 原问题已经不能只归结为“再加一点预算就会自然修好”：自然组本轮没有耗尽额度便作出错误的输入冲突判断。
  也不能把指导组的正式失败概括为“不会建模”：它已得到独立合格解，之后的收尾/额外加密属于另一层失效。
- 当前仍没有证据要求增加第二个Agent、新宿主判题层或强制加载全部Foundation，也没有把本轮提示写入产品默认值。
- 后续若讨论改进，应分别研究题设与实际对象的核对、收到异常后的修复、达到任务容差后的结束条件。
  不将本例的节点数、坐标条件或具体修法写成全局规则，不自动继续扩预算或替换历史成绩。

## 证据入口

- [本轮协议与顺序](D:/abaqus-runs/le10-continuation60-20260922/protocol.json)
- [恢复的runtime绑定](D:/abaqus-runs/le10-continuation60-20260922/runtime-restoration.json)
- [准备阶段记录](D:/abaqus-runs/le10-continuation60-20260922/preparation-attempts.json)
- [指导组manifest](D:/abaqus-runs/le10-continuation60-20260922/arm-a/manifest.json)、[history](D:/abaqus-runs/le10-continuation60-20260922/arm-a/nafems-le10-thick-plate-pressure/state/history.json)、[分析](D:/abaqus-runs/le10-continuation60-20260922/arm-a/analysis.json)
- [指导组独立复核](D:/abaqus-runs/le10-continuation60-20260922/arm-a/posthoc/assessment.json)、[读取执行记录](D:/abaqus-runs/le10-continuation60-20260922/arm-a/posthoc/execution.json)
- [自然组manifest](D:/abaqus-runs/le10-continuation60-20260922/arm-b/manifest.json)、[history](D:/abaqus-runs/le10-continuation60-20260922/arm-b/nafems-le10-thick-plate-pressure/state/history.json)、[分析](D:/abaqus-runs/le10-continuation60-20260922/arm-b/analysis.json)
- [本轮完成seal](D:/abaqus-runs/le10-continuation60-20260922/completion-seal.json)
