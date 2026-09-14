# 回答证据A/B v2：取得一组完整对照，输出稳定性仍有限

只修正实验输出协议后，原六份证据包在新目录重新运行。两臂的任务、初始文件、证据内容、模型、参数、预算和执行顺序不变；共同格式指令与schema改成五个平面必需字段。

## 协议修正

- 顶层字段为summary、findings、next_action、evidence_refs、proposed_script；去掉analysis嵌套，保留原字段类型、10条findings/20条引用上限和严格extra-forbid。
- 缺字段仍失败，不放宽解析。只有通过schema的新结果才机械重组为analysis.json形式，字段内容不变。
- 没有修改B证据的选段、增加“非穷尽”等元信息，或把上一批无效答案修补后使用。
- 新增16项协议/真实Pydantic AI本地模拟测试，加3项原始响应记录测试，共19 passed；新协议模块BasedPyright及Ruff检查通过。

## 六次模型调用

| 任务/组 | 基础schema | 任务对应的输出形状 | 内容/提议修改观察 |
|---|---|---|---|
| API-A | 有效 | 有效 | 添加辅助变量、try/except、实例遍历和集合创建回退，超出只改region表达式的要求 |
| API-B | 有效 | 有效 | AST核对只有指定region表达式改变 |
| Riks-A | 有效 | 有效 | proposed_script为JSON null |
| Riks-B | 有效 | 不符合指令 | proposed_script是字符串`"null"`，不是JSON null |
| warning-A | 有效 | 不符合指令 | 同样返回字符串`"null"` |
| warning-B | 无效 | 无效 | JSON字符串引号转义错误，原始响应保留 |

因此是**5/6基础schema有效、3/6符合任务输出形状、1组完整可用配对（API）**。这些不是任务成功率；API-A虽然输出形状有效，提议的修改仍不合规。没有把字符串null自动转成空值，也没有修复warning-B的JSON或追加调用。

## API配对给出的信息

A明确把“参考目录未检索到OdbAssembly.nodeSets”作为增加回退逻辑的理由：从实例集合中收集节点，尝试构造新集合，再传入getSubset。**资料未找到并不表示题目已给出的装配层LEFT不存在，也不授权扩大修改。** A同时声称只修改入参，但实际提议脚本增加多段逻辑。脚本没有被执行；没有对新加的vendor调用是否有效作运行验收。

B得到rootAssembly、nodeSets及集合重载的直接类型资料后，提议了`field.getSubset(region=odb.rootAssembly.nodeSets['LEFT'])`，其余语句与原脚本AST一致。该单组观察支持“直接API依据可能帮助减少无依据回退与越界方案”，但不能估计稳定效果。

B仍将所提供的两种region重载表述为参数类型要求，容易被理解为完整合法类型清单。B证据包本身也没有明确说明只摘录部分重载，因此这不是纯粹由模型造成的确定性错误证据。API-A所复述的`region: FieldOutput`同样来自其卡片内容，不能一概叫作凭空编造。

## 另外两组仅作分层观察

- Riks两组analysis字段齐全。B明确解释弧长、dead/reference load和时间/速率限制；A主要依据较短的比例加载、载荷列表和材料片段。两组都没有完全重复最早历史答案的“必须均为力型载荷”前提。B的字符串null问题使完整输出协议不合格，不能把这组记成完整任务通过；内容仍可作为有明确限制的观察。
- warning-A依然没有保留完整的最小增量/过程例外，还将若干终止消息概括为明确的收敛失败证据，存在超出所给资料的概括风险。warning-B原始文本包含E1中的过程例外，但JSON无效，不能作为正式完整答案通过。

本轮没有使用后来评审核对的完整资料替模型补引用，也没有用v2某个B答案与旧历史A答案单独比较来宣称改善。

## 当前判断

格式封装比上一轮更易被遵守，但仍未完全稳定：一个通用nullable-string字段允许字面字符串null通过类型校验，而任务指令要求的是JSON null；另有一条工具调用JSON转义失败。基础schema成功与任务输出正确仍应分别验证。

对最初问题，目前得到的是有限但具体的证据：**API这一例中，补齐直接依据后提议修改更符合原任务；同时，完整证据包和更好格式都不能自动保证模型不扩大结论。** 不能据这一组宣称B普遍更好，也不能将其余格式失败归因于RAG知识质量。

六次模型请求均只尝试一次；没有生成式重试、没有运行Abaqus、没有实际修改原任务脚本或模型。输入/输出用量及各项错误详见JSON报告，费用字段不可用。旧实验与旧评分全部保留。

## 证据

- 协议实现：`src/abaqus_sim_agent/evaluation/answer_protocol.py`。
- 测试：`tests/test_answer_protocol.py`。
- 一次性v2运行器：`var/audits/run_answer_evidence_ab_v2.py`。
- 冻结输入、实际ModelResponse、平面输出和机械投影后的答案：`var/audits/answer-evidence-ab-v2/`。
- 机器报告：[answer_evidence_ab_v2_20260909.json](answer_evidence_ab_v2_20260909.json)。
