import hashlib
import json

import pytest

from abaqus_sim_agent.skills.import_official_example import export_example


def _page(tmp_path, code='"""beamExample.py"""\nx = 1 &lt; 2\n'):
    page = tmp_path / "example.htm"
    page.write_text(
        '<html><head><meta name="DC.identifier" content="simacmd-c-intexabeamscript"/>'
        '</head><body><pre class="codeblock">' + code + "</pre></body></html>",
        encoding="utf-8",
    )
    return page


def test_import_preserves_source_and_extracts_text_without_execution(tmp_path):
    page = _page(tmp_path)
    original = page.read_bytes()
    output = tmp_path / "export"
    result = export_example(page, output, source_release="fixture")
    assert (
        output / "beamExample.py"
    ).read_text() == '"""beamExample.py"""\nx = 1 < 2\n'
    assert (output / "source.htm").read_bytes() == page.read_bytes() == original
    assert result["abaqus_executed"] is False
    assert result["source_sha256"] == hashlib.sha256(original).hexdigest()
    manifest = json.loads((output / "source.json").read_text(encoding="utf-8"))
    for name, digest in manifest["files"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError):
        export_example(page, output, source_release="fixture")
    assert (output / "source.htm").read_bytes() == original


@pytest.mark.parametrize(
    "code",
    [
        "# no matching block",
        '"""beamExample.py"""\nx = (',
        '"""beamExample.py"""</pre><pre class="codeblock">"""beamExample.py"""',
    ],
)
def test_invalid_example_does_not_create_output(tmp_path, code):
    page = _page(tmp_path, code)
    output = tmp_path / "export"
    with pytest.raises((ValueError, SyntaxError)):
        export_example(page, output, source_release="fixture")
    assert not output.exists()


@pytest.mark.parametrize("wrong_page", [True, False])
def test_source_identity_and_release_are_required(tmp_path, wrong_page):
    page = _page(tmp_path)
    if wrong_page:
        page.write_text(page.read_text().replace("simacmd-c-intexabeamscript", "other"))
    with pytest.raises(ValueError, match=r"expected|source_release"):
        export_example(
            page, tmp_path / "export", source_release="fixture" if wrong_page else " "
        )
    assert not (tmp_path / "export").exists()
