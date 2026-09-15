# Full-task API efficiency benchmark v1

## 目标

这个评测回答一个很具体的问题：一个真实 Abaqus 任务会使用很多 API，但模型是否只在少数真正不确定的位置调用 Catalog，而不是把每个 API 都走一遍 `discover -> Exact`。

它不是新的 API 搜索 benchmark，也不要求模型先知道 API 名。`discover_abaqus_api` 只负责在“知道对象/工程操作，但不知道方法名”时给候选；只有 `lookup_abaqus_api` 的 Exact 结果算已读 API 证据。

## 第一批 Case

Case 固定在 `full_task_api_efficiency_cases_v1.json`。第一批 6 个任务直接复用现有 evaluator：

1. `plate_tension_missing_thickness`：从零二维板，包含真实缺参澄清和完整建模/后处理链。
2. `beam_tension_fully_specified`：从零梁，覆盖截面、方向、集合、网格和 ODB。
3. `verified_plate_add_center_hole`：已有模型的几何/拓扑与重网格修改。
4. `verified_plate_split_two_materials`：已有模型的 partition、材料、section/region 路径。
5. `verified_plate_displacement_to_traction`：已有模型的载荷/边界条件修改。
6. `verified_b31_tip_load_y`：已有梁模型的载荷修改与 ODB 读取合同。

这些任务里的 `Part.seedPart`、平面孔、Surface traction、ODB 读取等都只是整条任务链中的自然观察点，不单独改写成“请找某个 API”的题。

## 自动统计

观察脚本只读取现有 `CodingRunService` state，不调用模型或 Abaqus。它从 `api_lookups.jsonl`、`api_discoveries.jsonl`、`request.json` 和 `outcome.json` 统计：

- Exact 与 discovery 的尝试次数、实际共享预算计数；
- 重复查询次数；
- 两类工具返回 payload 的 UTF-8 字节数；
- Exact 真正交付的 Card、参数、runtime note 数量；
- discovery 候选与 Exact 已交付 Card 的集合重合；
- Catalog 总调用与现有 `max_api_lookup_calls` 的关系。

payload 使用字节数，不伪装成 tokenizer 精确 token 数。若以后确实需要 token 化，再单独加入固定 tokenizer 统计。

## 人工审查

每个 run 只补一份很小的 review JSON。人工看最终脚本和已有 runtime/verifier 证据，填写：

- `api_usage_points_total`：最终实现中实际 Abaqus API 使用点总数；
- `api_usage_points_catalog_assisted`：其中确实靠 Catalog 解决过不确定性的使用点数；
- 各主要阶段 `correct / incorrect / not_applicable / not_reviewed`；
- 每个 `lookup:N` / `discovery:N` 是 `useful / redundant / unclear`；
- 是否存在“最终 API 用错或明显可疑，但全过程没查”的 `missed_uncertainties`。

示例：

```json
{
  "case_id": "beam_tension_fully_specified",
  "run_id": "example-run",
  "api_usage_points_total": 24,
  "api_usage_points_catalog_assisted": 3,
  "stage_reviews": {
    "geometry": "correct",
    "material_section": "correct",
    "assembly_region": "correct",
    "load_bc": "correct",
    "mesh": "correct",
    "postprocess": "correct"
  },
  "query_reviews": {
    "lookup:1": "useful",
    "lookup:2": "useful",
    "discovery:1": "useful"
  },
  "missed_uncertainties": [],
  "notes": []
}
```

这里的 `24` 不由 AST/LSP 自动猜。自动识别“哪个 Python 调用属于 Abaqus API、一个调用算几个使用点”很容易把评测器做成另一套脆弱系统；第一版直接人工数一次更可靠。

## 运行清单

观察脚本接收一个批次 run manifest。路径相对 run manifest 所在目录解析：

```json
{
  "schema_version": "full-task-api-efficiency-runs-v1",
  "runs": [
    {
      "case_id": "beam_tension_fully_specified",
      "profile": "full_with_discovery",
      "state_dir": "../../var/runs/example-state",
      "review_path": "reviews/example-run.json"
    }
  ]
}
```

离线生成结果：

```powershell
uv run python benchmarks/api_catalog/observe_full_task_api_efficiency.py `
  --cases benchmarks/api_catalog/full_task_api_efficiency_cases_v1.json `
  --runs path/to/runs.json `
  --output path/to/observations.json `
  --markdown path/to/report.md
```

## 判断方式

第一批不预设“最多只能查 3 次/4 次”之类硬阈值。先看真实行为：

- 主 API 路径是否正确；
- 20~30 个 API 使用点里，有多少直接完成，有多少真的需要 Catalog；
- Catalog 查询是否大多有用；
- discovery 是否只出现在方法名确实未知的少数位置；
- 有没有重复/无效查询；
- 有没有错误 API 从头到尾都没触发查证。

理想报告类似：`24 个 API 使用点，21 个直接完成，3 个 Catalog-assisted；3 次 Exact + 1 次 discovery；4 次查询均有明确作用；0 次冗余；主路径正确；0 个漏查错误。`

## 实验顺序

1. 先用 `full_with_discovery` 跑这 6 个整任务各 1 次，观察真实查询分布；不改 prompt 和预算。
2. 只有看到明显过查、漏查或 discovery 使用异常时，才决定是否调提示词/预算。
3. 若需要判断 discovery 本身的净收益，再选最有信息量的 3~6 个 Case，与现有 `full` 做同任务配对；不要为了凑 A/B 一开始就把全部模型调用翻倍。

模型/Abaqus 正式批次属于后续显式实验；本 protocol、observer 和单元测试本身都是纯离线。
