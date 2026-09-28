from pathlib import Path


def bundled_catalog_path() -> Path:
    return Path(__file__).resolve().parent / "data"
