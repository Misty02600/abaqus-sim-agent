# ADR-0053：普通 CLI/Web 共用运行状态目录与读取入口

## 状态

已采纳，2026-10-08。用户确认统一普通 CLI/Web 的会话存储、发现和读取，并明确暂不增加专门评测页面。

## 背景

CLI/Web 已共用 CodingRunService、运行合同、history、恢复与状态锁，但默认状态分别保存在 runs 和 web/states。
Web 仅枚举自己的登记，且要求 web 前缀的 ID；因此已经完成的普通 CLI 会话不能直接在浏览器查看或追加消息。

## 决定

- 默认 Web 与普通 CLI 使用同一应用数据 runs 目录。Web 的 Thread、命令回执、ToolExecution、显示元数据和创建登记继续由 Web 管理根保存，不替代运行状态。
- Web 读取普通 CLI 会话时，直接使用原 request、launch、initial snapshot 和 state，不生成替代 registration、不复制或改写原运行合同。只接受当前普通产品协议；损坏或不兼容记录明确不可用。
- Web 新建会话仍保留首次执行前的创建登记，沿用幂等和调度失败恢复。登记内容不覆盖已经持久化的运行合同。
- 显式 Web --root 保持隔离目录语义。旧 web/states 状态保留原路径；新旧根出现同一 ID 时拒绝选择，不猜测合并，也不做 schema 迁移。
- 空闲状态可以切换入口；复用 OS 状态锁、原模型、权限、累计用量、freshness 和执行副作用协议。外部进程占用时只显示占用，不提供跨进程停止或 Solver 接管。
- CLI 的澄清、resume 和显式任务修订语义不变；Web 普通追加消息仍使用 continuation。本次不新增 CLI 聊天协议、通用会话导入或评测页面。
- 删除已接入的普通会话仍需完成受控执行收尾，并只清理已验证归属的 state 与 Web 元数据；工程文件保留。

## 替代方案与代价

让 CLI 仿造 Web registration 会重复保存启动合同，并使核心入口依赖 Web 记录格式；本次选择在读取时适配已有运行记录。
全量搬迁旧会话、改为统一服务器控制全部 CLI 进程或引入数据库索引，都超出当前读取和空闲交接需求。

代价是 Web 仍需区分创建登记与无登记的已保存运行，并保留旧目录定位。目录发现不等于任意历史 state 可恢复；高级显式 state-dir 也不会被全盘扫描。

## 关系

部分替代 [ADR-0025](0025-local-web-workbench-over-coding-run.md) 的 Web 独立登记与目录读取限制。
保持 [ADR-0027](0027-decouple-web-thread-messages-from-task-revisions.md) 的 Thread/执行所有权、
[ADR-0030](0030-drop-pre-release-coding-run-state-compatibility.md) 的开发期兼容边界，以及
[ADR-0035](0035-delete-web-conversation-records-and-keep-workspaces.md) 的删除与工程保留规则。

当前实现和限制见[系统框架](../architecture/system-framework.md#数据放在哪里)及[配置字段合同](../architecture/configuration-reference.md#宿主与凭据逐字段)。
