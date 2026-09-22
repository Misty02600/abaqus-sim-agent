"""从本机 Abaqus 官方文档建立结构力学专题文本来源清单；不复制整页正文。"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bs4 import BeautifulSoup

SourceRole = Literal[
    "core",
    "secondary",
    "advanced",
    "troubleshooting",
    "theory",
    "cae-howto",
]


@dataclass(frozen=True)
class OfficialTextSourceSpec:
    topic: str
    source_map: str
    page_name: str
    recommended_role: SourceRole
    cross_topics: tuple[str, ...] = ()


DEFAULT_TEXT_SOURCES = (
    OfficialTextSourceSpec(
        "linear-structural-foundation",
        "SIMACAEANLRefMap",
        "simaanl-c-staticproc.htm",
        "core",
    ),
    OfficialTextSourceSpec(
        "linear-structural-foundation",
        "SIMACAEANLRefMap",
        "simaanl-c-static.htm",
        "core",
    ),
    OfficialTextSourceSpec(
        "linear-structural-foundation",
        "SIMACAEELMRefMap",
        "simaelm-c-beamelem.htm",
        "secondary",
        ("beams", "element-selection"),
    ),
    OfficialTextSourceSpec(
        "linear-structural-foundation",
        "SIMACAEELMRefMap",
        "simaelm-c-shellelem.htm",
        "secondary",
        ("shells", "element-selection"),
    ),
    OfficialTextSourceSpec(
        "linear-structural-foundation",
        "SIMACAEELMRefMap",
        "simaelm-c-solidcont.htm",
        "secondary",
        ("solids", "element-selection"),
    ),
    OfficialTextSourceSpec(
        "hyperelasticity",
        "SIMACAEMATRefMap",
        "simamat-c-hyperelastic.htm",
        "core",
        ("geometric-nonlinearity", "near-incompressibility", "material-calibration"),
    ),
    OfficialTextSourceSpec(
        "plasticity",
        "SIMACAEMATRefMap",
        "simamat-c-metalplastic.htm",
        "core",
        ("material-nonlinearity", "metal-plasticity"),
    ),
    OfficialTextSourceSpec(
        "plasticity",
        "SIMACAEMATRefMap",
        "simamat-c-plastic.htm",
        "secondary",
        ("material-data", "test-data", "stress-strain-measures"),
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAEGSARefMap",
        "simagsa-c-nlngeomnonlin.htm",
        "core",
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAECAERefMap",
        "simacae-t-simstepmannlgeom.htm",
        "cae-howto",
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAEMODRefMap",
        "simamod-c-conventions.htm",
        "core",
        ("finite-strain-output", "hyperelasticity", "postprocessing"),
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAEOUTRefMap",
        "simaout-c-stdoutputvar.htm",
        "core",
        ("finite-strain-output", "hyperelasticity", "postprocessing"),
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAETHERefMap",
        "simathe-c-strainmeas.htm",
        "theory",
        ("finite-strain-output", "hyperelasticity", "postprocessing"),
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAETHERefMap",
        "simathe-c-stressmeas.htm",
        "theory",
        ("finite-strain-output", "hyperelasticity", "postprocessing"),
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAEANLRefMap",
        "simaanl-c-eigenbuckling.htm",
        "core",
        ("buckling", "stability", "linear-perturbation"),
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAEANLRefMap",
        "simaanl-c-imperfection.htm",
        "secondary",
        ("buckling", "postbuckling", "imperfection"),
    ),
    OfficialTextSourceSpec(
        "geometric-nonlinearity",
        "SIMACAEANLRefMap",
        "simaanl-c-postbuckling.htm",
        "core",
        ("postbuckling", "riks", "path-following", "stability"),
    ),
    OfficialTextSourceSpec(
        "structural-dynamics",
        "SIMACAEANLRefMap",
        "simaanl-c-dynamicproc.htm",
        "core",
    ),
    OfficialTextSourceSpec(
        "structural-dynamics",
        "SIMACAEANLRefMap",
        "simaanl-c-freqextraction.htm",
        "core",
        ("modal-analysis",),
    ),
    OfficialTextSourceSpec(
        "structural-dynamics",
        "SIMACAEANLRefMap",
        "simaanl-c-modaldynamic.htm",
        "secondary",
        ("modal-analysis", "transient-response"),
    ),
    OfficialTextSourceSpec(
        "structural-dynamics",
        "SIMACAEANLRefMap",
        "simaanl-c-dynamic.htm",
        "secondary",
        ("implicit-dynamics", "direct-integration"),
    ),
    OfficialTextSourceSpec(
        "explicit-dynamics",
        "SIMACAEANLRefMap",
        "simaanl-c-expdynamic.htm",
        "core",
        ("time-integration", "stable-time-increment"),
    ),
    OfficialTextSourceSpec(
        "explicit-dynamics",
        "SIMACAEANLRefMap",
        "simaanl-c-massscaling.htm",
        "core",
        ("stable-time-increment", "quasi-static-explicit"),
    ),
    OfficialTextSourceSpec(
        "explicit-dynamics",
        "SIMACAEELMRefMap",
        "simaelm-c-sectioncontrol.htm",
        "secondary",
        ("hourglass-control", "distortion-control", "element-formulation"),
    ),
    OfficialTextSourceSpec(
        "explicit-dynamics",
        "SIMACAEOUTRefMap",
        "simaout-c-exp-totalenergyoutput.htm",
        "secondary",
        ("energy-output", "artificial-energy", "diagnostics"),
    ),
    OfficialTextSourceSpec(
        "contact",
        "SIMACAEITNRefMap",
        "simaitn-c-contactoverview.htm",
        "core",
    ),
    OfficialTextSourceSpec(
        "contact",
        "SIMACAEITNRefMap",
        "simaitn-c-contactmechanical.htm",
        "core",
        ("normal-contact", "friction"),
    ),
    OfficialTextSourceSpec(
        "contact",
        "SIMACAEITNRefMap",
        "simaitn-c-contacttrouble.htm",
        "troubleshooting",
        ("abaqus-standard",),
    ),
    OfficialTextSourceSpec(
        "contact",
        "SIMACAEITNRefMap",
        "simaitn-c-expcontacttrouble.htm",
        "troubleshooting",
        ("abaqus-explicit",),
    ),
    OfficialTextSourceSpec(
        "interface-cohesive",
        "SIMACAEELMRefMap",
        "simaelm-c-cohesiveoverview.htm",
        "core",
        ("cohesive-elements",),
    ),
    OfficialTextSourceSpec(
        "interface-cohesive",
        "SIMACAEELMRefMap",
        "simaelm-c-cohesiveelem.htm",
        "secondary",
        ("cohesive-elements", "element-selection"),
    ),
    OfficialTextSourceSpec(
        "interface-cohesive",
        "SIMACAEELMRefMap",
        "simaelm-c-cohesivebehavior.htm",
        "core",
        ("traction-separation", "damage"),
    ),
    OfficialTextSourceSpec(
        "interface-cohesive",
        "SIMACAEITNRefMap",
        "simaitn-c-cohesivebehavior.htm",
        "core",
        ("cohesive-contact", "damage", "contact"),
    ),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _page_metadata(raw: bytes) -> tuple[str, str, list[str]]:
    tree = BeautifulSoup(raw, "html.parser", from_encoding="utf-8")
    title_node = tree.find("title")
    title = title_node.get_text(" ", strip=True) if title_node is not None else ""
    description_node = tree.find("meta", attrs={"name": "description"})
    description = ""
    if description_node is not None:
        description = str(description_node.get("content", "")).strip()

    headings: list[str] = []
    for node in tree.find_all(("h1", "h2", "h3")):
        heading = " ".join(node.get_text(" ", strip=True).split())
        if heading and heading not in headings:
            headings.append(heading)
    return title, description, headings


def build_official_text_source_inventory(
    docs_root: Path,
    *,
    source_release: str,
    sources: tuple[OfficialTextSourceSpec, ...] = DEFAULT_TEXT_SOURCES,
) -> dict[str, object]:
    """读取精选官方页面元数据，生成可复查的专题来源清单。"""
    if not source_release.strip():
        raise ValueError("source_release is required")
    keys = [(spec.source_map, spec.page_name) for spec in sources]
    if len(set(keys)) != len(keys):
        raise ValueError("each curated official text page must have one canonical spec")

    records: list[dict[str, object]] = []
    for spec in sources:
        page = docs_root / spec.source_map / spec.page_name
        if not page.is_file():
            raise FileNotFoundError(page)
        raw = page.read_bytes()
        title, description, headings = _page_metadata(raw)
        records.append(
            {
                "topic": spec.topic,
                "cross_topics": list(spec.cross_topics),
                "recommended_role": spec.recommended_role,
                "source_release": source_release,
                "source_map": spec.source_map,
                "page": spec.page_name,
                "relative_path": f"{spec.source_map}/{spec.page_name}",
                "title": title,
                "description": description,
                "section_headings": headings,
                "size_bytes": len(raw),
                "sha256": _sha256(raw),
            }
        )

    topic_counts = Counter(str(record["topic"]) for record in records)
    role_counts = Counter(str(record["recommended_role"]) for record in records)
    return {
        "source_release": source_release,
        "page_count": len(records),
        "topic_counts": dict(sorted(topic_counts.items())),
        "role_counts": dict(sorted(role_counts.items())),
        "rights": "Vendor documentation indexed locally; redistribution not established.",
        "purpose": (
            "Curated source map for deriving high-density topic notes; full vendor pages are "
            "not copied into the knowledge package."
        ),
        "pages": records,
    }


def export_official_text_source_inventory(
    docs_root: Path,
    output: Path,
    *,
    source_release: str,
    sources: tuple[OfficialTextSourceSpec, ...] = DEFAULT_TEXT_SOURCES,
) -> dict[str, object]:
    """把官方文本来源清单写入本地快照目录。"""
    if output.exists():
        raise FileExistsError(output)
    inventory = build_official_text_source_inventory(
        docs_root,
        source_release=source_release,
        sources=sources,
    )
    inventory_bytes = (
        json.dumps(inventory, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")

    lines = [
        "# Abaqus 官方结构力学文本来源清单",
        "",
        f"来源版本：{source_release}",
        "",
        "本目录只保存页面级元数据、章节标题和 hash，不复制官方手册整页正文。",
        "用途是稳定记录每个知识专题应优先阅读的官方页面，后续从这些页面整理原创的高密度 Markdown 笔记。",
        "Abaqus 2026 FD02 文档来源不构成 Abaqus 2024 运行兼容证据。",
        "",
        "| 专题 | 角色 | 官方页面 |",
        "|---|---|---|",
    ]
    pages = inventory["pages"]
    if not isinstance(pages, list):
        raise TypeError("inventory pages must be a list")
    for page in pages:
        if not isinstance(page, dict):
            raise TypeError("inventory page must be an object")
        lines.append(
            f"| {page['topic']} | {page['recommended_role']} | "
            f"`{page['relative_path']}` — {page['title']} |"
        )
    readme = ("\n".join(lines) + "\n").encode("utf-8")

    manifest = {
        "source_release": source_release,
        "target_runtime": "Abaqus 2024; compatibility not established by source indexing",
        "abaqus_executed": False,
        "vendor_pages_copied": False,
        "inventory_sha256": _sha256(inventory_bytes),
        "readme_sha256": _sha256(readme),
        "rights": "Vendor documentation indexed locally; redistribution not established.",
    }

    output.mkdir(parents=True)
    (output / "inventory.json").write_bytes(inventory_bytes)
    (output / "README.md").write_bytes(readme)
    (output / "source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-release", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            export_official_text_source_inventory(
                args.docs_root,
                args.output,
                source_release=args.source_release,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
