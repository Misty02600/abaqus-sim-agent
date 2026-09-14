from abaqus_sim_agent.skills import skill_library_path


def _skill_text() -> str:
    return (
        skill_library_path()
        .joinpath("abaqus-2024-coding", "SKILL.md")
        .read_text(encoding="utf-8")
    )


def test_core_object_patterns_keep_the_verified_runtime_semantics() -> None:
    text = _skill_text()
    core_patterns = text.split("## Core object patterns", maxsplit=1)[1].split(
        "## Route by requested outcome", maxsplit=1
    )[0]

    required_fragments = (
        "from abaqus import mdb",
        "from abaqusConstants import *",
        "from caeModules import *",
        "import mesh",
        "import step",
        "dynamic APIs",
        "dictionary-like",
        "Only the corresponding constructor adds an object",
        "findAt((x, y, z))",
        "findAt(((x1, y1, z1),), ((x2, y2, z2),))",
        "native Abaqus sequence directly",
        "consumer-specific",
        "Set` to `SectionAssignment",
        "assembly-level `Set`",
        "instance-level region",
        "FieldOutput.componentLabels",
        "FieldValue.data",
        "FieldValue.nodeLabel",
        "FieldOutput.getSubset",
        "dir()",
        "one minimal evidence-backed repair",
        "run_cae_script",
        "run_solver_job",
        "run_abaqus_python",
        "not prohibit scripts",
    )
    assert all(fragment in core_patterns for fragment in required_fragments)
    assert core_patterns.count("Official") >= 5
    assert core_patterns.count("2024 reference") >= 3
    assert core_patterns.count("2024 runtime") >= 1
    assert len(core_patterns) < 5_000
    assert "do not wrap it again" not in core_patterns


def test_core_object_patterns_do_not_embed_the_plate_gold_answer() -> None:
    text = _skill_text()
    core_patterns = text.split("## Core object patterns", maxsplit=1)[1].split(
        "## Route by requested outcome", maxsplit=1
    )[0]

    task_specific_fragments = (
        "plate_tension",
        "CPS4R",
        "210000",
        "StaticStep(name=",
        "DisplacementBC(name=",
        "100×20",
        "20×4",
    )
    assert not any(fragment in core_patterns for fragment in task_specific_fragments)
