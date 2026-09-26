# LE10：增加一段通用排查指导的对照

2026-09-22。用户批准从原第40次请求停止点建立另一独立分支，只增加预先确认的排查指导，仍追加10次请求。
基线为 `d0e5d55368d7668dbd3a00510d50fa777e790633`。对照是
[同日起点相同的自然续跑](le10_natural_continuation_20260922.md)，原正式trial继续保留 `task_failed/model_budget`。

**指导组在最后一次响应写出了能够检查支承集合几何位置的代码，但没有执行它，也没有定位或修正错位。**
两组均在累计50次请求处停止，未重新求解；指导组出现了一个自然组没有出现的中间进展，不能记为完成纠错或验收通过。

## 唯一新增的模型可见内容

> 当结果与参考明显不符时，先核对实际代码和输入文件是否实现了题设，再分析提取方法与网格误差。核对时依据实际对象和数据，不只依据注释、名称或求解成功。

以一条新的user-prompt加入原history之后。没有提及SYM_Y、I/J、节点数量、错位位置或修正答案，
没有改原goal、Skill、主instructions、工具schema、权限或验收标准。已完成的人工修正实验和研究者诊断不进入模型上下文。

## 保持一致的条件

新目录：`D:/abaqus-runs/le10-method-guidance-20260922`。

- 从原v11的第40次请求末尾建立分支，不接着自然续跑的第50次继续。原错误工作区逐字节相同，
  原81条历史消息的全部parts保留，只增加上述指导。
- 沿用冻结源码、Case、Catalog、知识快照；`opencode-go / deepseek-v4-flash`、`enabled_high`、
  HTTP timeout600秒、retry0。18个function tools、3个完成输出、六个deferred Skill入口均与对照一致。
- 累计模型请求40→50；其他上限仍为active wall2700秒、Abaqus18、Solver6、API8及原文件allowlist。
  原40请求、10次Abaqus、1次Solver、5次API用量和freshness/execution baseline全部继承。
- 使用与自然续跑相同的诊断分支激活机制：保留原failed状态和原批次，只在独立副本激活；重定位必要的路径字段，
  不改工作区字节或历史tool-return正文。继承原只读read_file的未结算记录，不猜测补写；没有未决runtime effect。
- 本地service子类对恢复提示的唯一处理区别，是自然组 `prompt=None`、本组 `prompt=GUIDANCE`。
  不使用新Web continuation重划基线，不修改产品源码。
- 一次有界尝试；不在途中提示、不自动续跑、换模型或扩大预算。

真实运行前，离线FunctionModel捕获核对了工具/输出schema和instructions（仅工作区路径不同），
也核对了全部旧parts及唯一新增的指导。workspace仍是原错误模型。预检没有真实Provider/vendor调用。

## 实际轨迹

R41–R50为累计模型响应序号。因为新增user-prompt，history数组下标与自然组不同，不应按下标直接配对。

| 请求 | 行为 | 观察 |
|---|---|---|
| R41 | 提到用户指导，但选择增加全场S22、D附近积分点/节点值及U3诊断 | 尚未核查集合成员，开始修改extract_results.py |
| R42 | 运行提取 | `d_counts` 在定义前使用，失败 |
| R43 | 读取提取脚本局部 | 准备修定义顺序 |
| R44 | 两次edit_file使用同一旧expected_hash | 第一项成功，第二项因文件已变化被拒绝 |
| R45 | 用新hash完成第二项编辑 | 定义顺序修正 |
| R46 | 再运行提取 | `frame.fieldOutputs.get(...)` 不受该Repository支持，失败；已打印部分应力诊断 |
| R47 | 修正Repository访问，并增加内边/径向应力路径 | 继续扩展输出诊断 |
| R48 | 运行提取 | 成功，D点仍为+3772.9Pa；得到内边和y=0顶面路径分布 |
| R49 | 搜索工作区LE10信息、列知识脚本目录 | 开始找参考定义，仍未找到支承错位 |
| **R50** | **写入各支承集合的节点坐标检查及外边中面U3检查** | 首次形成针对实际集合几何的检查；写完即达到上限，未执行 |

R49保存的reasoning把 `SYM_X→U1、SYM_Y→U2、OUTER→U1/U2` 等对应当作“INP边界已核对”的依据。
这些自由度条目本身正确，但集合名不能证明成员的位置正确。原history确有读过这些条目，
因此不能简单说它伪造了读取；问题是把有限的条目核对扩大为整个边界实现已确认。

R50则明确转向实际成员坐标。新增代码包含：

```python
chk('SYM_Y', lambda c: abs(c[1]) < 1e-6, 'y=0 face')
```

这表示行为出现了变化：从检查字段数值或按名字解释约束，转向检查实际集合的位置。
但本次没有向模型返回这段检查的执行结果，未出现它据此定位/修复错误的证据。

## 对最后检查的离线核对

运行结束后只做宿主Python的AST和谓词分析，没有额外调用模型、Abaqus或Solver，也没有执行整个提取脚本。
使用冻结独立measurement中的ODB坐标、未变的INP集合成员，计算模型刚写出的五个几何谓词：

| 集合 | 节点数 | 不符合模型所写几何条件的节点数 |
|---|---:|---:|
| D_NODE | 1 | 0 |
| SYM_X | 183 | 0 |
| **SYM_Y** | **535** | **528** |
| OUTER | 535 | 0 |
| OUT_EDGE_MID | 49 | 0 |

因此这个新增几何条件确实能够暴露已知错位，不只是再次打印集合名。最后脚本通过Python3.10 AST解析。
仍须保留三个限制：

- 完整vendor脚本没有运行，不能认定它一定顺利打印这些结果。
- `chk` 只检查已经选中的节点，不检验是否漏选、集合是否为空或完整覆盖。
- `chk` 打印异常数量，没有assert或失败门禁；即使得到528，还需要模型正确解释和修复。

这份离线结果没有回送给Agent，也没有回填它的正式结果。

## 与自然续跑的对比

| 指标 | 自然续跑 | 方法指导 |
|---|---:|---:|
| 追加完整模型响应 | 10 | 10 |
| HTTP attempts / failed | 10 / 0 | 10 / 0 |
| 新增Abaqus Python | 3：1失败、2成功 | 3：2失败、1成功 |
| 新增CAE / Solver | 0 / 0 | 0 / 0 |
| 新增输入tokens | 1,274,311 | 1,246,684 |
| 新增输出tokens | 28,988 | 26,193 |
| active wall增量 | 496.52秒 | 770.43秒 |
| 写出实际支承几何检查 | 否 | 是，R50 |
| 执行该几何检查 | 否 | 否 |
| 定位/修正支承错位 | 否 | 否 |
| 重新求解、物理验收通过 | 否 | 否 |
| 诊断终态 | model_budget | model_budget |

本组共12个tool-call尝试，其中1个编辑hash冲突；宿主累计tool_calls增量为11。尝试分布为edit_file6、
run_abaqus_python3、read_file1、search_files1、knowledge_list_directory1。没有新Foundation加载，
API计数未增加。最后一次Provider请求较慢，但最终完成；不能将这次wall差异直接归因于指导或换算为费用。

唯一发生字节变化的工作区文件仍是extract_results.py。build_model.py、INP、CAE、ODB和results.json与原模型
相同，错误S22没有改变。未申请ready，未触发宿主verifier。累计active wall约2051.40秒，仍低于2700秒。

## 可以得出的结论

1. 本轮指导被收到、被提及，并最终对应到了一个具体几何检查，但采用很晚，未在10次追加请求内形成纠错闭环。
2. 不能只报告“两个都失败”而抹去中间行为差异，也不能把“写出正确检查”记为自主诊断或物理通过。
3. 没有证据要求立刻增加宿主工具或强制加载Foundation；也没有证据足以将这段指导写入全局默认指令。
4. 对照只有各一次、时间分离、没有盲化或稳定效果估计；“新增一条用户提醒”和“这段方法内容”也没有分别控制。
   因此不能将差异完全归因于指导措辞，或把token/wall差异视为成本收益。
5. 若后续另获授权让本组继续，应保留本次50次上限的失败，明确记录从“检查已写但未运行”开始，
   并区分检查能否执行、异常能否被采用、修复能否完成、独立验收能否通过。不能直接把更大预算结果与本次
   自然组50次结果比较成同预算胜负。

原批次、自然续跑及人工单变量实验保持各自身份；本次没有改产品、Skill、正式plan或旧成绩，未提交或推送。

## 证据

- [本组manifest及指导原文](D:/abaqus-runs/le10-method-guidance-20260922/manifest.json)
- [准备seal](D:/abaqus-runs/le10-method-guidance-20260922/seal.json)
- [完成seal](D:/abaqus-runs/le10-method-guidance-20260922/completion-seal.json)
- [预检](D:/abaqus-runs/le10-method-guidance-20260922/control/preflight.json)
- [history](D:/abaqus-runs/le10-method-guidance-20260922/nafems-le10-thick-plate-pressure/state/history.json)
- [完整结果](D:/abaqus-runs/le10-method-guidance-20260922/result.json)
- [紧凑比较](D:/abaqus-runs/le10-method-guidance-20260922/analysis.json)
- [最后检查的离线谓词分析](D:/abaqus-runs/le10-method-guidance-20260922/last_check_static_analysis.json)
- [最后的提取脚本](D:/abaqus-runs/le10-method-guidance-20260922/nafems-le10-thick-plate-pressure/workspace/extract_results.py)
- [诊断驱动](D:/abaqus-runs/le10-method-guidance-20260922/diagnostic.py)
