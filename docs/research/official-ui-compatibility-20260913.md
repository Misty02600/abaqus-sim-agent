# 官方 Pydantic UI 与 CodingRun 的兼容验证

2026-09-13。当次实验后的初步建议是复用官方 ai-chat-ui，做局部前端修改和 Python 宿主适配。已经证明官方页面不用改动就能承载一个实验性宿主问答/文件交付闭环；尚不能把原样 `agent.to_web()` 当作本项目的完整产品 UI。后续明确方案选择 FastAPI + assistant-ui，当前设计以 [ADR-0025](../adr/0025-local-web-workbench-over-coding-run.md) 为准；本文保留当次观察，不把这些结果描述成 assistant-ui 的运行证据。

本次没有安装 React/TypeScript/Vite 工程，没有构建前端，没有修改产品源码、`pyproject.toml` 或 `uv.lock`。FastAPI 未安装、未使用。临时 Starlette 0.47.3 仅安装在被忽略的 `var/official-ui-probe-20260913/python-deps`，使用已有 Uvicorn 启动两个 loopback 实验服务。两者已在实验结束后关闭。

## 证据与方法

- 实际框架为 Pydantic AI 2.33.0、Harness 0.24.0；页面为项目框架绑定的 ai-chat-ui 2.1.0。
- [上游 v2.1.0](https://github.com/pydantic/ai-chat-ui/tree/b9c6ae6d5a21b6a747099ae38934b99392317e2d) 对应 commit `b9c6ae6d5a21b6a747099ae38934b99392317e2d`。下载并保留源归档与 MIT LICENSE，未修改参考源码。
- 使用[官方已构建 offline HTML](https://cdn.jsdelivr.net/npm/@pydantic/ai-chat-ui@2.1.0/offline/index.html)，15,888,324 bytes，SHA-256 `18ad2ee902620e4c3e089a2979bc822612db31e0db48fde8e759aa78ce79f7f8`。两个服务的页面响应都重新核对了同一 hash；没有注入脚本、替换 DOM 或改动 JS。
- Playwright CLI 0.1.19 驱动本机 Edge，实际提交表单、刷新、使用独立浏览器会话、读取 DOM/IndexedDB 和截图。最初 Chrome 通道不可用，改用已有 Edge；未安装新浏览器。
- `native` 对照使用官方 `agent.to_web()` 和确定性 FunctionModel，产生原生 CallDeferred；`bridge` 使用未改的同一页面及实验 Python API，通过真实 CodingRunService、Harness 文件工具、submit_answer 和 ExecutionEvidenceVerifier 完成宿主调用。
- 两个 bridge run 使用 NoProcess，任何进程运行申请都会抛错，Abaqus/Solver budget 均为0。测试文件只写入实验 workspace。
- [实验驱动](../../benchmarks/coding/official_ui_probe.py)、[结构化结论与 hash 清单](../../benchmarks/coding/official_ui_probe_20260913/summary.json)、[原始 HTTP](../../benchmarks/coding/official_ui_probe_20260913/bridge-http.jsonl)和浏览器/宿主证据均保留。完整官方 bundle/source 留在 `var`；共享证据保存来源与摘要，不把整个前端依赖树加入本项目。

## 实测结果

| 检查 | 实际观察 | 判断 |
| --- | --- | --- |
| 原生 ask_user 显示 | 工具停在 `input-available`，页面显示 `ask_user Running`，问题需展开参数查看，没有输入答案的专用卡片。 | 原样交互不符合本项目待答语义。 |
| 普通聊天框回答 | 浏览器将 `1 mm` 作为 user 文本发出；框架 adapter 的 `deferred_tool_results` 为 None，没有按原问题 ID 形成 external tool result。 | approval 支持不能替代 ask_user 的外部答复。 |
| 会话与任务 ID | 同一 URL 刷新前后的请求 ID 从 `v9l2C4VziEhjr45Z` 变成 `0ll5cv708JfDUj0I`；URL ID 是另一个值。 | 不能直接把默认请求 ID 当作稳定 run ID。 |
| 宿主问答闭环 | bridge 实际写下 waiting_for_user；刷新页面，在此边界停止并重启后端，再回答，恢复同一个 `ui-8052e311e46f2ab2`，产物 fresh，宿主 completed。 | Python 适配可以保留现有宿主；不要求更换 Harness 或重写整个页面。 |
| 宿主拒绝模型的完成声明 | `ui-80b9f1e22b270d95` 中模型声明 MODEL_SAYS_COMPLETE，但 manifest 含 missing.txt；真实 verifier 拒绝，页面显示 HOST RESULT: failed 和缺文件原因。 | 可以由宿主生成最终展示文本，不能让 UI 自己依据模型话语判定。 |
| 重复答案 | 原样重放已成功的答复 HTTP，返回相同响应，产品 state 文件 hash 集合不变。 | 实验桥接的该条精确重放通过；不外推任意并发/崩溃 exactly-once。 |
| 同浏览器刷新 | 问题和聊天文字从 IndexedDB 恢复。 | 官方自带本地聊天持久化可复用。 |
| 独立浏览器打开原 URL | 页面聊天和侧栏为空，后端 run/outcome 仍在。没有请求宿主历史接口。 | 浏览器缓存不是产品任务历史；需要增加宿主读取/同步入口。 |
| 原生 file 和 data 消息 | IndexedDB 中已保存 `file`、`data-host-state` 两类 part，DOM 均未展示，PNG file URL 也未被请求。 | 缺渲染分支，不是传输丢失；结构化附件/宿主卡片需要局部修改。 |
| Markdown 图片 | 同响应中的 Markdown PNG 被实际加载，naturalWidth=120，页面显示图片和下载入口，控制台无错误。 | 第一版简单图片/链接展示无需先改前端。该 PNG 是合成协议夹具，不是 Abaqus 模型。 |

两个宿主 run 各3次 FunctionModel 请求、各一次实际 Harness write_file、各一个待答问题，累计6次宿主假模型请求、0次真实 Provider 请求、0次 Abaqus/Solver。native 对照另有两次脚本化假模型响应；第二次仍提问是夹具设定，不能据此声称真实模型会漏读答案。

成功样本只验证问答恢复和文件交付的 execution_evidence；失败样本是预期的缺交付物负例。它们不是两道建模题的成绩，不是物理验收或真实模型成功率。

## 接入时必须处理的具体位置

| 改动责任 | 官方源码位置 | 需要做什么 |
| --- | --- | --- |
| 稳定任务身份及命令语义 | [Chat.tsx](https://github.com/pydantic/ai-chat-ui/blob/b9c6ae6d5a21b6a747099ae38934b99392317e2d/src/Chat.tsx)、[useConversationIdFromUrl.tsx](https://github.com/pydantic/ai-chat-ui/blob/b9c6ae6d5a21b6a747099ae38934b99392317e2d/src/hooks/useConversationIdFromUrl.tsx) | 将后端 run ID、UI conversation 和请求 ID 明确绑定；把提交区分为新任务、按 question ID 回答及显式继续。 |
| 权威历史恢复 | [chat-db.ts](https://github.com/pydantic/ai-chat-ui/blob/b9c6ae6d5a21b6a747099ae38934b99392317e2d/src/lib/chat-db.ts)、[app-sidebar.tsx](https://github.com/pydantic/ai-chat-ui/blob/b9c6ae6d5a21b6a747099ae38934b99392317e2d/src/components/app-sidebar.tsx)、Chat.tsx | IndexedDB 可作为缓存；任务列表、当前问题和运行状态从宿主获取。浏览器消息数组不能覆盖宿主 history。 |
| 待答与宿主状态展示 | [Part.tsx](https://github.com/pydantic/ai-chat-ui/blob/b9c6ae6d5a21b6a747099ae38934b99392317e2d/src/Part.tsx)、[tool-part.tsx](https://github.com/pydantic/ai-chat-ui/blob/b9c6ae6d5a21b6a747099ae38934b99392317e2d/src/components/tool-part.tsx) | ask_user 显示“等待你的回答”，已答则显示已回答；增加宿主 scope/outcome 的独立展示。不能在宿主已完成后还把旧问题显示成 Running。 |
| 通用聊天操作与本项目合同对齐 | Chat.tsx、Part.tsx、[effort-select.tsx](https://github.com/pydantic/ai-chat-ui/blob/b9c6ae6d5a21b6a747099ae38934b99392317e2d/src/components/effort-select.tsx) | 官方 Retry/regenerate、编辑/分叉会改写浏览器历史；必须禁用或重接受控宿主命令。默认显示 Effort: Medium 也不能冒充本项目固定的有效配置。 |

这些位置是源码确认的修改范围，不是已编译和验收的 patch。可以继续保留整体布局、主题、滚动、文本/代码展示及通用工具卡片；当前没有证据要求重写整套聊天 UI。是否增加专门 file 分支取决于产品需要，简单 PNG 可以先用已实测的 Markdown 路线。

后端仍有必须自己实现的职责：共享 CLI/CodingRun 装配、run 绑定、答复校验与持久化、受控继续、只读宿主历史、产品事件到 UI stream 的投影、产物访问边界。实验只实现了足以验证闭环的部分，未实现完整 Web 服务。

一个额外框架陷阱已做离线核验：`VercelAIAdapter.dump_messages(..., sdk_version=7)` 将尚无返回值的 ask_user 投影为 `approval-requested`，见[实际投影](../../benchmarks/coding/official_ui_probe_20260913/pending-history-projection.json)。恢复时不能不加区分地把宿主 history 直接 dump 给页面，否则问题会变成批准/拒绝；需要按宿主 question contract 做显式投影。

## 实验桥接的限制

bridge 为保持官方 HTML 原样，临时用首条 user message ID 的摘要绑定 run，普通聊天框的下一条文本作为当前待答问题的答案。这只适合本次受控夹具：没有完整处理编辑、分叉、多标签、用户错误输入或任意客户端构造，不应晋升为产品身份/问答协议。

host outcome 与问题目前由 Python 生成普通文本发送，结构化 data chunk 在原页面不可见；宿主完成后旧 ask_user 卡片仍显示 Running。这证明“传得通”与“交互语义完整”不同，也给出了必要前端修改的直接依据。

未验证真实模型/Abaqus、运行中断连与进程取消、多任务、late-answer revision 的网页路径、浏览器 CSRF/完整文件服务安全、前端 fork 的编译兼容和发行包。隔离实验服务不能发布或作为产品命令使用。

## 当次实验后的初步建议（当前选型见 ADR-0025）

选择“官方 UI 局部定制 + Python 宿主适配”作为待采纳方向，撤回必须自建 React 页面/FastAPI/REST 轮询的结论。

使用已有官方 bundle 已经足够做功能原型，无须本项目拥有 Node 前端工程；要将上述任务语义修正为完整产品体验，仍需要少量 React/TypeScript 修改和构建。其成本集中在列出的交互边界，不能承诺完全零前端维护，也没有必要因此引入另一套前端框架。FastAPI 保持可选，本次 Starlette 已支持实验接入。

建议实施顺序为先共享宿主入口，再在官方 UI 上改稳定 run/question/history 接口，最后增加宿主结果卡片与必要的附件展示。来源、版本、许可证与本地修改需随 fork 记录。页面不追赶上游 main，先固定已经核对的版本。
