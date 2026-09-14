"""待显式组合根注入的文档证据端口；本模块不注册 Coder 工具。"""

from typing import Protocol

from abaqus_sim_agent.retrieval.evidence_contracts import EvidenceResult, SearchRequest


class DocumentSearchPort(Protocol):
    async def search(
        self, request: SearchRequest, *, search_id: str, deadline: float
    ) -> EvidenceResult:
        """执行一次有界查询。

        Args:
            request: 查询与不能自动放宽的宿主来源约束。
            search_id: 宿主分配的安全、稳定标识，不作为磁盘路径使用。
            deadline: 与 time.monotonic() 同基准的绝对截止时间。

        Note:
            预算预留、去重、run/revision 和后续补查由应用宿主拥有。
        """
        ...
