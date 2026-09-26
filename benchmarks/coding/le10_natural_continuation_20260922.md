# LE10：从第40次请求停止点自然续跑10次

2026-09-22。用户明确认可先检验预算中断的影响：在独立诊断分支保留原题设、历史和工具，只追加有限请求，
不提供支承错误提示。仓库基线 `d0e5d55368d7668dbd3a00510d50fa777e790633`。

**追加10次请求后，Agent没有定位或修改错误支承，再次在累计50次请求处失败。** 新增动作集中于ODB提取诊断、
修复诊断代码和读取Solver日志。建模脚本、INP、ODB与结果JSON全部保持原字节；没有新的CAE或Solver执行。
10个HTTP attempt全部完成，无Provider失败。

本结果支持“原运行并非只差这10次请求就能自主修复”。它仍是一个有界、单次的时间分离诊断，
不证明无限继续也不会修好，不给出通用失败率，也不证明新增工具、Foundation或某种Prompt会有效。

## 原因问题与实验边界

原[LE10正式run](public_mechanics_v11_nafems_le10_thick_plate_extension_20260917.md)已在第40次响应识别应力异常，
但下一次请求超预算。之前调查证明脚本把 y=0 的 U2 支承错选到了内椭圆柱面；
[单变量独立求解](le10_sym_y_isolation_20260922.md)又证明只修正 SYM_Y 就能使S22通过原10%容差。
这些事后诊断和修正结果没有交给本次Agent。

本次问题仅是：从原停止点继续，它会查什么，能否自行找到并修正，额外成本是多少。
不是从零重新做题，不是重新打分原trial，也不是对比Foundation是否有效。

## 预先固定的配置

诊断目录：`D:/abaqus-runs/le10-natural-continuation-20260922`。

| 项目 | 设置 |
|---|---|
| 原始来源 | `public-mechanics-v11-nafems-le10-thick-plate-20260917` 的最后完整checkpoint |
| Provider/model | 原 `opencode-go / deepseek-v4-flash` |
| 推理/网络配置 | 原 `enabled_high`；HTTP timeout600秒、retry0 |
| 请求限额 | 唯一资源上限变化：累计40→50，即追加10次 |
| 累计用量 | 原40请求、50工具调用、10次Abaqus、1次Solver、5次API查询全部继承 |
| 其他边界 | active wall总计2700秒、Abaqus18、Solver6、API8及文件allowlist均保持原值 |
| 工具/Skill | 原18个function tools、3个完成输出；coding+五个Foundation deferred入口；知识目录/API模式原样 |
| 历史 | 原81条消息，最后是 results.json 工具返回；没有新增用户消息或诊断提示 |
| 工作区 | 从错误的原工作区逐字节复制；没有复制人工修正模型 |
| verifier | 原 `results_first_v1`、原Case和oracle；仅沿用原来的ready触发时机 |
| 停止规则 | 一次有界尝试；不自动续跑、换模型、再扩预算或人工介入 |

最终核对：新 history 的前81条消息的 `parts` 与原始history逐项完全相同；全历史仍只有原来的一条
user-prompt。instructions中仅重定位工作区路径。模型始终可以看到原来的应力目标、原异常数值和历史工具反馈。

## 诊断分支怎样建立

原run已是failed，冻结batch命令不允许把这种终态直接当普通resume。为实现用户授权的诊断，使用独立复制目录：

1. 复制冻结源码、Catalog、知识快照、工作区、全部历史和tool-effect ledger。原failed状态原样保存在
   源批次和新目录的baseline；不读取今日launch v7替代旧配置，不迁移state schema。
2. 只重定位状态中workspace/state/raw-log路径字段、instructions的工作区路径，并相应重建历史执行记录的
   观察索引key。工作区文件和历史tool-return正文不改；原raw stdout/stderr字节保留。
3. 在诊断副本将请求上限设为50，将复制的resume投影标记为可启动状态。该 `failed→interrupted` 是
   **显式实验分支激活**，不声称原run发生过Provider中断，也不作为产品能力。
4. 本地诊断service子类复用冻结resume恢复及安全检查，只移除默认的“infrastructure interruption”恢复提示，
   让下一请求直接接在原工具结果后；不添加新的物理内容或审查指令。
5. 保留累计usage、原空工作区freshness baseline、原execution offset及全部已完成runtime证据。
   不使用新Web continuation重划验收基线。

准备前394项批次文件、2629项外部runtime文件、launcher hash全部匹配。离线FunctionModel捕获确认了18个工具、
六个deferred入口、81条原历史，以及继承的runtime/API计数；没有运行模型生成或vendor。

预检发现旧ledger遗留一个只读 `read_file` started effect：`call_582f3de5c4da4b6d9621e327`，对应原H44的
“Offset 17500 exceeds file length” ModelRetry。没有未决runtime或写入effect；保留原ledger，不推断补写完成状态。
原生恢复选择到最新complete snapshot。初次预检因过严的“无任何未决effect”断言停止，随后修正诊断预检；
另一次本地捕获哨兵被service包装为基础设施异常，修正捕获方式后通过。所有准备尝试均保留在control/preflight中，
真实Provider调用只发生在之后的唯一运行。

Harness横幅中的“11 capabilities”包含总装配能力；实际deferred Skill名单为六个。两种数量不能混用。

## 自然续跑轨迹

以下使用累计请求编号R41–R50；每轮只有一个工具调用。描述以可见文本、工具参数和返回为主。

| 请求 | 实际动作 | 结果与边界 |
|---|---|---|
| R41 | 给extract_results.py增加整体S22、D附近积分点/节点值及位移诊断 | 选择先检查提取与应力场；没有修改建模脚本 |
| R42 | 运行提取脚本 | 新增的 `S.position` 不存在而失败；工具附runtime note |
| R43 | 删除错误属性读取 | 根据工具提示修复；该属性不存在的知识在旧历史中也曾出现 |
| R44 | 再运行提取 | 成功；D附近单元应力很小，全场约±13MPa；D值仍+3772.9Pa |
| R45 | 增加沿y=0顶面路径与极值位置的诊断 | 新代码引用未定义变量 |
| R46 | 在执行前发现并改写这段诊断 | 消耗一次请求；未定义变量没有进入vendor执行 |
| R47 | 运行提取 | 成功，但路径列表为空；ODB顶面z约0.6000000238，所用1e-9容差太严 |
| R48 | 识别容差问题；搜索model.dat里的边界/载荷相关词 | 找到部分surface/统计行，没有获得支承集合成员 |
| R49 | 读取model.msg | 获得原Solver收敛/无警告日志，未核查实际选中的支承节点 |
| R50 | 放宽路径坐标容差，增加内边路径诊断 | 编辑成功，尚未再次执行；下一请求超50次上限 |

这些新增诊断并非全部无信息：它确认了分量/位置之外还存在局部场异常，也主动发现了两个新增代码问题。
但整个追加窗口内没有 `read_file(build_model.py)`、没有直接检查INP支承成员，没有识别 I/J 错位，
也没有新的 `load_capability` 或知识文件读取。

保存的reasoning与可见文字仍多次把“内边自由”作为当前模型的解释前提，并围绕局部应力、外推、边界层或
网格等提出假设。实际内边已经被错误施加U2；这些表述不能当作模型事实。后期虽开始找边界信息，
选择的是Solver日志而不是对集合坐标/构造条件的检查。不能从这些记录进一步声称知道模型内部为何如此选择。

## 结果与新增成本

| 项目 | 新增量 |
|---|---:|
| 完整模型响应 / HTTP attempts | 10 / 10，全部completed，0 failed |
| 工具调用 | 10：edit_file5、run_abaqus_python3、search_files1、read_file1 |
| Abaqus Python执行 | 3：1次失败、2次成功 |
| CAE / Solver执行 | 0 / 0 |
| API计数 / Foundation加载 | 0 / 0（错误后仍有旧机制提供的API提示） |
| 输入tokens | 1,274,311 |
| 输出tokens | 28,988 |
| active wall | 约496.52秒，约8.28分钟 |

累计请求50，active wall约1777.49秒，低于2700秒；本次终止仍是model_budget，未触及runtime或wall上限。
唯一发生字节变化的工作区文件是 `extract_results.py`。`build_model.py`、`model.inp`、`model.odb`、
`model.cae`、`results.json` 都与原工作区字节相同，S22仍为+3772.9123535 Pa。

未申请ready、未进入宿主物理verifier，最终诊断投影为 `failed/model_budget`。由于ODB和INP完全未变，
本次没有额外启动事后ODB读取器；原独立measurement仍对应这些相同字节。最后的提取脚本编辑未验证。

## 对下一步的约束

- 预算确实截断了原来的调查，但追加10次没有自然转化为支承错误定位或修复，不能再说“只差这10次就会修好”。
- 这不是永不收敛的证明，仍然存在预算截断；也不是扩预算与改提示的随机配对效果量。
- 更具体的待测问题是：能否让它把“题目描述的模型”与“实际代码/集合形成的模型”先对照起来。
  下一步若做干预，应单独比较这种排查方法指导与本次自然续跑，不同时增加工具、加载全部Skill或直接告诉错位答案。
- 原正式trial、seal、model_budget失败以及人工单变量实验都保持各自身份。原批次539个文件运行前后完全一致。
  本报告没有修改产品默认值、Prompt、Skill、权限或正式plan。

## 证据

- [实验manifest](D:/abaqus-runs/le10-natural-continuation-20260922/manifest.json)
- [准备seal](D:/abaqus-runs/le10-natural-continuation-20260922/seal.json)
- [完成证据seal](D:/abaqus-runs/le10-natural-continuation-20260922/completion-seal.json)
- [离线预检](D:/abaqus-runs/le10-natural-continuation-20260922/control/preflight.json)
- [实际history](D:/abaqus-runs/le10-natural-continuation-20260922/nafems-le10-thick-plate-pressure/state/history.json)
- [执行journal](D:/abaqus-runs/le10-natural-continuation-20260922/nafems-le10-thick-plate-pressure/state/execution_results.jsonl)
- [HTTP观测](D:/abaqus-runs/le10-natural-continuation-20260922/control/transport.jsonl)
- [原始诊断结果](D:/abaqus-runs/le10-natural-continuation-20260922/result.json)
- [紧凑比较](D:/abaqus-runs/le10-natural-continuation-20260922/analysis.json)
- [诊断驱动](D:/abaqus-runs/le10-natural-continuation-20260922/diagnostic.py)
