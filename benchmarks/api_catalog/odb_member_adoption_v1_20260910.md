# ODB成员目录：真实Agent采用对照

日期：2026-09-10。状态：预定六次运行全部结束，无补跑、resume或人工修补。

## 结果

在同一个离线局部修复任务中，旧 final15 为0/3完成正确修改，新成员目录为3/3。新目录三次均自主查询
`OdbAssembly.nodeSets`，收到成员声明后修改并回读脚本，最终明确引用abqpy 2024.10静态来源。
这次已有“真实模型查询—证据送达—目标修改”的观察，不再只有确定性工具接线测试；但仅支持这个
固定development任务，不能外推稳定成功率、任意ODB修复或物理仿真能力。

| 运行顺序 | 目录／重复 | 最终结果 | 模型请求 | API查询尝试／found计数 | 文件修改 |
|---|---|---|---:|---:|---|
| t01 | 旧 final15／1 | 模型请求预算耗尽 | 12 | 12／3 | 未修改 |
| t02 | 新成员目录／1 | execution_evidence完成，固定AST检查通过 | 6 | 3／3 | 正确窄范围修改 |
| t03 | 新成员目录／2 | execution_evidence完成，固定AST检查通过 | 6 | 3／3 | 正确窄范围修改 |
| t04 | 旧 final15／2 | 模型请求预算耗尽 | 12 | 12／3 | 未修改 |
| t05 | 旧 final15／3 | 模型请求预算耗尽 | 12 | 12／2 | 未修改 |
| t06 | 新成员目录／3 | execution_evidence完成，固定AST检查通过 | 6 | 3／3 | 正确窄范围修改 |

这里found计数指消耗产品API lookup额度的返回次数，不是金钱计费；not_found、ambiguous、duplicate仍占用
模型往返，但不消耗found lookup额度。两组总计：

- 模型请求：旧36，新18；全部工具调用：旧42，新21；API查询尝试：旧36，新9。
- found API预算计数：旧8，新9。不能将尝试次数下降说成found查询次数下降。
- 输入token：旧326,171，新140,133；输出token：旧2,198，新2,890。新目录完成后提供了更长解释，
  输出token并未降低。
- 宿主累计wall-clock：旧约64.89秒，新约42.29秒。网关存在prompt缓存，以上是描述性开销，不是通用
  延迟或成本结论；美元费用不可用。
- 6次均未越过最终编辑边界、未澄清、未运行Abaqus/Solver。旧组没有尝试编辑；新组每次只有一次成功
  `edit_file`。没有观察到“拒绝越界编辑后自行修复”的真实路径。

## 对照条件

任务沿用原来的5行 `extract_results.py`：只修正 `getSubset(region='LEFT')` 的实参，并解释依据。
用户已指定装配层节点集合LEFT；本题不要求模型在未知模型事实下自行选择集合所有权，也没有真实ODB。

原历史参考包中的人工not_found提示被移除，解释交付从 `analysis.json` 移至typed summary。两组使用
同一任务，没有预置标准修正表达式，没有要求必须查询或指定查询名；原案例是离线检查，没有实际
traceback，因此本次也未添加假想报错日志。这是明确适配后的新批次，不与历史参考包试验混算。

唯一处理差异是选用旧 final15，或选用保留全部final15方法内容、附加85条成员的独立目录。两组都使用
显式full模式，保留同一Skill、相同方法卡和runtime notes；不是拿“没有任何方法证据”的弱对照组比较。
模型初始工具schema、基础指令、实际送达的 `FieldOutput.getSubset` 与参数卡已逐条核对相同。

固定profile：OpenCode Go / `deepseek-v4-flash`，temperature=0，thinking disabled，单次输出上限
2400 token，HTTP timeout 60秒，HTTP retry=0。每run上限12次模型请求、4次found API lookup、240秒；
Concept、Planning工具与所有Abaqus runtime工具关闭，Abaqus/Solver预算为0，进程策略另设拒绝执行。
六次使用独立workspace、history和唯一session header，顺序为旧/新、新/旧、旧/新。没有中途调参。

冻结manifest SHA-256：`6a48919f8b88ed7cdbd5cfbcc217d76e4b2f19c6739733accb57f6cb4c84cb31`。
绑定了任务、产品实现、Skill、目录所有文件、provider配置及预检记录。运行前后哈希一致。
正式调用前完成了无网络wire/composition检查与55项相关合同测试；第一次无网络预检后只整理了驱动
的lint写法，再以最终源重新预检并冻结，没有消耗额外真实模型样本。

## 从轨迹能确认什么

六次共有相同的前缀：读取脚本、加载领域Skill、查询 `FieldOutput.getSubset`、查询
`FieldOutput.getSubset.region`，然后查询 `OdbAssembly.nodeSets`。

旧组在最后一步not_found后继续寻找类名、其他成员、构造方法或重复同一查询。三次均耗尽模型请求
预算，由宿主形成failed outcome，文件保持原样；这不是Agent主动完成的“证据不足、停止修改”决策。

新组三次在成员found后都直接进行同一修正并回读：

```python
subset = field.getSubset(region=odb.rootAssembly.nodeSets['LEFT'])
```

三个最终文件raw SHA-256均为
`f3384a0c687221b2472371ab01eb196b199b38dd3b9aadebb21cd52e8608d172`。
产品verifier只检查freshness、Python 3.10语法等execution evidence；固定宿主AST检查另外验证目标
表达式和其余AST不变。字节边界由原有reference-edit gate检查，允许平台换行规范化。

据此可报告：本题成员缺口获得直接补充，真实模型采用了新证据，并观察到局部收敛改善。由于只有
同题三重复、temperature=0且有provider缓存，不能把三次当作三个独立任务，更不能声称一般性RAG优势。

此前[scoped-edit v2](../rag/scoped_edit_codingrun_v2_20260909.md)在不同参考包、Catalog关闭的条件下也
完成过相同目标表达式的修改。它与本批任务输入/工具配置不同，不能混算，但提醒我们：本次0/3不证明
模型没有相关先验知识，也不证明RAG是这道题的必要条件；此次对照验证的是显式full工具路径中补齐成员
覆盖的作用。

## 不能被“3/3正确修改”掩盖的问题

1. **旧组三次都把未发生的错误文本放进查询参数。** 共6次 `error_excerpt` 包含TypeError或KeyError
   样式文本，但任务没有日志，模型也没有运行代码。另一个纯粹表达知识缺口的error_excerpt不计入这6次。
   这些内容是模型生成的假设，不能作为observed runtime evidence回流到后续审计或知识库。
2. **新组目标成员的来源说明合格，不等于所有额外说明都准确。** 三次都说明成员来自abqpy 2024.10
   静态声明、方法官方描述包含2026 FD02、当前未执行；目标OdbSet引用有依据。但旧方法卡本身仍存在
   来源差异：stub包含 `region: FieldOutput`，2026官方描述对应项为 `OdbInstance`。
   t03把FieldOutput变体概括为可接受的运行对象；本次没有验证该重载。t06则把OdbInstance说成2024
   重载注解，未准确区分它实际来自2026描述。这里确认的是来源归属/证据范围问题，不据本次零运行
   试验直接裁定该重载在真实2024内核中是否存在。
3. **t06的权限解释也不够准确。** 它说假设的实例级表达式“不在授权跨度内”；实际上这个引用形式
   可以通过结构span gate，真正不能改变的是用户指定的装配级语义。模型最终没有执行该替代修改，
   所以实际交付仍正确，但解释不能混淆结构编辑权限和任务语义。

因此不把结果合并为“完整行为与解释3/3通过”。已证明的是目标修复与成员证据采用；上面的局限原样
保留，未据此修改Prompt、Skill、目录内容或补跑这批样本。

## 证据与下一步边界

[机器报告](odb_member_adoption_v1_20260910.json)保存逐run outcome、原始查询参数、工具返回、usage、
源哈希和人工分轴评述。原始冻结与运行记录位于 `var/audits/odb-member-adoption-v1/`，驱动与只读
汇总脚本分别为 `var/audits/probe_odb_member_adoption_v1.py`、
`var/audits/summarize_odb_member_adoption_v1.py`。汇总正常完成，没有恢复或替换运行。

本次不更改默认Catalog或推荐Gold profile，不将新成员证据提升为runtime note，也不将输出当作物理
验收。Riks候选召回、warning排序与Concept入口的自动路由均未修改，应作为独立问题处理。
