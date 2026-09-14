# ODB成员目录：2024.10版本来源与工具送达验证

日期：2026-09-10。范围：API成员覆盖的显式小闭环，非新的 licensed Gold 或 Agent 收益实验。

## 结果

`OdbAssembly.nodeSets` 等成员现在可以通过现有 `lookup_abaqus_api` 查询到，前提是显式选择新的
成员目录并启用 full 模式。新增独立目录 `var/api-catalog/abaqus-2024-odb-members-v1`；原 final15
的 methods 与 manifest 均未改动，推荐 profile 和默认开关不变。

| 验证 | 结果 | 能证明什么 |
|---|---:|---|
| 6个显式 owner 的固定 wheel 成员提取 | 85条声明 | 已收集公开静态注解，不是全部动态属性 |
| 成员与邻近 method/parameter development 查询 | 14/14 | 精确解析、来源与预期状态匹配 |
| 同14条经过真实 Coder composition 的工具返回 | 14/14 | FunctionModel 驱动的本地工具链与 trace 可运行 |
| 旧 final15 / 新目录的方法基准 | 27/27、27/27 | 已有固定 method/parameter 基准无退化 |
| 全仓 pytest | 836 passed、27 skipped | 默认非 licensed 测试通过 |
| 修改范围 BasedPyright / Ruff | 0错误、通过 | 类型与静态规范检查通过 |

新增12项测试另覆盖继承遮蔽、C3菱形继承、未知/歧义owner、版本不符、未知基类、重复基类、条件类体、
侧文件hash校验、旧schema读取、CLI显式启用、预算与别名去重、trace、snapshot恢复，以及成员不生成
automatic runtime hint。最后补强后的成员测试独立重跑12/12。

14条查询包含9个成员：`Odb.rootAssembly`、`OdbAssembly.nodeSets`、`OdbInstance.nodeSets`、
`OdbAssembly.elementSets`、`OdbAssembly.instances`、`OdbSet.nodes`、`OdbSet.elements`、
`FieldOutput.values`、`FieldValue.data`；邻近方法与参数是 `FieldOutput.getSubset` 和
`FieldOutput.getSubset.region`；另外检查 bare `nodeSets` 的 ambiguous、大小写错误和虚构成员的
not_found。原 final15 对9个成员均为 not_found，新目录均为 found。

## 来源与边界

- 固定源：`abqpy-2024.10-py3-none-any.whl`，SHA-256
  `bdfe99139281489354c01f00b7ee8b96ee1d281b461441dcfe7fad5fda894504`。
- 只静态读取 AST，不导入、安装或执行 abqpy。METADATA 的包名/版本与调用参数必须相符；声明 release
  必须匹配 wheel 年份。不得把2026来源重新标注为2024。
- 新成员只来自2024.10 wheel。原 final15 方法仍保留其原有2026 FD02官方语义、2024.10 stub与2024
  runtime note来源；这次没有把2026方法语义升级为2024兼容证明。
- 6个 owner：`Odb`、`OdbAssembly`、`OdbInstance`、`OdbSet`、`FieldOutput`、`FieldValue`。
  `OdbAssembly.nodeSets` 的实际声明是 `OdbAssemblyBase.nodeSets`；`Odb.rootAssembly` 来自
  `OdbBase.rootAssembly`；`OdbInstance.nodeSets` 来自 `OdbInstanceBase.nodeSets`。
- `ApiMemberCard` 返回公开名、声明名、注解、源码注释、源文件/行范围、原文件bytes hash与wheel hash。
  不返回stub占位值作为runtime默认值；`dict[...]` 等注解不保证真实Repository是Python dict。
  模型工具正文最多1800字符并显式标注截断与静态证据限制。
- 简名出现多个成员时返回 ambiguous；not_found 仅表示当前目录没有精确记录。没有调用模型选择首个
  候选，也没有把静态成员自动变成错误修复规则。
- 原 methods SHA-256仍为
  `2dcf85685a011e149ae00828fd4988985a400fd1fde76c0b24b4964ac6f7f5c7`；members sidecar SHA-256为
  `1ef3516e715c547198a7ffbea842161f7026e27e5b025f76cee38ef9d77c96f2`。
  新schema v2与run/resume binding共同绑定成员数量和digest，旧schema v1读取兼容。

## 使用方式

已有本地产物可只读查询：

```powershell
uv run python -m abaqus_sim_agent.api_catalog lookup --catalog-dir var/api-catalog/abaqus-2024-odb-members-v1 OdbAssembly.nodeSets
```

产品调用仍需显式提供 `--api-catalog-dir var/api-catalog/abaqus-2024-odb-members-v1 --api-catalog-mode full`。
只选择旧 final15 不会自动拥有新增成员；`automatic_verified_only` 不返回这些静态成员。

新建其他独立目录时，现有 build CLI 可重复传入 `--abqpy-member-owner OdbAssembly` 等 owner；必须
同时提供 `--abqpy-source` wheel、`--abqpy-version` 和新 `--output-dir`。不提供该选项维持旧方法构建。
直接以 wheel 构建的目录不会自动继承 final15 的官方方法语义或runtime notes；本次独立目录是保留
final15 cards、附加成员的明确组合，不能把两种构建结果混为一谈。

## 证据与限制

机器摘要见[同名JSON](odb_member_catalog_v1_20260910.json)。原始本地记录在
`var/audits/odb-member-catalog-v1/`：`lookups.json`、`tool_returns.json`、`method_regression.json`、
`state/api_lookups.jsonl`。构建/验证脚本为 `var/audits/validate_odb_member_catalog_v1.py`。
这些本地私有来源与派生产物未作为包资源或第三方源码加入发布物。

11次found查询计入API预算，ambiguous与not_found不计入；0真实Provider请求、0 Abaqus、0 Solver、
无workspace写入、无新增runtime note。FunctionModel的查询由测试指定，因此不能据此声称模型能
自主识别API问题、选择正确入口或采用证据。没有修改Concept入口、默认索引、Riks候选召回或warning重排。

首轮脚本在所有查询、工具返回与trace已落盘后，汇总时误用了 `result.usage().requests`，正确接口为
`result.usage.requests`。仅修正离线脚本并从已落盘证据一次性核对汇总，没有重跑模型或工具；原usage
请求数未持久化，机器摘要保留null，不把脚本预期15次写成实测。该恢复与后续纯格式化/测试补强分别
保留在机器摘要，不是新实验或held-out。
