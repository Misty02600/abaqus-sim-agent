"""从本机官方页面保留悬臂梁代码；不导入或执行示例，不分发厂商代码。"""

import argparse
import ast
import hashlib
import json
from pathlib import Path

from bs4 import BeautifulSoup


def export_example(
    page: Path, output: Path, *, source_release: str
) -> dict[str, object]:
    """从指定官方 HTML 页导出独立的本地参考目录。

    Args:
        page: 悬臂梁脚本的官方页面。
        output: 必须不存在的新目录，避免覆盖旧资料及实验绑定。
        source_release: 宿主提供的来源版本标签，不等于运行兼容声明。

    Returns:
        来源及导出文件的 SHA-256 清单。

    Raises:
        ValueError: 页面身份、脚本块或来源版本缺失/不符。
        SyntaxError: 提取代码不能通过 Python 3.10 语法解析。
        FileExistsError: 输出目录已存在。
    """
    if not source_release.strip():
        raise ValueError("source_release is required")
    raw = page.read_bytes()
    tree = BeautifulSoup(raw, "html.parser", from_encoding="utf-8")
    identifiers = [
        node.get("content") for node in tree.select('meta[name="DC.identifier"]')
    ]
    if identifiers != ["simacmd-c-intexabeamscript"]:
        raise ValueError("expected the official cantilever beam script page")
    blocks = [
        node.get_text()
        for node in tree.select("pre.codeblock")
        if "beamExample.py" in node.get_text()
    ]
    if len(blocks) != 1:
        raise ValueError("expected exactly one beamExample.py code block")
    code = blocks[0]
    ast.parse(code, filename="beamExample.py", feature_version=(3, 10))
    guide = (
        Path(__file__).parent / "abaqus-2024-coding/references/official-beam-example.md"
    ).read_bytes()
    files = {
        "beamExample.py": code.encode("utf-8"),
        "beam-example.md": guide,
        "source.htm": raw,
        "README.md": (
            "# 官方悬臂梁脚本参考\n\n"
            f"来源版本（宿主标签）：{source_release}\n\n"
            "需要参考实体建模、实例网格或压力区域时，先读 beam-example.md，"
            "再按需读 beamExample.py。原代码仅提取，没有执行、清洗或适配。\n\n"
            "该示例包含 GUI、旧兼容开关和脚本内提交作业；不能直接作为本项目运行脚本。"
            "只有静态 Python 3.10 语法检查，不代表 Abaqus 2024 兼容或物理验收。\n"
        ).encode(),
    }
    manifest: dict[str, object] = {
        "source_path": str(page.resolve()),
        "source_release": source_release,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "extraction": "single HTML pre codeblock text; no code edits",
        "python310_syntax": True,
        "abaqus_executed": False,
        "rights": "Vendor material retained locally; redistribution not established.",
        "files": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        (output / name).write_bytes(data)
    (output / "source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-release", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            export_example(args.page, args.output, source_release=args.source_release),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
