# ADR-0026：按当前请求范围支持文字回答与局部执行

状态：已采纳（2026-09-14用户明确纠正产品范围）。扩展ADR-0019/0025的新普通会话完成语义；旧运行合同不变。

## 问题与决定

用户可以提出任何问题、讨论思路、请求一个局部步骤或请求完整流程。首页不得把完整建模需求写成准入条件。
完整板/杆任务是验收案例，不是产品允许的输入范围。澄清以当前请求的依赖为边界：只解释CPS4R不需要板厚；
只创建已给尺寸的几何不需要材料/载荷；真正请求分析却缺少必要物理输入时才在依赖动作前提问。局部动作本身
缺少必要事实时也应针对该事实澄清，不能猜值或要求补齐无关阶段。

仅修改首页文案不足：旧ReadyForVerificationDecision必须声明至少一个fresh文件，迫使纯解释生成无意义文件。
新普通CLI/Web会话采用launch v5，显式增加ResponseDecision；完整文字答案在summary交付，宿主保存
completion_kind=response、verification_scope=null，界面显示“已回答”。该路径不调用verifier，也不宣称事实、
文件执行或物理验证通过。宿主拒绝以文字完成替代当前revision中已记录的运行或终态工作区改动的验收。

文件/执行请求仍用原非空manifest与独立宿主verifier；只验证所用阶段，不自动补建模、求解或提取。没有放宽
ReadyForVerificationDecision的空列表校验，没有新增Agent、自动意图路由器或强制问题分类表单。模型按请求
选择回答/文件交付路径，语义适切性仍需行为验证，不能把结构合同通过等同完整自然语言验收。

launch v1–v4、固定Gold及显式legacy profile不自动获得新输出工具。ask_user与resume v5、OS锁、累计预算、
未决runtime reconcile及三工具权限保持原语义。本版仍没有前端自由追加消息/任务修订按钮，不能将单请求范围
放开描述成完整多轮聊天产品已经完成。

## 落实与验证

实现由coding/contracts、agent、instructions、service、launch及共用composition拥有；Web只投影和展示。
`tests/test_open_requests.py`覆盖纯答复零文件/零Abaqus、重新读取、只交付一个脚本、写文件后误用答复路径
被拒绝，以及旧/新输出schema差异；旧CLI/问答/执行验收回归一并运行。采用FunctionModel验证接线，
不声称真实模型必然正确澄清或按范围行动。旧Web验收proposal的源码绑定已过期；真实批次需重新封存。

最终离线回归1084通过、20项本机夹具缺失跳过、27项持证未运行；类型/前端构建通过。真实Edge页面用FunctionModel验证纯问答0文件/0Abaqus、completion_kind=response且不显示执行通过，控制台0错误。记录在var/audits/web-implementation/open-request-validation.json；不是新真实Provider行为成绩。

2026-09-14后续用户要求优先补齐基础多轮对话。Web已接入原有task revision以追加消息并保留同一会话历史，上文“本版仍没有”描述的是该ADR最初实施时点。当前支持范围及保存/执行语义见architecture/overview的“同一会话中的追加消息”，没有改动文件/执行验收决定。
