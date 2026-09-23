"""Broken runtime-only file-lifecycle case for the runtime-feedback benchmark."""

import json
import os

CASE_ID = "closed_stream_write"


def main():
    trace_path = os.path.join(os.getcwd(), "closed_stream_trace.txt")
    with open(trace_path, "w", encoding="utf-8") as stream:
        stream.write("started\n")
    stream.write("finished\n")
    write_result({"case_id": CASE_ID, "status": "ok"})


def write_result(payload):
    path = os.path.join(os.getcwd(), CASE_ID + "_result.json")
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)


if __name__ == "__main__":
    main()
