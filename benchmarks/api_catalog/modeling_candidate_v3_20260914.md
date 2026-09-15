# 板、杆建模Catalog第一批：覆盖审计与候选

日期：2026-09-14。状态：第一批内容与构建工作已完成，离线候选；没有新的模型或Abaqus运行，也没有修改默认Catalog。后续意图发现、脚本指南读取和首次编码任务验证尚未实施。

## 交付与结果

范围依据为板参考建模脚本、三维实体region fixture和三维杆公开任务要求，覆盖建模至CAE/INP生成。不是全部Abaqus API审计，也没有将代码清单设成Agent允许使用的方法白名单。

| 项目 | final15基线 | 本次候选v3 |
|---|---:|---:|
| 方法卡 | 5480 | 5224 |
| 成员卡 | 0 | 150 |
| runtime note种类 | 16 | 16，内容完全保留 |
| 原始来源冲突记录 | 48 | 47，继续显式保留 |
| 核心目录检查 | 29/61 | 61/61 |
| 原样27题lookup回归 | 27/27（历史基线） | 26/27 |
| 修正Python来源名称后的27题回归 | 不回填 | 27/27 |

61项由29项方法签名/官方来源检查、25项核心成员检查、3项factory返回对象检查和4项错误名称/歧义检查组成。它是预先列出的开发范围检查，不是通用API正确率。150个成员来自五个owner的静态抽取，核心审计涉及其中25个；不声称所有新增成员都经过运行验证。

验证结果：全仓离线1082项通过、27项持证测试未运行（199.57秒）；针对Catalog的39项回归通过。修改的解析器通过BasedPyright，解析器、回归与审计脚本通过Ruff。JUnit位于上述审计目录的`offline-tests.xml`；本轮没有修改运行脚本、调用模型或运行Abaqus。

候选位于本机`var/api-catalog/abaqus-2024-modeling-candidate-v3`。v1为仅新增成员的中间构建，方法bytes与final15相同；v2为语言过滤的中间构建；后续试验只使用v3。详细审计在本机`var/audits/catalog-modeling-coverage-20260914/coverage-audit.json`，包含逐项前后结果、卡片字段差异、记录增删及来源hash。

## 缺口清单

| 缺口 | 处理与依据 | 状态 |
|---|---|---|
| Part/PartInstance几何、网格成员和Model/Assembly/Mdb仓库关系未进入final15 | 复用`load_abqpy_member_cards`与已有CLI选项，从固定abqpy 2024.10静态提取；包含faces、cells、elements、rootAssembly、instances等 | 已补150条声明，核心25项检查通过 |
| `Mdb.Model`、`Mdb.Job`、`Material.Elastic`有签名但缺官方语义 | 修正官方Path到实际直接receiver的映射；factory路径现在与已有abqpy方法合并，返回对象分别为Model、ModelJob、Elastic | 已修正提取与合并入口 |
| 一个Path代码块中的多条路径被压成一条 | 保留行边界后分别推导；嵌套对象方法不再因包含models/parts就被误归到父对象 | 已修正并覆盖多行和嵌套反例 |
| C++页面复用pyconstructsect/pymethodsect标记，混入PythonCatalog | 根据cppaccess、cpppath、cppproto结构区分；365条被移除的官方记录全部来自C++参考，未丢失Python官方记录或任何abqpy记录 | 已排除混入，避免拿C++参数说明解释Python调用 |
| `Job.writeInput`查询不命中 | 官方与stub都说明`mdb.Job`返回ModelJob，正确查询为`ModelJob.writeInput`；没有为抽象Job伪造一个通用writeInput别名 | 已明确对象链，保留错误查询not_found与短名歧义 |
| 部分来源版本与重载差异仍在 | 见下表；不删除真实差异或把未验证行为标为verified | 保留并分类 |
| 不知道API名时的发现、脚本指南的过程说明 | 本轮补已有API内容与构建，尚未增加自然语言发现或指南读取工具 | 后续批次 |

所有成员标记为`abqpy_static_declaration`，保留wheel版本、文件/行号和hash。官方参考为2026 FD02，运行目标为2024；不能将“有官方来源”直接解释为已经证明2024运行兼容。

## 原回归为什么有一项变化

原`open-odb-node-array-nesting`题要求`declaring_name=Top level commands.openOdb`。该名称来自混入的C++页面。移除C++记录后，Python参考页的标题是`Odb commands`，因此正确来源变为`Odb commands.openOdb`。

原始`abaqus_api_queries.jsonl`保持不变，原样运行结果26/27完整记录。审计器另外构造一个只改这项来源名称期望的视图，27/27通过；查询名openOdb、其runtime note及其余26题的条件均没有放宽。依据是本地官方`SIMACAEKERRefMap/simaker-c-odbopenodbcommandpyc.htm`与旧C++对应页。

方法卡数量减少256，不表示丢了256个受支持Python方法。原始记录级审计确认：删除的官方记录全部为C++；所有abqpy记录与16条runtime note完整保留。卡片级还发生了factory正确合并及错误父对象名称修正，详细差异包含896张共同ID卡的字段变化、305个旧卡ID移除及49个新卡ID生成。

## 范围内仍保留的差异

| 卡片 | 差异 | 对首轮用法的影响 |
|---|---|---|
| Model.ConstrainedSketch / Model.Part | 官方复制构造分支参数未出现在对应abqpy factory签名中 | 当前范围使用新建几何分支；复制构造不在本批核心用法中，不将其合并成所有调用的必填参数 |
| FaceArray.findAt | 官方normal参数未在该abqpy签名显式列出 | 现有核心脚本使用坐标定位；normal相关2024行为仍需单独确认 |
| ElemType.__init__ | 官方2026的部分particle字段不在2024 stub中 | 当前CPS4R/C3D8R基础配置不使用这些字段，不能据此宣称它们在2024可用 |
| Part.generateMesh | 两个已有mixin重载分别比较产生参数差异记录 | 两种签名均保留；当前不传这些可选参数的用法不受阻。未为追求零冲突而删除记录 |

这五类卡片中的差异没有被静默抹平。61项目录检查不覆盖全部参数组合；任何后续使用超出上述基础用法时仍须按实际来源查证。

## 可重复构建与审计

使用项目`.venv`与现有锁文件。以下构建只读本地官方资料和wheel，不导入abqpy、不执行Abaqus。输出目录必须不存在，重复构建时换一个新的输出目录。

```powershell
uv run --no-sync python -m abaqus_sim_agent.api_catalog build `
  --runtime-release 2024 `
  --output-dir var/api-catalog/abaqus-2024-modeling-candidate-v3 `
  --docs-root D:/SIMULIA/Documentation/2026LE `
  --abqpy-source var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl `
  --abqpy-version 2024.10 `
  --abqpy-member-owner Part --abqpy-member-owner PartInstance `
  --abqpy-member-owner Assembly --abqpy-member-owner Model --abqpy-member-owner Mdb `
  --runtime-notes benchmarks/api_catalog/abaqus_2024_runtime_notes.jsonl

uv run --no-sync python benchmarks/api_catalog/audit_modeling_candidate.py `
  --base var/api-catalog/abaqus-2024-final15 `
  --candidate var/api-catalog/abaqus-2024-modeling-candidate-v3 `
  --output var/audits/catalog-modeling-coverage-20260914/coverage-audit.json
```

| 内容 | SHA-256 |
|---|---|
| 候选方法文件 | `db61d1af7d777fdf8fceaa60139e940f6172cf5ea0cb0b33359df04457d50ec9` |
| 候选成员文件 | `7f2b811dff9dcc5faf841fb2c7382c409c58ea29af142ce4989d13b779f441d0` |
| 候选manifest | `288184eadff7b2dedc33a6aeaff8f2a3df3691d9be7df57b469bb9e9598e31dc` |
| 核心检查定义 | `156d281e79920fdaa3a48de61eed66b97bbbdac1e38346bd31793abca81b623e` |
| abqpy 2024.10 wheel | `bdfe99139281489354c01f00b7ee8b96ee1d281b461441dcfe7fad5fda894504` |

manifest含构建时间，重建时其hash可变化；相同源码/资料下方法和成员内容摘要应可复核。来源树、runtime notes及旧查询基准摘要均与final15绑定一致。没有修改final15或uv.lock。正常产品默认仍不启用该候选，后续应由新配置显式选择并按新源码封存评测。

相关文件：[核心检查定义](modeling_core_queries_v1.json)、[审计入口](audit_modeling_candidate.py)、[解析器](../../src/abaqus_sim_agent/api_catalog/official.py)、[针对性回归](../../tests/test_api_catalog_official.py)。
