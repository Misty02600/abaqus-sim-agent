"""按名称选择模型工具；执行权限和报错后的自动提示由原宿主管理。"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness.filesystem import DEFAULT_TOOL_NAMES
from pydantic_ai_harness.shell import RUN_SCOPED_TOOL_NAMES

if TYPE_CHECKING:
    from .dependencies import CodingAgentDependencies

PROTOCOL_TOOLS = ("load_capability", "read_tool_result", "ask_user")
API_TOOLS = ("lookup_abaqus_api", "discover_abaqus_api")
RUNTIME_TOOLS = ("run_cae_script", "run_abaqus_python", "solver_job", "run_solver_job")
DOCUMENT_TOOLS = ("search_abaqus_docs", "record_document_assessment")
KNOWLEDGE_TOOLS = ("knowledge_list_directory", "knowledge_read_file")
TOOL_NAMES = (
    *DEFAULT_TOOL_NAMES,
    *RUN_SCOPED_TOOL_NAMES,
    *RUNTIME_TOOLS,
    *API_TOOLS,
    *DOCUMENT_TOOLS,
    *KNOWLEDGE_TOOLS,
    *PROTOCOL_TOOLS,
)


def validate_tool_names(names: tuple[str, ...]) -> tuple[str, ...]:
    if len(names) != len(set(names)):
        raise ValueError("tool names must be unique")
    unknown = set(names).difference(TOOL_NAMES)
    if unknown:
        raise ValueError(f"unknown tool names: {', '.join(sorted(unknown))}")
    return names


def resolve_tool_names(
    requested: tuple[str, ...] | None = None,
    excluded: tuple[str, ...] = (),
    *,
    shell: bool = True,
    runtime: bool = True,
    questions: bool = True,
    knowledge: bool = False,
    documents: bool = False,
    bounded_documents: bool = False,
) -> tuple[str, ...]:
    """确定本次实际工具集合；问答、能力加载和长结果读取不受功能工具开关影响。

    Args:
        requested: 显式允许的工具名；省略时使用当前可用工具的默认集合。
        excluded: 从允许集合中排除的工具名，排除优先。
        shell: 当前运行是否装配宿主Shell工具。
        runtime: 当前请求是否允许装配Abaqus执行工具；不授予执行权限。
        questions: 当前运行是否使用ask_user协议及后台Solver工具。
        knowledge: 是否绑定可供读取的知识包。
        documents: 是否装配文档查询工具。
        bounded_documents: 是否启用需要配套assessment工具的文档协议。

    Returns:
        顺序稳定的实际集合，供launch保存；恢复时直接使用保存值。

    Raises:
        ValueError: 名称重复、未知、所选工具缺少依赖或试图关闭协议工具。
    """
    protected: set[str] = set(PROTOCOL_TOOLS[:2])
    if questions:
        protected.add("ask_user")
    available = {*DEFAULT_TOOL_NAMES, *API_TOOLS, *protected}
    if shell:
        available.update(RUN_SCOPED_TOOL_NAMES)
    if runtime:
        available.update(("run_cae_script", "run_abaqus_python"))
        available.add("solver_job" if questions else "run_solver_job")
    if knowledge:
        available.update(KNOWLEDGE_TOOLS)
    if documents:
        available.add("search_abaqus_docs")
    if bounded_documents:
        available.add("record_document_assessment")
    validate_tool_names(excluded)
    if protected.intersection(excluded):
        raise ValueError("protocol tools cannot be excluded")
    if requested is None:
        selected = available - {"discover_abaqus_api"}
    else:
        selected = set(validate_tool_names(requested))
        unavailable = selected - available
        if unavailable:
            raise ValueError(
                f"tools unavailable in this run: {', '.join(sorted(unavailable))}"
            )
    selected = (selected - set(excluded)) | protected
    if "discover_abaqus_api" in selected and "lookup_abaqus_api" not in selected:
        raise ValueError("discover_abaqus_api requires lookup_abaqus_api")
    if bounded_documents and not set(DOCUMENT_TOOLS).issubset(selected):
        raise ValueError("bounded documents require search and assessment tools")
    return tuple(name for name in TOOL_NAMES if name in selected)


@dataclass
class SelectedTools(AbstractCapability["CodingAgentDependencies"]):
    names: tuple[str, ...]

    def get_instructions(self) -> str:
        return (
            "Active function tools for this session: " + ", ".join(self.names) + ". "
            "Workflow guidance applies only to tools present in this list. "
            "Report unavailable actions instead of requesting disabled tools. "
            "Output decisions remain available separately."
        )

    async def prepare_tools(
        self,
        ctx: RunContext["CodingAgentDependencies"],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        # 原生筛选同时作用于模型schema与可执行工具映射，不只隐藏描述。
        return [tool for tool in tool_defs if tool.name in self.names]
