"""Broken ordinary-Python name case for the runtime-feedback benchmark."""

import json
import os

CASE_ID = "undefined_result_name"


def main():
    n_total_elems = 4  # noqa: F841 - intentional repair fixture
    write_result({
        "case_id": CASE_ID,
        "n_total_elements": n_total_elements,  # noqa: F821 - intentional repair fixture
    })


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
