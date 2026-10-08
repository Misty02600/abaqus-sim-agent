import json
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.application import prepare_new_run
from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.domain_knowledge import (
    DOMAIN_KNOWLEDGE_STATE_DIRECTORY,
    discover_domain_knowledge_packs,
    materialize_domain_knowledge_bundle,
    prepare_domain_knowledge_bundle,
    validate_domain_knowledge_bundle,
)
from abaqus_sim_agent.coding.model import (
    DeepSeekGateway,
)
from abaqus_sim_agent.coding.options import RunOptions, resolve_run_config


def _write_pack(
    root: Path,
    pack_id: str = "flexible-electronics-fea",
    *,
    release: str = "2026.09.1",
    content: str = "# Flexible electronics\nReference only.\n",
) -> Path:
    directory = root / pack_id
    directory.mkdir(parents=True)
    (directory / "README.md").write_text(content, encoding="utf-8")
    (directory / "unlisted.md").write_text("must stay private", encoding="utf-8")
    manifest = {
        "schema_version": "domain-knowledge-pack-v1",
        "pack_id": pack_id,
        "release": release,
        "summary": "柔性电子结构与材料建模参考",
        "entrypoints": ["README.md"],
        "compatibility": ["Abaqus 2024", "reference-only"],
        "resources": [
            {
                "path": "README.md",
                "kind": "guide",
                "title": "入口",
                "source": {
                    "kind": "original",
                    "title": "本机维护者整理",
                    "reference": None,
                    "version": None,
                    "license": None,
                    "notes": None,
                },
                "validation": {
                    "content_review": "reviewed",
                    "runtime": "not_run",
                    "physics": "not_checked",
                    "task_benefit": "not_evaluated",
                    "evidence_ids": [],
                    "notes": None,
                },
            }
        ],
    }
    (directory / "pack.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def _workspace_and_state(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    workspace.mkdir(parents=True)
    state.mkdir(parents=True)
    return workspace, state


def test_inventory_scans_only_direct_nonhidden_pack_directories(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    _write_pack(root)
    invalid = root / "broken"
    invalid.mkdir()
    (root / ".archive" / "old-pack").mkdir(parents=True)

    inventory = discover_domain_knowledge_packs(root)

    assert [pack.pack_id for pack in inventory.packs] == ["flexible-electronics-fea"]
    assert inventory.packs[0].release == "2026.09.1"
    assert [
        (issue.directory, "pack.json" in issue.error) for issue in inventory.issues
    ] == [("broken", True)]


def test_selected_pack_is_snapshotted_namespaced_and_materialized(
    tmp_path: Path,
) -> None:
    root = tmp_path / "packs"
    source = _write_pack(root)
    workspace, state = _workspace_and_state(tmp_path)

    binding = prepare_domain_knowledge_bundle(
        ("flexible-electronics-fea",),
        state_dir=state,
        workspace=workspace,
        packs_root=root,
    )

    assert binding is not None
    assert binding.packs[0].snapshot_relative_path == (
        "domain-knowledge/packs/flexible-electronics-fea"
    )
    snapshot = state / DOMAIN_KNOWLEDGE_STATE_DIRECTORY
    assert (snapshot / "bundle.json").is_file()
    assert (
        (snapshot / "packs/flexible-electronics-fea/README.md")
        .read_text(encoding="utf-8")
        .startswith("# Flexible")
    )
    assert not (snapshot / "packs/flexible-electronics-fea/unlisted.md").exists()

    shutil.rmtree(source)
    validate_domain_knowledge_bundle(state, binding)
    with materialize_domain_knowledge_bundle(state, binding) as materialized:
        assert materialized is not None
        assert "flexible-electronics-fea" in (
            materialized.root / "_catalog.md"
        ).read_text(encoding="utf-8")
        assert (materialized.root / "flexible-electronics-fea/README.md").is_file()
        assert not (materialized.root / "flexible-electronics-fea/unlisted.md").exists()


def test_snapshot_corruption_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    _write_pack(root)
    workspace, state = _workspace_and_state(tmp_path)
    binding = prepare_domain_knowledge_bundle(
        ("flexible-electronics-fea",),
        state_dir=state,
        workspace=workspace,
        packs_root=root,
    )
    assert binding is not None
    file = state / "domain-knowledge/packs/flexible-electronics-fea/README.md"
    file.write_text("corrupt", encoding="utf-8")

    with pytest.raises(ValueError, match=r"corrupt|size changed"):
        validate_domain_knowledge_bundle(state, binding)


def test_same_release_with_changed_bytes_gets_a_new_digest(tmp_path: Path) -> None:
    root = tmp_path / "packs"
    pack = _write_pack(root)
    workspace1, state1 = _workspace_and_state(tmp_path / "one")
    first = prepare_domain_knowledge_bundle(
        ("flexible-electronics-fea",),
        state_dir=state1,
        workspace=workspace1,
        packs_root=root,
    )
    (pack / "README.md").write_text("changed", encoding="utf-8")
    workspace2, state2 = _workspace_and_state(tmp_path / "two")
    second = prepare_domain_knowledge_bundle(
        ("flexible-electronics-fea",),
        state_dir=state2,
        workspace=workspace2,
        packs_root=root,
    )

    assert first is not None
    assert second is not None
    assert first.packs[0].release == second.packs[0].release
    assert first.packs[0].pack_digest != second.packs[0].pack_digest


@pytest.mark.parametrize(
    "resource_path",
    ["../secret.md", "nested\\guide.md", "payload.exe", "pack.json"],
)
def test_manifest_rejects_unsafe_or_unsupported_resources(
    tmp_path: Path, resource_path: str
) -> None:
    root = tmp_path / "packs"
    pack = _write_pack(root)
    manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    manifest["entrypoints"] = [resource_path]
    manifest["resources"][0]["path"] = resource_path
    (pack / "pack.json").write_text(json.dumps(manifest), encoding="utf-8")
    workspace, state = _workspace_and_state(tmp_path)

    with pytest.raises(
        ValueError,
        match=r"knowledge paths|unsupported knowledge resource type|pack.json",
    ):
        prepare_domain_knowledge_bundle(
            ("flexible-electronics-fea",),
            state_dir=state,
            workspace=workspace,
            packs_root=root,
        )


def test_prepare_new_run_binds_selected_pack_to_launch_v7(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fixture")
    root = tmp_path / "packs"
    _write_pack(root)
    catalog = tmp_path / "api-catalog"
    ApiCatalog([], runtime_release="2024").write(catalog)
    workspace, state = _workspace_and_state(tmp_path)
    prepared = prepare_new_run(
        run_id="private-pack-run",
        goal="Use the selected private reference.",
        workspace=workspace,
        state_dir=state,
        settings=resolve_run_config(
            workspace,
            RunOptions(
                model_provider=DeepSeekGateway.OFFICIAL,
                model_name="deepseek-v4-flash",
                knowledge_pack_ids=("flexible-electronics-fea",),
            ),
        ),
        **CodingRunBudget(
            max_abaqus_calls=0,
            max_solver_calls=0,
        ).as_request_controls(),
        knowledge_packs_root=root,
    )

    assert prepared.launch.schema_version == "coding-run-launch-v11"
    assert prepared.launch.allows_response()
    assert prepared.launch.domain_knowledge is not None
    assert prepared.launch.domain_knowledge.packs[0].pack_id == (
        "flexible-electronics-fea"
    )
