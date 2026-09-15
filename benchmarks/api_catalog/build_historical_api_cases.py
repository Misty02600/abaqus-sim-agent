"""编译八个有来源的失败反馈查证案例，不执行历史代码或生成模型。"""

import argparse
import hashlib
import json
from pathlib import Path

from extract_historical_api_context import extract

from abaqus_sim_agent.api_catalog import ApiCatalog

PROJECT = Path(__file__).resolve().parents[2]
DESIGNS = {
    "box": (
        "只核实长方体几何创建这一处错误尝试应如何替换，保持原100×10×10几何目标；不要处理后续网格/求解。",
        [
            ["Part.BaseSolidExtrude", "Part.BaseSolidRevolve", "Part.BaseSolidSweep"],
            ["Model.ConstrainedSketch"],
        ],
        "给出合法几何路径与来源，不猜另一个Box便利方法；接受有证据且满足相同几何的替代。",
    ),
    "global_seed": (
        "只核实脚本中全局按尺寸播种的正确入口，保留既有size=5.0及网格目标；不要换成按边数量，也不要实际生成网格。",
        [["Part.seedPart"]],
        "全局尺寸而非数量；保留给定参数，查证调用归属。",
    ),
    "beam_section": (
        "重点核实梁截面构造及其紧邻的profile引用、截面赋值步骤。保持矩形截面和材料设定，区分对象和仓库键；不要修复后面不相关的边界/作业代码。",
        [["Model.BeamSection"], ["Model.RectangularProfile"]],
        "合法梁截面构造，profile/material使用正确引用；识别网格前elements集合可能为空，允许有证据的几何区域方案。",
    ),
    "planar_hole": (
        "重点核实二维平面板开圆孔的几何步骤，保持已有外轮廓和圆的中心/半径，不升级为三维模型，不改网格方案。可以说明某个错误操作应直接删除而不是必须找一个替代方法。",
        [["Part.BaseShell"], ["ConstrainedSketch.CircleByCenterPerimeter"]],
        "区分草图对象与Part特征；已有闭合轮廓的处理要有证据，不能虚构草图cut/删除圆后声称开孔。",
    ),
    "instance_set": (
        "重点核实实例几何用于边界集合的构造归属，保持当前选面/顶点坐标和装配层用途，不改变几何或约束。",
        [["Assembly.Set"]],
        "区分PartInstance仓库与构造器；保持实例几何及native序列，不改成Part层边界对象。",
    ),
    "mesh_node_select": (
        "重点核实当前CAE网格节点选择及端部集合的正确方法，保留已有节点/网格和端点坐标；不是ODB读取，也不通过重新划分网格绕过。",
        [
            [
                "MeshNodeArray.getByBoundingBox",
                "MeshNodeArray.getClosest",
                "MeshNodeArray.getFromLabel",
            ],
            ["Assembly.Set"],
        ],
        "区分几何/CAE网格/ODB容器；接受合法节点选择路径并解释结果形状、容差/数量核查，不能把现有节点重新生成。",
    ),
    "beam_orientation": (
        "重点核实梁截面构造与梁方向设置是否属于同一个调用，并检查相关参数与对象归属。保持历史方案的截面、材料和声明方向，不改变载荷/网格。",
        [["Model.BeamSection"], ["Part.assignBeamSectionOrientation"]],
        "不再把orientation放入BeamSection；核实profile是名字及方向方法region/method/n1等，不新增物理设定。",
    ),
    "surface_traction": (
        "重点核实当前面牵引调用的参数和区域构造。保持general surface traction、均匀210MPa、全局+X、非随动及原右端作用区域；不改成Pressure或集中力，不无故改变已有正确参数。",
        [["Model.SurfaceTraction"], ["Assembly.Surface"]],
        "从实际断面检查follow/follower和Set/Surface；directionVector原本已为两点形式，不能机械地把它当错；提出方案而非执行。",
    ),
}


def build(output):
    source = json.loads(
        (
            PROJECT / "benchmarks/api_catalog/historical_api_case_sources_v1.json"
        ).read_bytes()
    )
    catalog = ApiCatalog.open(
        PROJECT / "var/api-catalog/abaqus-2024-reference-context-v2"
    )
    cases = []
    for row in source["cases"]:
        run = Path("D:/abaqus-runs") / row["run"]
        error = run / "state/executions" / row["execution"] / "stderr.bin"
        code, provenance = extract(run / "state/history.json", error)
        request = json.loads((run / "state/request.json").read_bytes())
        outcome_path = run / "state/outcome.json"
        events_path = run / "state/events.jsonl"
        outcome = json.loads(outcome_path.read_bytes()) if outcome_path.exists() else {}
        clarification_events = (
            [
                json.loads(line)["data"]
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "clarification"
            ]
            if events_path.exists()
            else []
        )
        focus, groups, review = DESIGNS[row["id"]]
        availability = {
            name: catalog.lookup(name).status for group in groups for name in group
        }
        assert all(
            any(availability[name] == "found" for name in group) for group in groups
        )
        seeds = {
            "historical_build.py": code.decode("utf-8"),
            "runtime_error.txt": error.read_bytes().decode(
                provenance["error_encoding"], errors="replace"
            ),
            "original_task.json": json.dumps(
                {
                    "goal": request["goal"],
                    "clarifications": outcome.get(
                        "clarifications", clarification_events
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
        }
        question = (
            "这是历史失败反馈后的离线API查证场景，不是新的仿真执行任务。请先读original_task.json、historical_build.py和runtime_error.txt。历史脚本是待核实的错误尝试，不是所有语句都正确；原任务和已确认输入优先。"
            + focus
            + " 允许提出不同API或删除多余错误步骤，但要实际查证并说明依据。不要修改这三个证据文件，不运行任何脚本或Abaqus。"
        )
        cases.append(
            {
                "id": "history_" + row["id"],
                "question": question,
                "seed_files": seeds,
                "source_run": row["run"],
                "provenance": {
                    **provenance,
                    "request_sha256": hashlib.sha256(
                        (run / "state/request.json").read_bytes()
                    ).hexdigest(),
                    "outcome_sha256": hashlib.sha256(
                        outcome_path.read_bytes()
                    ).hexdigest()
                    if outcome_path.exists()
                    else None,
                    "events_sha256": hashlib.sha256(
                        events_path.read_bytes()
                    ).hexdigest()
                    if events_path.exists()
                    else None,
                },
                "expected": [],
                "expected_groups": groups,
                "availability": availability,
                "review": review,
                "scope": "historical_failure_feedback_development",
                "alternatives": "Groups are declared reference routes, not exhaustive correctness rules. Other evidenced valid solutions may pass manual review.",
            }
        )
    with output.open("x", encoding="utf-8") as f:
        json.dump(
            {
                "version": "historical-api-feedback-v1",
                "cases": cases,
                "builder_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
                "extractor_sha256": hashlib.sha256(
                    (
                        PROJECT
                        / "benchmarks/api_catalog/extract_historical_api_context.py"
                    ).read_bytes()
                ).hexdigest(),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print("built", len(cases), "cases", hashlib.sha256(output.read_bytes()).hexdigest())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    build(parser.parse_args().output)
