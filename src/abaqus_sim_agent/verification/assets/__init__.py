"""Host-owned verification scripts executed by the Abaqus runtime."""

from pathlib import Path


def plate_tension_oracle_script() -> Path:
    return Path(__file__).resolve().parent / "extract_plate_tension_oracle.py"


def plate_hole_change_oracle_script() -> Path:
    return Path(__file__).resolve().parent / "extract_plate_hole_change_oracle.py"


def plate_material_change_oracle_script() -> Path:
    return Path(__file__).resolve().parent / "extract_plate_material_change_oracle.py"


def plate_load_change_oracle_script() -> Path:
    return Path(__file__).resolve().parent / "extract_plate_load_change_oracle.py"
