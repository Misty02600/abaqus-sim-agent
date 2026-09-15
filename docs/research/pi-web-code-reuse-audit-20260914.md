# Pi Web 代码复用审计

日期：2026-09-14
状态：调研结论，尚未实施 Pi 代码迁移
关联文档：`docs/research/pi-ui-ux-reuse-implementation-guide-20260914.md`

## 1. 结论

当前 Web Workbench 不需要因为参考 Pi Web 而更换技术栈，也不适合把 Pi Web 整体嵌入当前项目。

推荐保持现有架构：

- 后端继续以现有 FastAPI `/api/v1` CodingRun 接口作为事实来源；
- 前端继续使用 React 19 + Vite + `@assistant-ui/react`；
- `WorkbenchRuntime` 继续把后端 run/message 状态投影到 assistant-ui runtime；
- Pi Web 作为第二层 UI/UX 参考和可追溯的组件来源，用于主题、图标、消息展示、文件浏览、侧栏和输入体验的增强。

这样可以获得 Pi 已经打磨过的交互质量，同时保留本项目最重要的语义边界：run 生命周期、用户澄清、task revision/amendment、command idempotency、外部执行、verifier、artifact freshness 等状态仍由宿主控制。UI 负责展示这些状态，而不重新定义它们。

从代码层面看，Pi 并不是“完全不能复用”。它有一批非常适合直接或轻改迁入的低耦合组件；真正不适合直接复用的是 `useAgentSession`、`ChatWindow`、`AppShell` 这一层会话运行时和应用壳，因为它们绑定了 Pi 自己的 session、model、tool preset、terminal、git、subagent、Next.js routing 等语义。

## 2. 审计范围和基线

Pi Web 本地参考快照：

- 仓库：`D:\Projects\abaqus-agent\references\pi-web`
- 上游：`https://github.com/agegr/pi-web.git`
- 审计提交：`135517b26c9f74a1a796569c3c8d2a9cf65b3b43`
- `package.json` 版本：`0.9.1`
- 框架：Next.js 16.3.1、React 19.2.4

当前 Web UI：

- 目录：`D:\Projects\abaqus-agent\abaqus-sim-agent-web\webui`
- React 19.2.3
- Vite 7.3.6
- `@assistant-ui/react` 0.15.19
- `@assistant-ui/react-markdown` 0.14.15

本报告关注“当前 UI 可以从 Pi 复用什么代码或实现方式”。更上层的交互原则、页面布局和阶段规划见已有 `pi-ui-ux-reuse-implementation-guide-20260914.md`，这里不重复展开。

## 3. 当前前端基础为什么保留

当前前端已经不是一套需要推倒重来的临时页面。

`Composer.tsx` 已使用 assistant-ui 的 `ComposerPrimitive`；`Conversation.tsx` 使用 `ThreadPrimitive` 和 Markdown renderer；`ConversationList.tsx` 使用 `ThreadListPrimitive`；`WorkbenchRuntime.tsx` 使用 `useExternalStoreRuntime` 把当前后端状态转换为 assistant-ui 可消费的 conversation/thread 状态。

现有 API 也已经形成明确的产品语义，包括：

- 创建和读取 run；
- `start`；
- 追加 `messages`；
- 回答 pending question 的 `answers`；
- `title` 重命名；
- `stop`；
- timeline/event；
- artifact 列表和 artifact 内容读取。

这层结构有两个价值。

第一，assistant-ui 已经承担通用聊天 UI 的 primitive/runtime 职责，因此无需再从 Pi 手工复制一套完整聊天运行时。

第二，本项目的真实状态比普通 chat session 更复杂。一个对话可能处于 waiting-for-user、running、interrupted、needs-reconcile、completed 等宿主状态，也可能发生 amendment 和 task revision。Pi 的 session runtime 没有义务理解这些本项目合同。如果强行换成 Pi 的 session 核心，就会把 UI 复用变成后端语义迁移。

因此当前架构边界继续采用：**host state is truth, UI is projection**。Pi 主要进入 presentation 层。

## 4. 许可证与复用边界

Pi Web 根 `LICENSE` 为 MIT：

- Copyright (c) 2026 agegr

Catppuccin 图标目录也有独立 MIT 许可证：

- Copyright (c) 2023 Catppuccin
- Copyright (c) 2023 thang-nm

所以法律层面允许复制、修改和再分发这些代码/资产，但直接复制或实质性改编时仍应保留 MIT 要求的 copyright 和 permission notice。

项目自身的复用要求还更严格：任何实际迁入的 Pi 代码、提示词或资产都应记录：

1. 上游仓库；
2. 基线提交；
3. 原始文件路径；
4. 本地目标文件；
5. 做了哪些修改；
6. 对应许可证。

当前 `references/pi-web` 只作为只读研究快照。产品构建和运行不能依赖该路径。真正采用的组件或资产必须 vendoring 到产品仓库内。

## 5. 代码复用矩阵

以下行数来自上述审计提交，只用于反映耦合量级；未来上游变化时需要重新核对。

| Pi 文件 | 规模/耦合观察 | 可复用等级 | 当前项目中的目标 | 主要注意点 |
| --- | --- | --- | --- | --- |
| `components/FileIcons.tsx` | 126 行，仅 1 个 React 类型 import | **接近直接复用** | 文件浏览器、artifact 列表、written-files | 需要同时 vendoring Catppuccin light/dark SVG/mask 资产并保留其 MIT notice |
| `components/ThinkingIcon.tsx` | 14 行，无 import | **直接复用** | thinking/running 状态小图标 | 迁入后按当前尺寸和无障碍语义调整即可 |
| `components/ThemeIcon.tsx` | 32 行，仅绑定 Pi 的 `ThemePreference` 类型 | **轻改复用** | 主题切换入口 | 将 Pi 类型换成本项目 theme union |
| `components/TurnWrittenFiles.tsx` | 52 行，4 个 import | **轻改复用** | 每轮已写文件/修改文件摘要 | 去掉 Pi i18n/path 类型；数据必须来自本项目真实成功 tool/event/artifact 状态 |
| `components/FrontmatterCard.tsx` | 70 行，2 个 import | **轻改复用，低优先级** | Markdown/文档预览 | 替换 Pi helper；当前 Abaqus UI 不是强需求 |
| `components/MermaidBlock.tsx` | 329 行，7 个 import | **部分复用** | Markdown artifact / assistant message 中 Mermaid | 与 Pi theme、i18n、clipboard、syntax-highlighter 有耦合，适合抽取渲染/复制/错误状态逻辑 |
| `components/FileExplorer.tsx` | 1054 行，7 个 import | **复用交互和局部算法** | 把当前 Artifacts 升级为文件树 | Pi 的 file index/git/search API 不能照搬；保留展开、选中、过滤、图标和树导航模式 |
| `components/FileViewer.tsx` | 1796 行，17 个 import | **复用渲染能力，重接数据源** | artifact 预览器 | 可参考 Markdown/code/image/PDF/diff 等 renderer；读取统一接当前 `/api/v1/runs/.../artifacts/...` |
| `components/MessageView.tsx` | 1842 行，17 个 import，内部 Pi 依赖多 | **复用展示模式** | 现有 `Conversation.tsx` 增强 | 抽取 thinking、tool result、duration、copy、diff、written-files 等视图；消息 runtime 继续由 assistant-ui 提供 |
| `components/ChatInput.tsx` | 2713 行，13 个 import，强依赖 session/model/skills/tool presets | **只复用 UX 模式** | 现有 `Composer.tsx` 渐进增强 | `@file`、图片、queue、slash command、model/thinking/tool selector 都必须逐项有后端合同才能接入 |
| `components/SessionSidebar.tsx` | 2344 行，13 个 import，绑定 session/project/git/i18n | **只复用 UX/算法** | 现有 conversation list | 可以借搜索、hover、running/unread、长列表；不能引入 Pi project/git/session-family 语义 |
| `components/ChatWindow.tsx` | 1958 行，25 个 import | **不整体复用** | 无 | 核心围绕 Pi `useAgentSession`，替换成本项目 API 的代价高于保留现有 assistant-ui runtime |
| `components/AppShell.tsx` | 2462 行，41 个 import | **不整体复用** | 无 | 混合 Next.js routing、terminal、branch、subagent、settings、session 等大量 Pi 产品语义 |
| Pi `useAgentSession` / session API | Pi 的运行时核心 | **明确不复用** | 无 | 会与 CodingRun、resume/revision、verifier、artifact/execution truth 冲突 |

### 5.1 可以真正“复制代码”的第一批

如果下一轮开始实施，最适合实际 vendoring 的是：

- `FileIcons.tsx`；
- Catppuccin file icon assets；
- `ThinkingIcon.tsx`；
- `ThemeIcon.tsx` 的视觉实现；
- `TurnWrittenFiles.tsx` 的小组件结构。

这批代码的共同特点是：它们主要接受 props 并渲染，没有把 Pi session runtime 带进来。迁移工作基本是类型、CSS token、数据 adapter 和 attribution。

### 5.2 适合“切片复用”的第二批

`FileViewer`、`FileExplorer`、`MessageView` 的价值很高，但不适合整文件复制。

例如 `FileViewer` 的 Markdown/code/image/PDF/diff 展示逻辑可以逐种 renderer 迁入，但“文件从哪里来、当前路径是什么、如何获取内容”必须换成当前 artifact API。

`MessageView` 也类似。Pi 对 tool call、thinking、written files、copy、diff 的展示很成熟，但本项目已有 assistant-ui `ThreadPrimitive`。正确做法是在现有 message component 中吸收 Pi 的 renderer/presentation，而不是再套一层 Pi message/session model。

### 5.3 只借设计的第三批

`ChatInput`、`SessionSidebar`、`ChatWindow`、`AppShell` 越往后越接近 Pi 的产品内核。

其中 `ChatInput` 仍值得长期参考，它包含：

- draft persistence / restored submission；
- 图片附件与压缩；
- `@file` autocomplete；
- 键盘菜单；
- queued steer/follow-up；
- slash command；
- capability warning；
- queue recall；
- abort/compaction；
- model/thinking/tool preset selector。

但这些并不是“把组件复制过来就有了”。例如 `@file` 需要后端暴露文件索引和可引用语义；图片需要请求合同和模型 capability；queue steer/follow-up 要定义与当前 running run 的关系；model selector 要遵循当前 provider/model amendment 规则。所以这部分应继续作为 UX backlog，而不是代码迁移清单。

## 6. CSS 和主题最值得先复用

Pi 的 `app/globals.css` 比当前 UI 的 `style.css` 更系统。最值得复用的是 token 和布局规则，而不是整份 CSS 覆盖。

Pi 已经定义了一组适合作为本项目设计 token 的变量，包括：

- `--bg`
- `--bg-panel`
- `--bg-hover`
- `--bg-selected`
- `--border`
- `--text`
- `--text-muted`
- `--text-dim`
- `--accent`
- `--accent-hover`
- `--user-bg`
- `--assistant-bg`
- `--tool-bg`
- `--bg-subtle`
- `--chat-content-max-width`
- `--chat-content-font-size`

Pi 还已经覆盖 light/dark/mist/rose/pine 主题、聊天正文宽度、Markdown、tool panel、file viewer、sidebar、mobile 和 View Transition 主题动画。

建议迁移方法是按功能拆分：

1. 先建立本项目自己的 theme tokens；
2. 把当前组件 CSS 改为消费 tokens；
3. 再迁移 Pi 中已验证的 spacing、hover、selected、message width、markdown/tool/file-viewer 规则；
4. 最后再决定是否引入多主题和 View Transition。

这样可以保持现有 DOM/assistant-ui primitive 不变，也不会因为复制整份 `globals.css` 引入 Next.js/Pi 专属选择器。

## 7. 推荐实施顺序：兼容性和难度

| 顺序 | 工作 | 与当前架构兼容性 | 难度 | 后端改动 |
| --- | --- | --- | --- | --- |
| 1 | theme tokens、字体、间距、chat max width | 很高 | 低 | 无 |
| 2 | `FileIcons` + Catppuccin assets + `ThinkingIcon` | 很高 | 低 | 无 |
| 3 | 改进现有 assistant-ui message/tool/thinking/written-files 展示 | 很高 | 低到中 | 通常无；written-files 只消费已有 host truth |
| 4 | sidebar 搜索、hover、running/unread、长列表交互 | 高 | 中 | 多数无，取决于 unread 定义 |
| 5 | 把 `Artifacts` 升级为完整 FileViewer/文件树 | 高 | 中 | 主要复用现有 artifact API；PDF/diff 等格式可能需要补 metadata/endpoint |
| 6 | Composer 的 `@file`、图片、快捷命令、queue 等 | 中 | 中到高 | 需要逐项定义真实后端合同 |

这也解释了为什么“参考 Pi”不等于“把 Pi 前端换进来”。前五类提升都可以在现有 React/Vite/assistant-ui 中完成，而且前三类能快速得到明显视觉和交互收益。

## 8. 当前不引入的 Pi 能力

本轮报告不把以下 Pi 能力纳入实现范围：

- Pi session runtime / `useAgentSession`；
- Next.js routing；
- 内置 terminal / PTY；
- branch/git project 管理；
- subagent UI；
- Pi 自己的 model/provider 管理；
- tool preset 系统；
- compaction/session tree 语义；
- Pi 的 server API。

这些能力未来只有在本项目自己出现对应产品需求和后端合同后才单独评估。不能因为 Pi UI 上存在一个控件，就把对应能力描述成当前 Abaqus Agent 已实现。

## 9. 实施时的代码组织建议

为了让“上游复用”和“本项目语义”保持清楚，后续迁移建议遵循以下组织方式：

- 低耦合视觉组件放入本项目常规 component 目录，不运行时引用 `references/pi-web`；
- 对直接/实质性改编的文件，在文件头或独立 third-party notice 中留下 Pi 来源和提交；
- Catppuccin 资产连同许可证文本一起 vendoring；
- `Conversation.tsx`、`Composer.tsx`、`WorkbenchRuntime.tsx` 保持为当前产品语义的 adapter/owner；
- `FileViewer` 等较大能力按 renderer 拆分，不创建一个复制自 Pi 的巨型兼容层；
- 所有“是否可操作”的 UI 状态继续来自后端 snapshot/capability，而不是前端根据消息文本猜测。

建议在真正发生第一次代码复制时新增一份精简的 third-party reuse manifest，至少记录：

| 字段 | 示例 |
| --- | --- |
| upstream | `https://github.com/agegr/pi-web` |
| baseline | `135517b26c9f74a1a796569c3c8d2a9cf65b3b43` |
| source | `components/FileIcons.tsx` |
| destination | 实际迁入路径 |
| reuse | copied / adapted / concept-only |
| modifications | 类型、CSS token、API adapter 等 |
| license | MIT |

## 10. 决策

下一阶段可以放心以当前 Web Workbench 为基础继续做 UI。Pi Web 对本项目最有价值的角色是成熟的代码与交互参考库，而不是替代前端框架或替代 CodingRun runtime。

优先实施顺序固定为：主题和布局 token → 图标/小组件 → 消息/tool/thinking/written-files → sidebar → FileViewer；Composer 的高级能力等后端合同成熟后逐项引入。

这个路径允许我们真正复用 Pi 的低耦合代码，也避免为了复用 UI 而改写已经成立的 FastAPI + assistant-ui + CodingRun 边界。
