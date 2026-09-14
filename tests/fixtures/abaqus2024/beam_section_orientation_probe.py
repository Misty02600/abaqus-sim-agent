"""Probe Abaqus 2024 BeamSection constructor/orientation ownership."""

import json
import os
import platform
import traceback

import section  # noqa: F401  # Registers profile and section APIs on Model.
from abaqus import mdb
from abaqusConstants import DURING_ANALYSIS, LINEAR

OUTPUT_PATH = os.path.join(
    os.getcwd(),
    "beam_section_orientation_probe_results.json",
)

results = {
    "schema_version": "1",
    "probe": "Abaqus 2024 BeamSection constructor/orientation ownership",
    "python": platform.python_version(),
    "cases": [],
    "completed": False,
}


def persist():
    with open(OUTPUT_PATH, "w", encoding="utf-8") as stream:
        json.dump(results, stream, ensure_ascii=True, indent=2, sort_keys=True)


def run_case(name, function):
    record = {"name": name}
    try:
        record["status"] = "ok"
        record["observation"] = function()
    except BaseException as exc:
        record["status"] = "error"
        record["error_type"] = type(exc).__name__
        record["error"] = str(exc)
        record["traceback"] = traceback.format_exc()
    results["cases"].append(record)
    persist()


def beam_section_case(model_name, keyword):
    if model_name in mdb.models:
        del mdb.models[model_name]
    model = mdb.Model(name=model_name)
    material = model.Material(name="Steel")
    material.Elastic(table=((210000.0, 0.3),))
    model.RectangularProfile(name="Profile", a=1.0, b=1.0)
    arguments = {
        "name": "Section",
        "integration": DURING_ANALYSIS,
        "profile": "Profile",
        "material": "Steel",
        "poissonRatio": 0.3,
        "temperatureVar": LINEAR,
    }
    if keyword is not None:
        arguments[keyword] = (0.0, 1.0, 0.0)
    created = model.BeamSection(**arguments)
    return {
        "created_type": type(created).__name__,
        "section_names": list(model.sections.keys()),
    }


def main():
    cases = (
        ("valid", None),
        ("orientation_keyword", "orientation"),
        ("beam_orientation_keyword", "beamOrientation"),
        ("geometric_type_keyword", "geometricType"),
    )
    for name, keyword in cases:
        run_case(
            f"Model.BeamSection.{name}",
            lambda name=name, keyword=keyword: beam_section_case(
                "BeamSection" + name.title(),
                keyword,
            ),
        )
    results["completed"] = True
    persist()
    print("Wrote beam_section_orientation_probe_results.json")


if __name__ == "__main__":
    main()
