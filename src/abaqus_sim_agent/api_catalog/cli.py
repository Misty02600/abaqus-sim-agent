from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from pydantic import BaseModel

from .abqpy import load_abqpy_records
from .catalog import ApiCatalog
from .evaluation import evaluate_api_catalog, load_api_catalog_cases
from .members import load_abqpy_member_cards
from .models import AbqpyApiRecord, ApiRuntimeNote
from .official import AbaqusScriptingReferenceLoader


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m abaqus_sim_agent.api_catalog",
        description="Build and query an exact Abaqus API catalog.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Build an immutable API catalog")
    build.add_argument("--runtime-release", required=True)
    build.add_argument("--output-dir", required=True, type=Path)
    build.add_argument("--docs-root", type=Path)
    build.add_argument(
        "--docs-base-url",
        default="http://localhost:4040/English",
    )
    build.add_argument("--abqpy-records", type=Path)
    build.add_argument("--abqpy-source", type=Path)
    build.add_argument("--abqpy-version")
    build.add_argument("--abqpy-release")
    build.add_argument(
        "--abqpy-member-owner",
        action="append",
        default=[],
        help="Opt in to annotated members of this owner (repeatable; wheel only)",
    )
    build.add_argument("--runtime-notes", type=Path)
    build.set_defaults(handler=_build)

    lookup = commands.add_parser(
        "lookup",
        help="Look up an exact method, Method.parameter, or Owner.member name",
    )
    lookup.add_argument("--catalog-dir", required=True, type=Path)
    lookup.add_argument("--limit", type=int, default=3)
    lookup.add_argument("name")
    lookup.set_defaults(handler=_lookup)

    evaluate = commands.add_parser("evaluate", help="Evaluate exact API lookups")
    evaluate.add_argument("--catalog-dir", required=True, type=Path)
    evaluate.add_argument("--benchmark", required=True, type=Path)
    evaluate.add_argument("--output", type=Path)
    evaluate.set_defaults(handler=_evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return 2


def _build(args: argparse.Namespace) -> int:
    if args.docs_root is None and all(
        value is None for value in (args.abqpy_records, args.abqpy_source)
    ):
        raise ValueError(
            "build requires --docs-root, --abqpy-source, or --abqpy-records"
        )
    if args.abqpy_records is not None and args.abqpy_source is not None:
        raise ValueError("use either --abqpy-source or --abqpy-records, not both")
    if args.abqpy_source is not None and not args.abqpy_version:
        raise ValueError("--abqpy-version is required with --abqpy-source")
    members = ()
    if args.abqpy_member_owner:
        if args.abqpy_source is None:
            raise ValueError("--abqpy-member-owner requires --abqpy-source")
        members = load_abqpy_member_cards(
            args.abqpy_source,
            version=args.abqpy_version,
            release=args.abqpy_release or args.runtime_release,
            runtime_release=args.runtime_release,
            owners=args.abqpy_member_owner,
        )
    official_records = ()
    source_digests: dict[str, str] = {}
    if args.docs_root is not None:
        official_loader = AbaqusScriptingReferenceLoader(
            args.docs_root,
            docs_base_url=args.docs_base_url,
        )
        official_records = tuple(official_loader.lazy_load())
        source_digests["official_scripting_reference"] = _sha256_tree(
            official_loader.guide_root
        )
    if args.abqpy_source is not None:
        abqpy_records = tuple(
            load_abqpy_records(
                args.abqpy_source,
                version=args.abqpy_version,
                release=args.abqpy_release or args.runtime_release,
            )
        )
        source_digests["abqpy_source"] = _sha256_source(args.abqpy_source)
        if any(
            member.distribution_sha256 != source_digests["abqpy_source"]
            for member in members
        ):
            raise ValueError("abqpy wheel changed between method and member extraction")
    elif args.abqpy_records is not None:
        abqpy_records = _load_jsonl(args.abqpy_records, AbqpyApiRecord)
        source_digests["abqpy_records"] = _sha256_file(args.abqpy_records)
    else:
        abqpy_records = ()
    runtime_notes = (
        _load_jsonl(args.runtime_notes, ApiRuntimeNote)
        if args.runtime_notes is not None
        else ()
    )
    if args.runtime_notes is not None:
        source_digests["runtime_notes"] = _sha256_file(args.runtime_notes)
    catalog = ApiCatalog.build(
        runtime_release=args.runtime_release,
        official_records=official_records,
        abqpy_records=abqpy_records,
        runtime_notes=runtime_notes,
        members=members,
    )
    manifest = catalog.write(args.output_dir, source_digests=source_digests)
    print(manifest.model_dump_json(indent=2))
    return 0


def _lookup(args: argparse.Namespace) -> int:
    result = ApiCatalog.open(args.catalog_dir).lookup(
        args.name,
        limit=args.limit,
    )
    print(result.model_dump_json(indent=2))
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    evaluation = evaluate_api_catalog(
        ApiCatalog.open(args.catalog_dir),
        load_api_catalog_cases(args.benchmark),
    )
    payload = evaluation.model_dump_json(indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if evaluation.pass_count == evaluation.total else 1


def _load_jsonl[ModelT: BaseModel](
    path: Path,
    model_type: type[ModelT],
) -> tuple[ModelT, ...]:
    records: list[ModelT] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            records.append(model_type.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid record at {path}:{line_number}") from exc
    return tuple(records)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.resolve(strict=True).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    resolved = root.resolve(strict=True)
    digest = hashlib.sha256()
    for path in sorted(resolved.glob("*.htm")):
        if path.is_symlink() or not path.is_file():
            continue
        digest.update(path.relative_to(resolved).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _sha256_source(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if resolved.is_file():
        return _sha256_file(resolved)
    digest = hashlib.sha256()
    for source_file in sorted(resolved.rglob("*.py")):
        if source_file.is_symlink() or not source_file.is_file():
            continue
        digest.update(source_file.relative_to(resolved).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(source_file)))
    return digest.hexdigest()
