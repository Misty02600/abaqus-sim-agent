"""Broken FieldOutputRequest setup case for the API-repair benchmark."""

import json
import os

from abaqus import mdb

CASE_ID = "field_output_missing_step_import"


def main():
    model = mdb.Model(name="FieldOutputMissingStepImportCase")

    # Create the requested field output request in the Load step.
    model.FieldOutputRequest(
        name="ProbeOutput",
        createStepName="Load",
        variables=("U", "S"),
    )

    if "Load" not in model.steps or "ProbeOutput" not in model.fieldOutputRequests:
        raise RuntimeError("step or field output request was not created")
    write_result(
        {
            "case_id": CASE_ID,
            "step": "Load",
            "request": "ProbeOutput",
            "variables_requested": ["U", "S"],
        }
    )


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
