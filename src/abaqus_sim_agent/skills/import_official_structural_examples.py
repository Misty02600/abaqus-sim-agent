"""从本机 Abaqus 官方文档导出结构力学专题脚本池；只保留本地，不执行脚本。"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from bs4 import BeautifulSoup

ExtractionKind = Literal["embedded-codeblock", "linked-resource"]
TeachingTier = Literal["basic-representative", "topic-typical", "complex-comprehensive"]

LINKED_INVENTORY_MAPS = (
    "SIMACAEEXARefMap",
    "SIMACAEBMKRefMap",
    "SIMACAEVERRefMap",
)


@dataclass(frozen=True)
class OfficialExampleSpec:
    primary_topic: str
    script_name: str
    page_name: str
    cross_topics: tuple[str, ...] = ()
    source_map: str = "SIMACAEGSARefMap"
    extraction_kind: ExtractionKind = "embedded-codeblock"
    teaching_tier: TeachingTier = "basic-representative"


DEFAULT_EXAMPLES = (
    OfficialExampleSpec(
        "linear-structural-foundation",
        "gsi_frame_caemodel.py",
        "simagsa-c-appendixframe.htm",
        ("truss", "wire", "sections"),
    ),
    OfficialExampleSpec(
        "linear-structural-foundation",
        "gsi_lug_caemodel.py",
        "simagsa-c-appendixlug.htm",
        ("solid-mechanics", "partitioning", "meshing"),
    ),
    OfficialExampleSpec(
        "linear-structural-foundation",
        "gsi_skewplate_caemodel.py",
        "simagsa-c-appendixskewplate.htm",
        ("shells", "pressure"),
    ),
    OfficialExampleSpec(
        "linear-structural-foundation",
        "gsi_crane_caemodel.py",
        "simagsa-c-appendixcrane.htm",
        ("beams", "beam-orientation"),
    ),
    OfficialExampleSpec(
        "hyperelasticity",
        "gsi_mount_caemodel.py",
        "simagsa-c-appendixmount.htm",
        ("geometric-nonlinearity", "axisymmetric", "hybrid-elements"),
    ),
    OfficialExampleSpec(
        "plasticity",
        "gsi_plasticlug_caemodel.py",
        "simagsa-c-appendixplastlug.htm",
        ("solid-mechanics",),
    ),
    OfficialExampleSpec(
        "geometric-nonlinearity",
        "gsi_nlskewplate_caemodel.py",
        "simagsa-c-appendixnlskew.htm",
        ("shells",),
    ),
    OfficialExampleSpec(
        "structural-dynamics",
        "gsi_dyncrane_caemodel.py",
        "simagsa-c-appendixdyncrane.htm",
        ("modal-dynamics", "explicit-dynamics"),
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "structural-dynamics",
        "gsi_pipe_caemodel.py",
        "simagsa-c-appendixpiping.htm",
        ("frequency", "prestress", "restart"),
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "explicit-dynamics",
        "gxi_stresswave_caemodel.py",
        "simagsa-c-appendixwave.htm",
        ("stress-wave",),
    ),
    OfficialExampleSpec(
        "explicit-dynamics",
        "gxi_stiffplate_caemodel.py",
        "simagsa-c-appendixblast.htm",
        ("plasticity", "shells", "blast-loading"),
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "explicit-dynamics",
        "gxi_circuit_caemodel.py",
        "simagsa-c-appendixcircuit.htm",
        ("contact", "drop-test"),
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "contact",
        "gsi_lap_joint_caemodel.py",
        "simagsa-c-appendixlap.htm",
        ("plasticity", "geometric-nonlinearity", "friction"),
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "contact",
        "gsi_channel_caemodel.py",
        "simagsa-c-appendixchannel.htm",
        ("plasticity", "geometric-nonlinearity", "explicit-dynamics", "forming", "friction"),
        teaching_tier="complex-comprehensive",
    ),
    OfficialExampleSpec(
        "plasticity",
        "gsi_channel_caemodel_spring.py",
        "simagsa-c-appendixchannel.htm",
        ("geometric-nonlinearity", "springback", "forming"),
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "contact",
        "pillar_fastener_std.py",
        "simaexa-c-bucklespotweld.htm",
        ("plasticity", "geometric-nonlinearity", "fasteners", "buckling"),
        source_map="SIMACAEEXARefMap",
        extraction_kind="linked-resource",
        teaching_tier="complex-comprehensive",
    ),
    OfficialExampleSpec(
        "contact",
        "snubber_model.py",
        "simaexa-c-snubberarm.htm",
        ("geometric-nonlinearity", "friction", "rigid-bodies", "connectors"),
        source_map="SIMACAEEXARefMap",
        extraction_kind="linked-resource",
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "structural-dynamics",
        "rigmultimech.py",
        "simaexa-c-crank.htm",
        ("explicit-dynamics", "rigid-bodies", "connectors", "mechanisms"),
        source_map="SIMACAEEXARefMap",
        extraction_kind="linked-resource",
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "explicit-dynamics",
        "rivet_forming.py",
        "simaexa-c-rivet.htm",
        ("contact", "plasticity", "forming", "eulerian", "cel"),
        source_map="SIMACAEEXARefMap",
        extraction_kind="linked-resource",
        teaching_tier="complex-comprehensive",
    ),
    OfficialExampleSpec(
        "explicit-dynamics",
        "cel_bottle_drop.py",
        "simaexa-c-bottledrop.htm",
        ("contact", "plasticity", "damage", "eulerian", "cel", "drop-test"),
        source_map="SIMACAEEXARefMap",
        extraction_kind="linked-resource",
        teaching_tier="complex-comprehensive",
    ),
    OfficialExampleSpec(
        "interface-cohesive",
        "alfano_2d_model.py",
        "simabmk-c-alfanodelamination.htm",
        ("contact", "damage", "composites", "geometric-nonlinearity"),
        source_map="SIMACAEBMKRefMap",
        extraction_kind="linked-resource",
        teaching_tier="topic-typical",
    ),
    OfficialExampleSpec(
        "linear-structural-foundation",
        "pipe_cae_c3d20rsub_s4.py",
        "simaexa-c-shellsolidpipe.htm",
        ("shells", "solids", "submodeling", "shell-to-solid-coupling", "odb"),
        source_map="SIMACAEEXARefMap",
        extraction_kind="linked-resource",
        teaching_tier="complex-comprehensive",
    ),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_python310(code: bytes, script_name: str) -> None:
    ast.parse(code.decode("utf-8-sig"), filename=script_name, feature_version=(3, 10))


def _page_metadata(raw: bytes) -> tuple[str, str]:
    tree = BeautifulSoup(raw, "html.parser", from_encoding="utf-8")
    title_node = tree.find("title")
    title = title_node.get_text(" ", strip=True) if title_node is not None else ""
    description_node = tree.find("meta", attrs={"name": "description"})
    description = ""
    if description_node is not None:
        description = str(description_node.get("content", "")).strip()
    return title, description


def _resource_name_from_href(href: str) -> str | None:
    clean = unquote(href).split("#", maxsplit=1)[0].split("?", maxsplit=1)[0]
    parts = [part for part in clean.replace("\\", "/").split("/") if part]
    if len(parts) < 2 or parts[-2] != "SIMAINPRefResources":
        return None
    name = parts[-1]
    return name if name.lower().endswith(".py") else None


def _extract_embedded_script(page: Path, script_name: str) -> tuple[bytes, bytes, str | None]:
    raw = page.read_bytes()
    tree = BeautifulSoup(raw, "html.parser", from_encoding="utf-8")
    headings = [
        node
        for node in tree.find_all("h2")
        if node.get_text(strip=True) == script_name
    ]
    if len(headings) != 1:
        raise ValueError(f"expected one {script_name!r} heading in {page}")
    block = headings[0].find_next("pre", class_="codeblock")
    if block is None:
        raise ValueError(f"missing code block for {script_name!r} in {page}")
    code = block.get_text().encode("utf-8")
    _parse_python310(code, script_name)
    return raw, code, None


def _extract_linked_script(
    docs_root: Path,
    page: Path,
    script_name: str,
) -> tuple[bytes, bytes, str]:
    raw = page.read_bytes()
    tree = BeautifulSoup(raw, "html.parser", from_encoding="utf-8")
    linked_names = {
        name
        for node in tree.find_all("a", href=True)
        if (name := _resource_name_from_href(str(node.get("href", "")))) is not None
    }
    if script_name not in linked_names:
        raise ValueError(f"{page} does not link SIMAINPRefResources/{script_name}")
    resource_rel = f"SIMAINPRefResources/{script_name}"
    code = (docs_root / "SIMAINPRefResources" / script_name).read_bytes()
    _parse_python310(code, script_name)
    return raw, code, resource_rel


def _extract_script(
    docs_root: Path,
    spec: OfficialExampleSpec,
) -> tuple[bytes, bytes, str | None]:
    page = docs_root / spec.source_map / spec.page_name
    if spec.extraction_kind == "embedded-codeblock":
        return _extract_embedded_script(page, spec.script_name)
    return _extract_linked_script(docs_root, page, spec.script_name)


def build_linked_example_inventory(
    docs_root: Path,
    *,
    source_release: str,
    source_maps: tuple[str, ...] = LINKED_INVENTORY_MAPS,
) -> dict[str, object]:
    """索引 EXA/BMK/VER 中链接的 Python 资源，不复制全部 vendor 脚本。"""
    records: dict[str, dict[str, object]] = {}
    resource_cache: dict[str, tuple[bool, str | None, bool, str | None]] = {}
    anchor_link_count = 0
    reference_count = 0

    for source_map in source_maps:
        map_root = docs_root / source_map
        pages = sorted({*map_root.rglob("*.htm"), *map_root.rglob("*.html")})
        for page in pages:
            raw = page.read_bytes()
            if b"SIMAINPRefResources" not in raw or b".py" not in raw:
                continue
            tree = BeautifulSoup(raw, "html.parser", from_encoding="utf-8")
            title, description = _page_metadata(raw)
            source_sha256 = _sha256(raw)
            seen_on_page: set[str] = set()
            for node in tree.find_all("a", href=True):
                script_name = _resource_name_from_href(str(node.get("href", "")))
                if script_name is None:
                    continue
                anchor_link_count += 1
                if script_name in seen_on_page:
                    continue
                seen_on_page.add(script_name)
                reference_count += 1
                if script_name not in resource_cache:
                    resource = docs_root / "SIMAINPRefResources" / script_name
                    exists = resource.is_file()
                    script_sha256: str | None = None
                    syntax_ok = False
                    syntax_error: str | None = None
                    if exists:
                        payload = resource.read_bytes()
                        script_sha256 = _sha256(payload)
                        try:
                            _parse_python310(payload, script_name)
                            syntax_ok = True
                        except (SyntaxError, UnicodeDecodeError) as exc:
                            syntax_error = str(exc)
                    resource_cache[script_name] = (
                        exists,
                        script_sha256,
                        syntax_ok,
                        syntax_error,
                    )
                exists, script_sha256, syntax_ok, syntax_error = resource_cache[script_name]
                record = records.setdefault(
                    script_name,
                    {
                        "script_name": script_name,
                        "resource": f"SIMAINPRefResources/{script_name}",
                        "resource_exists": exists,
                        "script_sha256": script_sha256,
                        "python310_syntax": syntax_ok,
                        "syntax_error": syntax_error,
                        "references": [],
                    },
                )
                references = record["references"]
                if not isinstance(references, list):
                    raise TypeError("inventory references must be a list")
                references.append(
                    {
                        "source_map": source_map,
                        "page": page.name,
                        "source_sha256": source_sha256,
                        "title": title,
                        "description": description,
                    }
                )

    scripts = [records[name] for name in sorted(records)]
    missing_count = sum(not bool(record["resource_exists"]) for record in scripts)
    invalid_count = sum(
        bool(record["resource_exists"]) and not bool(record["python310_syntax"])
        for record in scripts
    )
    return {
        "source_release": source_release,
        "source_maps": list(source_maps),
        "anchor_link_count": anchor_link_count,
        "reference_count": reference_count,
        "unique_script_count": len(scripts),
        "missing_resource_count": missing_count,
        "invalid_python310_count": invalid_count,
        "all_python310_syntax": missing_count == 0 and invalid_count == 0,
        "rights": "Vendor material indexed locally; redistribution not established.",
        "scripts": scripts,
    }


def export_structural_example_pool(
    docs_root: Path,
    output: Path,
    *,
    source_release: str,
    examples: tuple[OfficialExampleSpec, ...] = DEFAULT_EXAMPLES,
) -> dict[str, object]:
    """导出精选官方专题脚本并保存 EXA/BMK/VER 全量 Python 链接清单。"""
    if not source_release.strip():
        raise ValueError("source_release is required")
    if output.exists():
        raise FileExistsError(output)
    if len({spec.script_name for spec in examples}) != len(examples):
        raise ValueError("each curated script must have exactly one canonical spec")

    script_payloads: dict[str, bytes] = {}
    source_payloads: dict[str, bytes] = {}
    records: list[dict[str, object]] = []

    for spec in examples:
        raw, code, source_resource = _extract_script(docs_root, spec)
        script_payloads[spec.script_name] = code
        source_key = f"{spec.source_map}/{spec.page_name}"
        source_payloads[source_key] = raw
        records.append(
            {
                "primary_topic": spec.primary_topic,
                "cross_topics": list(spec.cross_topics),
                "teaching_tier": spec.teaching_tier,
                "script": f"scripts/{spec.script_name}",
                "source_map": spec.source_map,
                "source_page": f"sources/{source_key}",
                "source_resource": source_resource,
                "extraction_kind": spec.extraction_kind,
                "source_sha256": _sha256(raw),
                "script_sha256": _sha256(code),
            }
        )

    inventory = build_linked_example_inventory(
        docs_root,
        source_release=source_release,
    )
    inventory_bytes = (json.dumps(inventory, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    inventory_summary = {
        "path": "inventory.json",
        "source_maps": inventory["source_maps"],
        "anchor_link_count": inventory["anchor_link_count"],
        "reference_count": inventory["reference_count"],
        "unique_script_count": inventory["unique_script_count"],
        "missing_resource_count": inventory["missing_resource_count"],
        "invalid_python310_count": inventory["invalid_python310_count"],
    }

    lines = [
        "# Abaqus 官方结构力学专题脚本池",
        "",
        f"来源版本：{source_release}",
        "",
        "`scripts/` 只保存精选教学案例；`inventory.json` 记录 EXA/BMK/VER 中全部 Python 资源链接，避免后续反复扫描官方文档。",
        "精选文件从本机官方文档原样提取，只通过 Python 3.10 语法解析，没有执行，也不证明 Abaqus 2024 兼容。",
        "同一脚本只有一个 canonical 副本，其他专题通过 cross_topics 交叉引用；教学层级分为 basic-representative、topic-typical、complex-comprehensive。",
        "官方教程中的 GUI、submit/wait、ODB 或旧 API 操作仍保留在原稿中，生产建模脚本必须按当前 CAE → Solver → ODB 边界适配。",
        "",
        "| 层级 | 主专题 | 脚本 | 来源 | 交叉主题 |",
        "|---|---|---|---|---|",
    ]
    for spec in examples:
        lines.append(
            f"| {spec.teaching_tier} | {spec.primary_topic} | `scripts/{spec.script_name}` | "
            f"{spec.source_map}/{spec.page_name} | {', '.join(spec.cross_topics) or '-'} |"
        )
    readme = ("\n".join(lines) + "\n").encode("utf-8")

    files: dict[str, str] = {
        "README.md": _sha256(readme),
        "inventory.json": _sha256(inventory_bytes),
    }
    manifest: dict[str, object] = {
        "source_release": source_release,
        "target_runtime": "Abaqus 2024; compatibility not established by export",
        "extraction": {
            "embedded-codeblock": "exact h2 script heading followed by first pre.codeblock",
            "linked-resource": "page must link the named SIMAINPRefResources Python file; resource bytes copied unchanged",
        },
        "python310_syntax": True,
        "abaqus_executed": False,
        "rights": "Vendor material retained locally; redistribution not established.",
        "inventory": inventory_summary,
        "examples": records,
        "files": files,
    }

    output.mkdir(parents=True)
    (output / "scripts").mkdir()
    (output / "sources").mkdir()
    (output / "README.md").write_bytes(readme)
    (output / "inventory.json").write_bytes(inventory_bytes)
    for name, payload in script_payloads.items():
        path = output / "scripts" / name
        path.write_bytes(payload)
        files[f"scripts/{name}"] = _sha256(payload)
    for name, payload in source_payloads.items():
        path = output / "sources" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        files[f"sources/{name}"] = _sha256(payload)
    (output / "source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-release", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            export_structural_example_pool(
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
