import pytest

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.api_catalog.bundled import bundled_catalog_path
from abaqus_sim_agent.coding.api_lookup import bind_api_catalog
from abaqus_sim_agent.coding.application import prepare_new_run
from abaqus_sim_agent.coding.options import resolve_run_config


@pytest.fixture
def product_catalog_path():
    return bundled_catalog_path()


def test_bundled_catalog_contains_method_and_member_evidence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    catalog = ApiCatalog.open(bundled_catalog_path())

    assert catalog.lookup("Part.Set").status == "found"
    assert catalog.members
    member = catalog.lookup(catalog.members[0].canonical_name)
    assert member.status == "found"
    assert member.members
    assert catalog.manifest is not None
    assert catalog.manifest.official_release == "2026 FD02"
    assert catalog.manifest.abqpy_version == "2024.10"
    assert catalog.runtime_release == "2024"
    assert not list(tmp_path.iterdir())


def test_product_defaults_bind_bundled_catalog_without_path_configuration(
    monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "workspace").mkdir()
    prepared = prepare_new_run(
        run_id="bundled-catalog",
        goal="test",
        workspace=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        settings=resolve_run_config(tmp_path / "workspace"),
    )

    assert prepared.launch.api_catalog_dir == bundled_catalog_path()
    assert prepared.launch.api_catalog_mode == "automatic_verified_only"
    assert prepared.request.api_catalog == bind_api_catalog(
        ApiCatalog.open(bundled_catalog_path())
    )
    assert not (tmp_path / "api-catalog").exists()
    assert not (tmp_path / "workspace" / ".abaqus-sim-agent").exists()
