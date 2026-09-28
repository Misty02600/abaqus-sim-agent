"""生成API类型、构建前端并复制随Python包发布的静态资源。"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.persistence import write_bytes_atomic
from abaqus_sim_agent.coding.preferences import RunPreferences
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig

root = Path(__file__).resolve().parents[1]
frontend = root / "webui"
with tempfile.TemporaryDirectory(prefix="abaqus-web-schema-") as temporary:
    catalog_dir = Path(temporary) / "api-catalog"
    ApiCatalog([], runtime_release="2024").write(catalog_dir)
    app = create_app(
        ServerConfig(
            root=root / "var" / "schema-only",
            overrides=RunPreferences(api_catalog_dir=catalog_dir),
        )
    )
    (frontend / "openapi.json").write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
npm = shutil.which("npm.cmd") or shutil.which("npm")
if npm is None:
    raise SystemExit("请安装Node.js 22+与npm")
for arguments in (
    ("ci", "--ignore-scripts", "--no-audit", "--no-fund"),
    ("run", "generate:api"),
    ("run", "build"),
):
    subprocess.run([npm, *arguments], cwd=frontend, check=True)
lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
notices = [
    "Third-party runtime dependencies\nVersions are bound by webui/package-lock.json.\n"
]
for relative, metadata in sorted(lock["packages"].items()):
    if not relative or metadata.get("dev"):
        continue
    package = frontend / relative
    info = json.loads((package / "package.json").read_text(encoding="utf-8"))
    notices.append(
        f"\n=== {info['name']} {info['version']} ({metadata.get('license', 'see package')}) ===\n"
    )
    licenses = sorted(
        p
        for p in package.iterdir()
        if p.is_file()
        and p.name.lower().startswith(("license", "licence", "copying", "notice"))
    )
    fallback = frontend / "licenses" / f"{info['name'].replace('/', '_')}.txt"
    if not licenses and fallback.exists():
        licenses = [fallback]
    if licenses:
        notices.extend(
            p.read_text(encoding="utf-8", errors="replace") for p in licenses
        )
    else:
        notices.append(
            f"Package declares {info.get('license')}; no license text is distributed in its tarball. Upstream: {info.get('repository')}\n"
        )
notices.extend(
    (
        "\n=== Pi Web presentational adaptations (MIT; see webui/THIRD_PARTY_REUSE.md) ===\n",
        (frontend / "third-party" / "pi-web-LICENSE").read_text(encoding="utf-8"),
    )
)
(frontend / "dist" / "THIRD_PARTY_NOTICES.txt").write_text(
    "\n".join(notices), encoding="utf-8", newline="\n"
)
destination = root / "src/abaqus_sim_agent/web_static"
destination.mkdir(exist_ok=True)
# Publish the index last; hashed assets are immutable and existing pages can finish loading.
for item in (frontend / "dist").iterdir():
    if item.name == "index.html":
        continue
    if item.is_dir():
        shutil.copytree(item, destination / item.name, dirs_exist_ok=True)
    else:
        shutil.copy2(item, destination / item.name)
write_bytes_atomic(
    destination / "index.html", (frontend / "dist/index.html").read_bytes()
)
print(f"Web assets: {destination}")
