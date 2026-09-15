# 单Coder API查证小闭环：首轮开发观察

日期：2026-09-15。**5个API方案场景都选择了预期主入口，并有实际读卡证据；另1个缺厚度场景发出了原生ask_user延期请求。** 这不是完整建模、运行或物理验收，也不将最终解释的每一句话都标成正确。

## 与上一轮意图改写测评的区别

本轮允许模型使用已有知识提出方法名、参数名和技术步骤，然后通过工具核实。未命中可以调整问题或修正候选；不强制翻译用户原话、不禁止提出用户未说出的API、不强制每题语义检索。

复用当前产品的`create_abaqus_coding_agent`工厂和`lookup_abaqus_api`。只在评测专用组装中额外注册一个只读`discover_abaqus_api(operation)`，使用固定v2/E5返回候选，再用既有lookup读取方法、参数或docstring。发现结果不标记为“完整卡片已返回”，所以不阻断后续读卡。

这是一组用户明确要求“查证接口”的任务，要求选用前核实证据；不能据此规定普通建模任务即使已有可靠知识也必须查询。

## 工程边界

- 仍是单Coder，未创建翻译Agent或Reviewer Agent。基础领域Skill为冻结的abaqus-2024-coding，foundation profile显式off；没有启用其他领域Skill。
- API共用现有计数预算8：有效发现调用计1，成功Exact/参数读取沿用原计数；未命中Exact仍按原规则不计费，实际调用总数因此可能高于预算计数。模型请求上限24、wall900秒，HTTP60秒/额外重试10、enabled_high请求设置、Planning开。
- 三个Abaqus运行工具预算为0，通用Shell仍移除；六个场景均0 Abaqus。
- 本轮是原生Agent的单会话评测，不经CodingRunService生成最终CodingRunOutcome，没有普通CLI新工具配置、launch/resume协议或产品waiting_for_user恢复链。证据目录不能被宣传为可用普通CLI续跑的产品run。
- 对ready文件方案另由ExecutionEvidenceVerifier检查fresh文件；对ask_user捕获DeferredToolRequests。不将Agent自报ready当语义或物理通过。
- 代码、core Skill、模型配置、案例、v2索引与驱动在运行前绑定。未修改产品源码、既有索引、旧成绩或默认开关。

## 六个场景的实际轨迹

| 场景 | 模型实际查证行为 | 主入口/交接结果 |
|---|---|---|
| 截面赋给选定Part面，无现成集合 | 直接查Part.SectionAssignment、Part.Set，再查region和faces | 先创建Part.Set，再赋截面；没有谎称集合已存在 |
| 已有表面上的均匀压力 | 查Model.Pressure、docstring和Assembly.Surface；Load.Pressure未命中 | 采用Model.Pressure，复用已有装配表面，未补压力值 |
| 新建场输出U/S/RF | Step.FieldOutputRequest未命中→语义发现→查Model.FieldOutputRequest、构造器、variables/createStepName | 采用Model.FieldOutputRequest，拒绝修改已有请求的setValues系列 |
| 已有Part实例化 | 查Assembly.Instance、docstring、dependent、part及instances成员 | 选择part重载而非model重载，正确放在rootAssembly |
| 用户猜Job.writeInput | 先查猜测名，再查ModelJob.writeInput、Mdb.Job及docstring | 以factory返回ModelJob纠正限定名，不提交Job |
| 实际厚度缺失 | 未查API，直接调用ask_user | 捕获原生延期问题，未填厚度或执行程序 |

Job场景的mdb.Job工厂信息直接由题目提供，本轮没有种入真实建模脚本，不能声称验证了工作区脚本解析能力。

5份api_plan.json均通过文件freshness检查。所有被选主API都确实经过Exact/参数读取，且至少有一个正确的主卡片引用。主助手核对了对象归属、步骤方向和关键参数；不是外部专家签署。

## 语义发现到底发挥了什么作用？

六个场景中只有场输出场景调用了语义发现，共1次；其他API任务主要靠模型已有知识提出候选，再用Exact核实。这是允许的路径，不因没有搜索而扣分。

该次搜索Top-5为FieldOutputRequest的setValuesInStep、setValues、__init__、reset、move，**目标Model.FieldOutputRequest本身不在Top-5**。模型随后仍提出正确的Model入口并精确读卡，且读取了候选中的__init__作补充。

因此可确认“搜索与读卡在同一真实Agent会话中发生，模型没有照搬第一名”；不能证明语义搜索单独找到了目标，也不能证明没有搜索时模型就无法纠正。没有消融，不作单因果收益声明。

这也说明之前“新增findAt/Set名字即不保真”的窄规则不能作为产品原则：模型可以提出正确技术方案，关键是核实证据、不补造用户事实。旧意图改写成绩仍保留为旧协议结果，不回填。

## 仍需修正的解释与引用问题

主入口选对不等于说明完全可靠，本轮至少留下这些注意项：

- 截面方案把Assembly.Set用途概括为“仅用于载荷/边界条件”，范围过窄。当前任务应使用Part层集合，不代表Assembly.Set只能用于那两类用途。
- 压力方案写“Surface即Region”，更准确的表达应是已有Surface可作为此region参数的输入，不能由此宣称二者类型恒等。
- 压力方案在evidence_card_ids里混入了一个实际读到的abqpy record_id。它不是伪造来源，也已有正确主卡片引用，但字段类型使用不规范，不能当成所有引用格式都通过。
- 原生厚度问题附带较长物理解释和示例数值；没有采用示例值，但本轮只确认提出了正确阻塞问题，不验证该说明涵盖所有载荷/约束条件，也没有接收答复后继续建模。

故不公布笼统“6/6全部正确”，而分别记录选择、实际读取、拒绝不适用候选、交接和解释质量。

## 收尾统计错误及一次性整理

首次驱动将当前Pydantic AI的`AgentRunResult.usage`属性当作函数调用，六条均在Agent已经返回、history/输出以及ready文件检查保存之后发生TypeError。原result.json保留了错误；不是Provider失败，也没有因此重新生成任何方案。

驱动已改用属性，并在确定性测试中实际检查返回对象的usage属性。首次驱动保存为本机run_driver_first.py并匹配seal hash；observe_api_discovery_loop.py只读取既有证据，生成observations.json，明确区分收尾错误。

统计为history可见32个模型响应、输入368408/output26523 tokens；这是历史观测，不伪装成精确恢复的SDK总usage或HTTP次数。API计数20，实际lookup trace共28条，其中语义发现1条；六条elapsed合计约366.1秒。未观察到的传输重试不作推断。

## 证据与复核

- [案例](api_discovery_loop_cases_v1.json)：5个接口方案场景+1个缺参交接场景，属于development。
- [驱动](run_api_discovery_loop.py)：当前已修正统计属性使用，实验发现入口不进入普通产品组装。
- [观察器](observe_api_discovery_loop.py)：读既有history、工具trace和计划，核对选中主卡片曾被读取，区分原始record引用与card引用。
- 本机`var/audits/api-discovery-loop-20260915`保留seal、Skill快照、首次驱动、逐case原始result/history/lookup trace、observations。
- 5份原始方案位于`D:/abaqus-runs/api-discovery-loop-20260915/<case>/workspace/api_plan.json`；没有修改模型生成内容来美化结果。

确定性测试覆盖发现后继续读卡、原始需求保留、重复去重、共享预算耗尽及usage属性；已通过。脚本Ruff通过，产品源码hash在运行前后相同；没有新CLI配置、恢复协议、实体Abaqus调用或Git提交。

下一步应先规范上述解释/引用边界，再决定是否把发现入口做成正式显式profile及其恢复接线。不为拿到更多搜索次数而强制模型搜索；查证闭环应允许已有知识、Exact、语义发现和必要澄清各自发挥作用。
