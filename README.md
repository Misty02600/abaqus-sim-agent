# Abaqus Sim Agent

可恢复的 Abaqus 多 Agent 仿真系统。项目使用 LangGraph 编排专业 Agent，重点是把长任务状态管理、结构化协作、人工审批、外部副作用幂等、故障修复和分层验证真正做通，并能准确说明每项设计的行为、边界和已知局限。

当前仓库处于工程地基阶段：已经建立独立仓库、核心领域契约、面向可序列化设计的 Graph State 骨架、
执行器协议和可测试的幂等 Mock 执行器。LLM Agent、LangGraph 父图/子图、Abaqus 真实执行器和评测集
尚未实现，README 不把这些目标描述成已完成功能。

项目明确面向秋招：主目标是 AI Agent / LLM 应用工程岗位，其次是 Python AI 后端 / AI 平台岗位，Abaqus/有限元提供 AI for Science 的领域场景。技术路线会有意识地覆盖 SQL 与事务、REST/SSE、长任务状态机、幂等与恢复、Agent 工具权限、结构化生成、评测和容器化交付，但不为简历关键词拼接无关的电商、旅游或 Text2SQL 功能。三类常见项目与本项目的能力映射见[技术栈与工程讲解点](docs/architecture/technology-stack.md#秋招岗位定位与能力映射)。

## 已确定的系统边界

- 多 Agent 是必要能力，专业 LLM 角色至少包括 Supervisor、Interpreter、Architect、Input Writer、Reviewer 和 Verifier；Runner/Job Service 是确定性执行角色，不授予 LLM 任意求解器控制权。
- 整体采用模块化单体控制面：SQLite 阶段 FastAPI 与 Coordinator 同进程，迁移 PostgreSQL 后才拆成同一代码包的两个运行入口；不把 Agent 拆成微服务。
- LLM Agent 负责判断、建模设计和诊断；确定性工具负责校验、文件操作、Job 提交和结果提取。
- Graph State、Runtime Context 和 Artifact Store 分离。
- Mock 与真实 Abaqus 执行器实现同一协议，使上层工作流测试不依赖 Abaqus 许可证；当前 Mock 只验证基础幂等、状态和取消，持久恢复与故障对账仍待实现。
- 本仓库不继承 AbaqusAgent 的 Git 历史，但允许在记录来源和许可证的前提下，吸收、重构或复用其已验证的业务设计与实现；当前取舍见[上游源码设计分析](docs/scratch/abaqusagent-source-design-analysis.md)。

先从[架构文档入口](docs/architecture/README.md)建立全局印象；已经确定的分层、运行拓扑、状态所有权、
API、部署与测试边界见[完整系统框架](docs/architecture/system-framework.md)，参考项目逐角色对照及首个
完整版本真正实现到哪里见[Agent 与实现范围](docs/architecture/agent-implementation-scope.md)，父图和 Job 状态机分别见
[一次仿真 Run 的工作流](docs/architecture/flows/simulation-run.md)与
[Abaqus Job 生命周期](docs/architecture/flows/job-lifecycle.md)。当前技术选择、实现状态和可讲清的工程问题见
[技术栈与工程讲解点](docs/architecture/technology-stack.md)，长期取舍见[架构决定](docs/adr/README.md)。

## 开发

```console
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run basedpyright
```

Windows 下运行 Just recipes 需要 PowerShell 7.4 或更高版本，并通过 `pwsh` 命令调用；
直接运行上述 uv 命令不受此要求影响。

## 更新模板

在干净的工作树中运行：

```console
just update-template
```

该命令让官方 Copier 从 answers 记录的来源按默认规则选择版本并合并模板更新，再调用 `just finish-template-update` 检查 diff 与
冲突产物、刷新本地 lock，并运行 hooks、Ruff、BasedPyright 和 pytest。如果 Copier
报告冲突，先解决冲突并删除所有 `.rej`，再运行 `just finish-template-update` 继续；
不要重新启动要求干净工作树的 `just update-template`。默认更新会保留 answers 中已有的
profile 和身份。Application 如需调整部署 Python 次版本，可以运行
`copier update --skip-answered --data python_version=3.13`，再运行 `just finish-template-update`。
模板更新不支持修改 `project_kind`、`repo_owner`、`repo_name` 或 `module_name` 等项目 profile
与身份答案；这些变化应作为项目迁移单独处理。

模板只提供新项目的初始依赖结构和兼容范围。项目生成后，例行依赖版本更新由本仓库自己的
Renovate PEP 621 manager 维护；active Prek hook 和使用可读版本 tag 的 GitHub Action 也由本仓库
Renovate 通过显式更新维护。Copier 只在模板配置兼容性确实要求时改变依赖结构、范围或自动化结构。仓库中的配置不会自行
安装 Renovate App，首次维护前必须确认 Hosted Renovate 已实际启用。

workflow 使用精确 uv 版本以固定 CI 工具环境。uv patch PR 通过必需检查后可以自动合并；
minor/major PR 不自动合并，维护者必须协调相关 lock、build backend 与测试后再人工合并。


Renovate 生成的 Copier 模板更新 PR 不会额外执行 `uv lock`。请在该 PR 分支运行
`just finish-template-update`，并把发生变化的 `uv.lock` 随 PR 一起提交后再合并。



## 发布

待发布的源码通过 CI 后，在 `main` 分支运行：

```console
just bump
```

Commitizen 会创建版本提交和 annotated tag，Just 会将当前提交及其可达的 annotated tags
整体原子推送到 `origin`，由版本 tag 触发远端 release workflow。

Release 先在只有仓库只读权限的 job 中校验 tag、按本 profile 的 lock 策略安装依赖、重新运行 Ruff、
BasedPyright 和 pytest，再构建一个 wheel 与一个 sdist，并检查归档数量、完整性与安全路径。通过检查的
发行包和 release notes 作为同一份 artifact 传给后续最小权限 jobs；发布 job 不重新 checkout、构建或
执行发行包。


本项目是使用 Python 3.12 的部署型应用；`.python-version`、项目元数据
和 BasedPyright 使用同一次版本。仓库提交 `uv.lock`，以固定开发、CI 和部署所使用
的依赖集合。CI 使用独立的 Ruff、BasedPyright 和单版本 pytest jobs。tag 触发的
workflow 会把检查后的 Python 发行包附加到 GitHub Release，但不会申请 PyPI OIDC 或发布 PyPI；
这些附件不是通用部署产物，容器、云平台或进程部署方式仍由具体项目定义。
