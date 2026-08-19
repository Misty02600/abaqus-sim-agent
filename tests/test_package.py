from pathlib import Path

import abaqus_sim_agent


def test_package_is_importable() -> None:
    assert abaqus_sim_agent.__file__ is not None
    expected = Path(
        Path(__file__).parents[1],
        "src",
        "abaqus_sim_agent",
        "__init__.py",
    )
    assert Path(abaqus_sim_agent.__file__).resolve() == expected.resolve()
