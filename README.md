# Abaqus Sim Agent

面向 Abaqus 2024 的本地 AI 编程助手。用自然语言提出任务，让 Agent 在工程目录中编写和修改
Abaqus Python 脚本，按授权执行建模、求解和结果提取。支持命令行和本地 Web 工作台。

[快速开始](#快速开始) · [使用指南](docs/usage.md) · [架构](docs/architecture/overview.md) · [评测](benchmarks/README.md)

## 主要功能

- **脚本与仿真**：编写普通 Abaqus Python，分别调用 CAE、Solver 和 odbAccess；也可以只讨论问题或修改脚本。
- **交互与续跑**：遇到必要输入缺失时向用户提问，保存会话、文件和执行记录，支持后续继续任务。
- **本地工作台**：查看对话、工具调用、工作区文件和运行详情；支持文本、图片及 CAE 导出的 OBJ 几何预览。
- **API 查证**：内置 Abaqus API Catalog，按需查询接口说明；可显式接入本机私有知识包。
- **执行检查**：由宿主检查本次交付物、脚本语法和运行记录，保留日志与验收反馈。

底层使用 Pydantic AI Harness 的单一 Coding Agent，CLI 与 Web 共用运行服务。
项目仍在开发中，当前面向 Windows 本机单用户使用；执行检查通过不代表仿真结果已通过物理验收。
实际验证范围见[验证证据](docs/architecture/verification-evidence.md)。

## 快速开始

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 Node.js 22+（用于构建 Web 页面），
以及可用的模型账号或 API Key。执行仿真还需要本机 Abaqus 2024 和许可证，且 `abq2024` 命令可通过 PATH 找到。

在 PowerShell 中运行：

```powershell
git clone https://github.com/Misty02600/abaqus-sim-agent.git
cd abaqus-sim-agent
uv sync --locked --all-groups --extra web
uv run --no-sync python scripts/build_web.py
uv run --no-sync abaqus-sim-agent-web
```

服务就绪后会自动打开 <http://127.0.0.1:8765/>。首次使用：

1. 在“设置 → 模型账号”连接提供商，再从可选列表中选择模型。
2. 选择一个**已存在、路径只含 ASCII 字符**的工程目录，例如 `D:\abaqus-work\demo`。Agent 会在该目录中读写文件。
3. 输入任务要求。需要求解时，先在会话权限中允许 Solver；默认允许 Abaqus、禁止 Solver。

可以先尝试：“阅读工作区中的建模脚本，说明材料、载荷和边界条件，列出缺失信息，暂不运行仿真。”

### 命令行

仅使用 CLI 时无需 Node.js 或前端构建，安装依赖可用 `uv sync --locked --all-groups`。
先连接模型账号并查看可选型号，例如使用 DeepSeek：

```powershell
uv run abaqus-sim-agent --login deepseek
uv run abaqus-sim-agent --list-models
```

将下面的占位符替换为列表中的 DeepSeek 型号，并将工作区路径替换为已有目录：

```powershell
uv run abaqus-sim-agent `
  --model-provider deepseek `
  --model "<可选型号>" `
  --workspace D:\abaqus-work\demo `
  --goal "阅读工作区中的建模脚本，说明材料、载荷和边界条件，暂不运行仿真"
```

完整参数见 `uv run abaqus-sim-agent --help`；会话恢复、执行权限和 Phoenix 追踪见[使用指南](docs/usage.md)。

## 文档

| 想了解什么 | 入口 |
|---|---|
| 日常使用、会话恢复和可选功能 | [使用指南](docs/usage.md) |
| 系统如何运行、各组件负责什么 | [架构总览](docs/architecture/overview.md) |
| 模型、权限及配置项的具体语义 | [配置参考](docs/architecture/configuration-reference.md) |
| 测试、开发和发布流程 | [开发与评测](docs/development.md) · [前端开发](webui/README.md) |
| 仿真与检索评测的条件和结果 | [测评与证据](benchmarks/README.md) |
| 架构取舍和相关研究 | [架构决定](docs/adr/README.md) · [调研索引](docs/research/README.md) |

## 许可证

项目代码采用 [MIT 许可证](LICENSE)。第三方内容保留各自的许可与来源说明：
[前端复用](webui/THIRD_PARTY_REUSE.md)、[API Catalog](src/abaqus_sim_agent/api_catalog/data/THIRD_PARTY_NOTICES.md)。
Catalog 中第三方 Abaqus 文档内容的公开再分发许可尚未确认，不由项目的 MIT 许可证覆盖。
