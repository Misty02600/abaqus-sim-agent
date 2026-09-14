# ADR-0023：在纯编辑模式写入前执行调用方声明的范围合同

## 状态

已采纳为显式、默认关闭的窄 profile；2026-09-09 用户确认评估后的实现范围。

## 当时遇到了什么

知识缺口不等于运行时对象缺失，也不等于有权增加 fallback、helper 或新对象。Prompt 可以提醒模型，
但不能保证文件工具只修改用户授权的位置。已有 proposal-only AST 评测把范围检查与固定正确答案混合，
既不位于写入入口，也不能作为通用权限检查器。

## 最后决定

新增 `CodingRunRequest.edit_scope`，默认 `None`。CLI 只从可信调用方显式传入的
`--edit-scope-file` 加载 strict `ReferenceEditScope`；不从自然语言、模型 decision 或检索结果推导授权。
v1 只支持一个现有 UTF-8 Python 文件中的一个完整表达式，原文件必须可按 Python 3.10 grammar 解析。
调用方冻结路径、原文与原始 SHA-256，以及 LF 规范化原文上的半开字符区间。

- 写入前重建 `write_file`/`edit_file` 的完整候选。范围外文本（含注释）固定；允许 Harness 的平台换行
  转换，不宣称范围外原始 bytes 恒定。替换只能是 Name、Attribute 或字符串/整数常量下标组成的引用，
  禁止 Call、条件表达式、推导式和计算下标。检查器不含 Abaqus 对象名或固定任务答案。
- 通过原生 `wrap_tool_execute` hook 把检查、现有文件工具和检查点更新放在同一异步锁内；不另建工具循环、
  DSL renderer 或 publisher。普通 `before_tool_execute` guard 不足以覆盖并行写的检查后间隙。
- 纯编辑模式不注册三个 Abaqus runtime tools，关闭 Planning；除明确只读工具、Skill 加载、typed final
  decision 和受保护的两个文件工具外，其他工具执行一律拒绝，包括 `create_directory`。不允许新文件。
  文件工具只接受合同中的规范相对路径，拒绝路径 alias、symlink、junction 和 hardlink。
- `request.json` 继续拥有 immutable 合同。宿主在已有 OS run lock 内初始化或加载
  `edit_scope_state.json`，绑定合同 digest、最后已提交原始文件 hash 和 pending 状态。每次写前核对当前
  bytes；外部漂移不能通过重读或省略 `expected_hash` 接受。写入开始前先持久化 pending，成功核对完整
  候选后再 settle；检查点缺失、损坏、合同改变或未决写入均拒绝恢复，不猜测是否已写完。
- operational amendment 与 task revision 均保留原 edit scope；新 goal 不能扩权。改变文件、范围或基线
  需调用方重新审阅合同并新建 run，不覆盖旧 request/checkpoint。

## 为什么这样选

权限由调用方拥有，语义正确性由独立验证拥有。一个显式表达式位置既能阻止已观察到的扩大编辑，
又不把普通 Abaqus CodingRun 改造成受限语言。使用原文件工具保留已验证的文件接口与 Harness history；
检查点只补充原工具不拥有的 scope/hash 一致性。

## 没有采用的方案

- 继续只加 Prompt：无法让越界候选在落盘前被确定性拒绝。
- 全局 AST 限制或固定 `LEFT/nodeSets` 正确答案：前者改变普通 CodingRun，后者把任务 oracle 当权限。
- 任意表达式替换：一个参数内部也可以藏入函数调用，因此 v1 明确缩小可接受语法形式。
- 自动恢复 pending 写入或自动重绑当前文件：缺少可靠提交证据时会把外部漂移当成合法新基线。

## 带来的影响与边界

这是窄范围编辑防错接口，不是 OS sandbox、跨 run/workspace 的文件租约或数据库事务。异步锁仅串行化
同一 deps 的模型写入；现有 OS 锁仅独占同一 state。其他进程在检查与文件工具实际写入之间并发改文件仍
属于不支持的竞争，不能声称抗恶意外部写者。崩溃留下 pending 时需要人工审查并以新 run 重新授权，
没有自动 reconcile/解锁接口。

允许 Attribute/Subscript 不等于运行时无副作用：Python 可重载属性与下标。纯编辑模式不执行它们；
也不证明引用存在、Abaqus API 正确、物理模型正确或未解决的知识冲突已被正确处理。
普通 CLI 最终仍只做 `execution_evidence`，不能把 ready 或 completed 描述成这些语义验收。

## 落实与确认

- 合同与 guard：[`edit_scope.py`](../../src/abaqus_sim_agent/coding/edit_scope.py)
- 接线：`coding/contracts.py`、`agent.py`、`dependencies.py`、`instructions.py`、`service.py`、`cli.py`
- 测试：[`test_edit_scope.py`](../../tests/test_edit_scope.py)，覆盖真实 Harness 越界拒绝、合法编辑、并行
  stale hash、外部漂移、未决写入、合同冻结、纯编辑工具集合与 service 中断/恢复。
- 只有确定性测试；没有新增真实模型或 Abaqus 运行，不引用旧 Gold 为该 profile 的收益证据。

补充 [ADR-0008](0008-pydantic-ai-harness-abaqus-coding-run.md)、
[ADR-0018](0018-enforce-verified-abaqus-aci.md) 与
[ADR-0021](0021-use-amendments-and-task-revisions-for-continuation.md)，不替代其默认产品路径。
