# 官方 UI、ExternalStore 与 AG-UI 的同组接线比较

实验日期2026-09-13，整理完成于2026-09-14。用户授权实际比较，并明确要求解释为什么不继续使用Pydantic官方UI。本次完成了三条隔离接线、构建、浏览器和宿主合同检查。

**结论：三条都能满足本次接线场景。继续采用assistant-ui ExternalStore主线的理由是维护边界更符合当前任务工作台，不是官方UI做不到。** 官方最小定制版是有效备选；AG-UI也已经实际跑通，但在本次不逐token输出、宿主拥有状态的范围内没有减少共用状态读取和同步。

本报告只覆盖确定性接线资格，不是正式产品、物理仿真、自然模型稳定性或性能比较。项目`src`、`pyproject.toml`和`uv.lock`未修改，没有真实Provider或Abaqus调用。隔离服务及比较浏览器均已关闭。

## 比较方法与公平性

三条路线共用[实验宿主](../../benchmarks/coding/ui_comparison_probe.py)：真实CodingRunService、Harness、submit_answer、ExecutionEvidenceVerifier，配FunctionModel和拒绝所有进程调用的NoProcess。模型/Abaqus工具定义不由前端注册；Abaqus/Solver预算为0。REST与AG-UI命令委托同一调度函数，同一文件交付夹具。

| 路线 | 实际实现 |
|---|---|
| 官方最小定制 | 从ai-chat-ui 2.1.0固定commit复制工作副本，保留整体布局、主题、文本/工具渲染；将原会话连接逻辑替换为宿主任务API与只读消息展示。不是原样to_web，也没有保留其完整默认聊天生命周期。 |
| assistant-ui ExternalStore | 使用0.15.19公开外部状态接口，直接映射宿主快照；项目任务表单POST原问题答案，工具只渲染。 |
| assistant-ui AG-UI | 使用react-ag-ui/client 0.0.59的真实HttpAgent和runtime，开始/答复走标准RunAgentInput与resume；使用原生interrupt hooks，未偷偷调用REST答案接口。 |

两种assistant-ui模式共用同一页面组件，区别集中在runtime adapter。所有页面共用任务列表/快照来源；AG-UI模式仍保留同样的GET/SSE宿主状态读取，以支持其他浏览器答复后的更新及重新打开页面。这说明当前接法可行，不证明所有AG-UI设计都必须保留两套通知或当前适配是最小实现。

三条都禁用未实现的重试/编辑/分叉/取消操作，任务身份由宿主生成。所有会话恢复都从宿主取数据，浏览器history/state不会覆盖宿主记录。官方最小定制版修正了原样页面的ask_user Running问题，已回答则显示Answered。

## 最终检查结果

最终共用夹具固定后，每条路线运行PASS、FAIL、SLOW、RESTART四个任务，共12个。六类交互场景分布在这些任务内，不能称为12次自然模型成功率评测。

| 检查 | 官方最小定制 | ExternalStore | AG-UI |
|---|---|---|---|
| 新任务→ask_user→答案→文件交付 | 通过 | 通过 | 通过，原生resume |
| 待答刷新与独立浏览器打开同任务 | 通过 | 通过 | 通过，含interrupt重建 |
| 模型声明完成但交付物缺失 | 正确显示宿主失败 | 正确显示宿主失败 | 正确显示宿主失败 |
| 已接收答案后离开页面，后台继续 | 通过 | 通过 | 通过，连接结束不取消共用worker |
| 重放答案/冲突答案/禁止操作 | 相同请求不重跑；冲突409 | 相同请求不重跑；冲突409 | 相同resume不重跑；冲突409 |
| 三任务同时处于待答，重启后端再回答 | 原问题同run完成 | 原问题同run完成 | 原interrupt同run完成 |

每个最终任务恰好3次FunctionModel请求、一次实际Harness write_file、一个问题；合计36次假模型请求、0次真实Provider请求、0次Abaqus/Solver。9个任务按预期completed，3个缺交付物负例按预期failed。最终12份工具schema摘要一致，包含原三个Abaqus工具和ask_user，没有run_command或新增前端模型工具。

前端静态验证：官方fork的TypeScript/Vite构建与改动范围ESLint通过；assistant-ui两模式typecheck/build通过，9项实际hook/原生HttpAgent解析的headless检查通过。浏览器检查捕获的最终pageerror为空；早期静态资源MIME和favicon问题在共用测试服务中修复，不能记为某个UI的缺陷。大型bundle警告保留，不以bundle大小排名；官方保留了更多Markdown/代码高亮等功能，assistant-ui夹具只覆盖普通文本和工具，二者不是等功能性能基准。

## 官方 UI 为什么仍可用、为什么目前不作为主线

它的实际优势是已经提供了完整的页面、主题、滚动、文本和工具详情。原样页面此前也已验证Markdown图片能显示。因此，“官方只定位调试”不能作为否定定制的充分理由。

此次为了满足同一宿主合同，实际修改了7个上游文件，新增2个宿主组件文件，package与原pnpm锁保持不变：

- `App.tsx`：接宿主任务/状态，组合结果面板。
- `Chat.tsx`：用宿主消息作为只读输入，替换原useChat发送、浏览器会话持久化与编辑/重试等连接逻辑；仍复用官方消息和工具渲染。
- `Part.tsx`：只读显示，移除没有宿主语义的动作。
- `components/app-sidebar.tsx`：从宿主读取任务列表。
- `components/tool-part.tsx`、`components/ai-elements/tool.tsx`：按问题状态显示Waiting for answer/Answered。
- `vite.config.ts`：本地静态资源路径。
- 新增`host-api.ts`与`components/host-panel.tsx`：API/事件连接、问答表单和宿主结果。

这是一条可维护的fork路线，但它已涉及应用核心交互，而不仅是改Logo或增加一个面板。未来升级官方应用时，需要核对这些文件与其会话行为的变化。升级冲突并未在本次实际测量，所以不能把这个维护风险写成一定更高的工时。

ExternalStore路线没有修改assistant-ui包内源码，任务语义放在项目自己的adapter/组件里。它的作用域更符合我们要拥有任务工作台、又复用通用聊天组件的目标。因此保留其为主线，是维护结构上的选择；本次没有证明它功能更强、开发更快或总成本更低。

如果产品目标进一步收窄为尽快得到一个现成聊天界面，官方定制版依然值得采用。当前不选它，不等于需要把它永久排除，也不影响继续使用Pydantic AI/Harness作为Agent内核。

## ExternalStore 与 AG-UI 的实际差别

- ExternalStore adapter直接接宿主snapshot.messages和状态，命令使用明确的start/answer接口；不提供通用聊天编辑/重新生成处理。
- AG-UI实际接通RUN_STARTED、MESSAGES_SNAPSHOT、STATE_SNAPSHOT、RUN_FINISHED interrupt及resume。原问题答案先经共用宿主校验/保存，再进入原生DeferredToolResults；不是调用默认审批适配器绕过question ledger。
- AG-UI新增history投影：将宿主待答映射为requires-action及`custom.agui.interrupts`元数据，通过library history/import机制恢复原interrupt。该约定与检查过的0.0.59实现关联，升级要回归。
- AG-UI的文字/工具消息由现成runtime重建，但宿主结果、跨页更新和独立浏览器重开仍使用共用快照。此处未评估其完整在途stream replay、动态生成UI或逐token收益，不据本次结果否定那些能力。

所以当前保留ExternalStore + 轻SSE；当真正需要丰富流式交互或标准协议互操作时，AG-UI已是有可复用实测基础的升级方向，不必更换整套页面。

## 保留的失败与共用宿主问题

全部21个开发/最终宿主记录和57次FunctionModel调用保留；最终比较子集是上述12个/36次，其他记录包括API预检查、旧夹具通过记录、未启动/待答开发记录和两个中断样本，不删除或回填。

`cmp-1539ce1084ae4d6d`（官方页面）与`cmp-59119c0eaffe4b64`（ExternalStore页面）都在共用夹具并发读取产品状态时遇到resume_state原子replace的WinError5。独立检查复现了本机普通读句柄阻止replace；仅增加FILE_SHARE_DELETE的尝试也仍失败，不能宣称该标志已解决问题或断言每次失败的全部底层原因。

最终夹具在活动段只返回最后一次settled展示快照及running标记，结束后再读取产品文件；单独断言了活动快照不调用文件读取。三条最终比较均使用此相同版本。代价是活动段显示的是上一份持久证据，不能把它称为每个内部步骤实时更新。正式API需要可控的观察/发布边界，这一问题已转入PLAN-0009。

浏览器测试脚本初期也修正了Playwright沙箱没有全局URL、创建后整页跳转导致response body不可读等测试问题。它们不涉及模型/Abaqus，也不属于某UI的失败结论。

## 证据、复现与边界

- [完整结构化摘要与hash](../../benchmarks/coding/ui_comparison_20260913/comparison-summary.json)：最终run ID、工具schema、版本、失败记录和全部归档摘要。
- [官方fork差异](../../benchmarks/coding/ui_comparison_20260913/official-changes.patch)、[来源与修改说明](../../benchmarks/coding/ui_comparison_20260913/official-notes.md)：基线commit `b9c6ae6d5a21b6a747099ae38934b99392317e2d`，上游MIT LICENSE随归档保留。没有修改原始研究快照。
- [assistant-ui接线说明](../../benchmarks/coding/ui_comparison_20260913/aui-notes.md)、[9项headless结果](../../benchmarks/coding/ui_comparison_20260913/aui/validation-results.json)。
- [官方页面截图](../../benchmarks/coding/ui_comparison_20260913/screenshots/ui-comparison-official-failed.png)、[ExternalStore截图](../../benchmarks/coding/ui_comparison_20260913/screenshots/ui-comparison-external-failed.png)、[AG-UI截图](../../benchmarks/coding/ui_comparison_20260913/screenshots/ui-comparison-agui-failed.png)。
- [实际浏览器比较脚本](../../benchmarks/coding/ui_comparison_20260913/browser-matrix.js)、[HTTP/宿主记录](../../benchmarks/coding/ui_comparison_20260913/requests.jsonl)与各run的request/launch/history/question/outcome均封存。

执行时宿主脚本另存server-executed.py；交付实验驱动仅整理导入/格式，非导入AST相同，Ruff通过，前后hash均记录。前端source/config/锁和official修改文件已归档，不归档node_modules或dist来冒充可复现源码；恢复实验应先核对宿主源码/锁hash，并按各notes重建，不能直接套到已改变的产品版本。

本轮不证明完整生产API的幂等/安全/多进程恢复、晚答revision网页全路径、真实Abaqus运行日志卡片、ODB预览或物理验收。实际工具调用只包括ask_user与write_file，三个Abaqus工具只验证了schema保持和预算为0。两种assistant-ui模式并未补齐官方页面所有富文本能力，不能做整体视觉成熟度排名。

PLAN-0008的有界接线资格和比较完成，后继PLAN-0009/0010仍未实施。原先9–15工程日粗估没有因此获得工时验证。
