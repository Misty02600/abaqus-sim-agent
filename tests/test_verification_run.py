from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType
from typing import Protocol, cast

import pytest

from abaqus_sim_agent.verification.assets import plate_tension_oracle_script
from abaqus_sim_agent.verification.run import _build_oracle_request


class _SelectAnalysisStep(Protocol):
    def __call__(
        self,
        steps: dict[str, object],
        requested_name: str | None = None,
    ) -> tuple[str, object]: ...


def _load_step_selector(monkeypatch: pytest.MonkeyPatch) -> _SelectAnalysisStep:
    odb_access = ModuleType("odbAccess")
    odb_access.openOdb = None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "odbAccess", odb_access)
    namespace = runpy.run_path(str(plate_tension_oracle_script()))
    return cast(_SelectAnalysisStep, namespace["_select_analysis_step"])


def test_oracle_request_does_not_disclose_a_hidden_step_name(tmp_path: Path) -> None:
    odb_path = tmp_path / "plate_tension.odb"

    request = _build_oracle_request(odb_path)

    assert request == {"odb_path": str(odb_path)}
    assert "step_name" not in request


def test_oracle_accepts_the_only_analysis_step_regardless_of_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_step = _load_step_selector(monkeypatch)
    arbitrary_step = object()

    name, selected = select_step({"Any-Legal-Step-Name": arbitrary_step})

    assert name == "Any-Legal-Step-Name"
    assert selected is arbitrary_step


def test_oracle_rejects_ambiguous_multiple_analysis_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    select_step = _load_step_selector(monkeypatch)

    with pytest.raises(
        RuntimeError,
        match=r"requires exactly one analysis step.*found 2",
    ):
        select_step({"Loading": object(), "Unloading": object()})
