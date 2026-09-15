# 八个历史真实失败断点：API查证回放

2026-09-15。**8条均已尝试一次：5条交付文档支持的API主方案，3条在Provider/API层中断而没有最终方案。** 不把中断算API能力失败，也不把五份方案说成全部解释正确或已通过Abaqus运行。

## 来源与上下文

这8个断点来自6个真实历史运行，涵盖虚构便利方法、构造器归属、几何/网格容器、梁截面与方向、面牵引。它们不是人工植入错误的fixture，也不是彼此家族独立的held-out。

编译过程只应用history中有成功回报的write_file/edit_file；失败编辑不生效。每次重建内容都与文件工具返回hash前缀核对，并与运行工具回报的stderr全文（规范换行后）匹配。保存完整history/error SHA、匹配消息位置、tool_call_id及重建脚本SHA；另外核对八个匹配点前均无未完成的目标文件写入、也未出现通用shell调用。

这证明的是**基于成功文件工具记录重建的历史断面**，不是完整文件系统checkpoint，也不是vendor实际读取的输入字节attestation。相同错误可能重复出现，使用的是history中匹配错误文本的首个断面，不声称单靠stderr文本就能唯一关联外部执行目录ID。

没有使用最终修复后的workspace。提供给新Coder的三个不可修改输入为historical_build.py、runtime_error.txt、original_task.json；脚本是待核实的错误尝试，不把所有旧模型决策冒充用户事实。原任务及已确认输入用于约束，但当前任务只查证接口，不能执行历史任务中的求解指令。

本批明确为**错误反馈后的查证**，不是从空白需求首次选择API；不能与前者混算。允许模型直接知道方法后查证，也允许搜索、选择替代或指出资料不足，不强制必须使用语义搜索。

## 八条结果

| 场景 | 交付 | 已观察到的主要结果 |
|---|---|---|
| BaseSolidBox长方体 | 有方案 | 用矩形草图＋BaseSolidExtrude保持100×10×10，拒绝无关BooleanMerge候选 |
| seedPartBySize全局播种 | Provider中断 | 两次搜索未返回直接Part.seedPart；没有最终方案，不评最终技术成败 |
| HomogeneousBeamSection | 有方案 | 选择BeamSection，核实profile/material键及网格前edge Set，区分方向独立赋值 |
| ConstrainedSketch.cut开孔 | Provider中断 | 查询了BaseShell及多个cut/trim候选，未交付；不能以调用次数代替解决问题 |
| PartInstance.Set | 有方案 | 识别四处相同owner错误，改为装配层构造，保留实例几何及坐标 |
| MeshNodeArray.findAt | 有方案 | 采用getByBoundingBox＋Assembly.Set，避开不确定的getClosest返回细节，不重划网格 |
| BeamSection.orientation | 有方案 | 去掉不支持的构造参数，使用Part.assignBeamSectionOrientation并核实profile字符串引用 |
| SurfaceTraction | Provider中断 | 已读取载荷、directionVector、follower和Assembly.Surface，尚无最终方案 |

五份方案都通过fresh文件检查，关键修复API有实际读卡证据；主助手认为主路径可作为下一步验证候选。参考路线组不是穷尽的正确答案，不能仅凭列中出现某个API就判方案合理。例如BaseSolidRevolve出现于参考候选组不代表单次旋转就能生成同样长方体，几何目标仍须人工核对。

原牵引断面已用两点directionVector，不能把它作为本题错误；实际还包含follow关键字及Set作为区域等待核查处。模型最后没有交付，不能倒用评审已知答案声称它完成了修复。

## 解释和引用仍有问题

原始计划未被修饰，主助手的逐例审查在本地review.json，不是外部人类专家签署：

- 梁截面方案把HomogeneousBeamSection解释为“旧命令被替代”，但本批资料没有证明这段版本历史。不存在/不适用的现场证据不等于弃用历史证据。
- 实例集合方案的主修复正确，但“前面的findAt已成功”不能证明单对象和序列都可被后面的Set接受；前文泛化与末尾承认容器未验证存在冲突。
- 节点选择方案合理选择了有明确数组返回说明的bbox方法，但getClosest中所谓numToChoose历史惯例未在本批资料出现，不能当作已核实冲突。bbox实际运行仍需检查选择数量和坐标。
- 部分计划把实际收到的runtime note ID放进card ID字段；还有Part.Set对照引用Assembly.Set卡的情况。真实来源存在，但引用粒度及直接读卡覆盖要区分，不能称全部引用格式合格。
- rectangle的官方2026返回描述和2024.10 stub存在差异；本例不使用返回值，因此不影响主几何路线，但不能宣称所有类型细节均已在2024确认。

这些不是“模型用了用户未说过的API”所造成的失败，而是查证后对证据范围的推理与表达问题。

## 发现工具的观察

共7次语义发现，分布于4个场景（长方体1、全局播种2、开孔3、梁方向1）。不能把7次调用当作7次成功发现：

- 长方体的搜索候选未提供直接替代路径，模型使用已精确读到的草图/挤出方案，并拒绝无关候选。
- 梁方向搜索返回Part方法、ODB方法和撤销方法；Part.assignBeamSectionOrientation位于第二，模型随后精确读卡并拒绝其它作用域。此前BeamSection runtime note也已提供方向线索，所以不作搜索单因果收益归因。
- 全局播种中，模型还尝试查询MeshPart类名，当前Exact接口不能作为方法目录；搜索又未返回直接入口。这是后续“按已知owner浏览方法”值得研究的实际线索，不用为单个错误维护改名表。
- 开孔与牵引的中断不能证明资料一定不足；尤其牵引已查到关键卡片，暴露的是“读到资料”与“完整交付”之间仍有断点。

## Provider与成本边界

三条记录均为ModelAPIError，没有最终模型输出；本驱动只保存了异常类型，无法进一步精确区分网络/超时原因或还原全部失败请求usage。不得把history缺失时的零条可见响应解释成零模型调用。

中断场景耗时分别约821.6、849.5、824.0秒。它们合计占八条累计活动时间约82%；这个比例是**最终中断的场景耗时占比**，不是精确HTTP等待占比。没有新增尝试、加预算或修改提示来替换它们。

五个返回会话的已记录usage为42次模型请求、输入683405/output48339 tokens、78次工具调用；不含三条中断场景未完整保存的usage，因此不是全批总用量。全批API计数54、trace68条，0次Abaqus/Solver，所有种子文件保持原样。累计活动时间约3025秒。

配置仍为同一产品Coder工厂＋评测专用发现入口、固定v2/E5、core Skill快照、Go/Flash enabled_high、Planning开、HTTP60秒/额外重试10、每题模型请求24/API计数8/wall900秒。普通CLI/default/profile/resume没有改变；这是单会话评测，不支持把这三个原型证据目录直接当作可续跑产品CodingRun。没有新物理正确性或实际Abaqus调用节省证据。

## 数据、脚本与复核

- [历史来源清单](historical_api_case_sources_v1.json)
- [编译后的8题包](historical_api_feedback_cases_v1.json)：包含代码断面、原任务、错误和逐题provenance，可运行时直接铺成只读工作区，不依赖最后的历史workspace。
- [断面提取器](extract_historical_api_context.py)、[题包构建器](build_historical_api_cases.py)
- [现有查证驱动](run_api_discovery_loop.py)现在可显式传入题包；旧6题仍为默认，不覆盖旧结果。

```powershell
uv run --no-sync python benchmarks/api_catalog/run_api_discovery_loop.py `
  --root var/audits/<新的批次目录> `
  --cases-file benchmarks/api_catalog/historical_api_feedback_cases_v1.json
```

本次原始证据在`var/audits/api-history-loop-20260915`：seal、Skill、逐题result/history/lookup、observations及review。原始断面提取证据另在`var/audits/history-api-context-20260915`。本轮题包SHA为`ef56bbb8d7099704e3541f26897d209ab740cf30514cd31a8a9fe2c10a125239`。

提取器测试确认不会应用失败编辑或错误后的最终修复，并验证返回hash不匹配时拒绝；另检查8题包hash与输入文件边界。连同发现→读卡/预算测试共3项通过，相关Ruff通过。没有修改产品源码、旧题/旧成绩、索引或锁文件，未运行新的持证测试。

## 当前结论

这次扩充揭示了真实接口发现、跨API配合与证据解释的问题，比继续增加普通方法问句更有价值。当前应停止继续扩题：先讨论现有CodingRun服务的正式发现接线/恢复，以及按owner查找方法的可用性；对已经形成合理方案的少数场景，只有另行授权的最小Abaqus验证才能判断是否真消除了动态绑定错误。不要把这一步的文档方案结果包装成仿真闭环通过。
