from pathlib import Path


def skill_library_path() -> Path:
    return Path(__file__).resolve().parent
