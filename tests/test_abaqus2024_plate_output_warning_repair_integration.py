from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from abaqus_sim_agent.evaluation.plate_output_warning_repair import (
    _run_reference,
    derive_defective_seed,
)
from abaqus_sim_agent.evaluation.verified_plate_seed import load_verified_plate_seed

pytestmark = pytest.mark.abaqus


@pytest.mark.skipif(
    os.environ.get("ABAQUS_LICENSED_TESTS") != "1",
    reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
)
def test_rf3_defective_seed_reproduces_warning_and_reference_repair(
    tmp_path: Path,
) -> None:
    source = load_verified_plate_seed(
        Path(
            r"D:\abaqus-runs\plate-clarification-policy-20260829-v1"
            r"\eval-9eae508c0206"
        ),
        forbidden_root=tmp_path,
    )
    defective = derive_defective_seed(source)

    result = asyncio.run(_run_reference(tmp_path.resolve(), defective))

    assert result.passed
    assert result.baseline_warning_reproduced
    assert result.final_warning_removed
    assert result.build_repaired
    assert result.extractor_preserved
    assert result.input_contract_passed
    assert result.ordered_runtime_passed
    assert result.physical_verification is not None
    assert result.physical_verification.passed
