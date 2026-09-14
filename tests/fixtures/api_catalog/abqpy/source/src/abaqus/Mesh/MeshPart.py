from __future__ import annotations

import typing
from typing import Sequence, overload


class MeshBase:
    pass


class MeshPart(MeshBase):
    def __init__(self, name: str, /, *, enabled: bool = True) -> None:
        ...

    @overload
    @abaqus_method_doc
    def seedEdgeByNumber(
        self,
        edges: Sequence[Edge],
        number: int,
        constraint: SymbolicConstant = FREE,
    ) -> None:
        ...

    @typing.overload
    def seedEdgeByNumber(
        self,
        edges: EdgeArray,
        *,
        number: int,
    ) -> None:
        ...

    @abaqus_method_doc
    def seedEdgeByNumber(self, edges, number, constraint=FREE):
        ...


@abaqus_function_doc
def upgradeScript(
    fileNames: str,
    /,
    *searchPaths: str,
    preview: bool = True,
    **options: object,
) -> int:
    ...
