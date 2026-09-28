# 领域仿真案例清单

经典解析题、厂商验证题和NAFEMS统一按物理能力组织；来源作为标签。
现有案例库共有20题，实现与既有结果保留。选题已确认：**5题入选、LE1–LE10共10题暂留评分、4题历史、LE11待定**，目前评分选择规模为15题。入选为均布载荷悬臂梁、圆轴扭转、厚壁圆筒、孔板、三向实体梁VM184；历史为均匀杆、分段杆、均匀平面应力板、端载悬臂梁。入选不代表已经通过当前配置的正式验收。
下面列出全部现有覆盖。v25运行器保留20题定义，默认运行这15题；显式选择的历史/未入选题不计分。自定义子集报告注明未覆盖完整评分集合，不能与默认15题混报。

| 分类 | 案例 | 来源 / 理论依据 |
|---|---|---|
| 轴向与面内拉伸 | 均匀杆（历史） | 项目自设；[Roylance轴向杆关系](https://eng.libretexts.org/Bookshelves/Mechanical_Engineering/Mechanics_of_Materials_%28Roylance%29/02%3A_Simple_Tensile_and_Shear_Structures/2.01%3A_Trusses) |
| 轴向与面内拉伸 | 分段杆（历史） | 项目自设；[Roylance轴向杆关系](https://eng.libretexts.org/Bookshelves/Mechanical_Engineering/Mechanics_of_Materials_%28Roylance%29/02%3A_Simple_Tensile_and_Shear_Structures/2.01%3A_Trusses) |
| 轴向与面内拉伸 | 均匀平面应力板（历史） | [项目自设](../../src/abaqus_sim_agent/evaluation/public_mechanics_cases.py)；均匀单轴应变与平面应力解析关系 |
| 轴向与面内拉伸 | 中心圆孔板（已入选） | [Ansys VM-WB-MECH-002，2024 R2](https://ansyshelp.ansys.com/public/Views/Secured/corp/v242/en/wb_vm/wbvt_VM-WB-MECH-002.html) |
| 轴向与面内拉伸 | 椭圆膜（暂留评分） | [NAFEMS LE1：平面应力椭圆膜](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le1.htm) |
| 梁、板、壳弯曲 | 端载悬臂梁（历史） | 项目自设；[LibreTexts悬臂梁挠度公式](https://eng.libretexts.org/Courses/Illinois_Institute_of_Technology/Building_Envelope_Inspection_and_Rehabilitation_Engineering/08%3A_Non-Destructive_Evaluation_and_Facade_Monitoring_for_Distress_Conditions/8.06%3A_Vibration_Analysis/8.6.01%3A_Computing_fundamental_natural_frequency_of_vibration_of_beams) |
| 梁、板、壳弯曲 | 均布载荷悬臂梁（已入选） | 项目自设；[LibreTexts悬臂梁挠度公式](https://eng.libretexts.org/Courses/Illinois_Institute_of_Technology/Building_Envelope_Inspection_and_Rehabilitation_Engineering/08%3A_Non-Destructive_Evaluation_and_Facade_Monitoring_for_Distress_Conditions/8.06%3A_Vibration_Analysis/8.6.01%3A_Computing_fundamental_natural_frequency_of_vibration_of_beams) |
| 梁、板、壳弯曲 | 三向实体悬臂梁（已入选） | [Ansys VM184，2025 R2](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/ans_vm/Hlp_V_VM184.html) |
| 梁、板、壳弯曲 | 圆柱壳弯曲patch test（暂留评分） | [NAFEMS LE2](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le2.htm) |
| 梁、板、壳弯曲 | 斜板法向受压（暂留评分） | [NAFEMS LE6](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le6.htm) |
| 梁、板、壳弯曲 | 厚板受压（暂留评分） | [NAFEMS LE10](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le10.htm) |
| 扭转 | 实心圆轴（已入选） | [项目自设](../../src/abaqus_sim_agent/evaluation/public_mechanics_cases.py)；Saint-Venant圆轴扭转关系 |
| 扭转 | Z形截面悬臂（暂留评分） | [NAFEMS LE5](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le5.htm) |
| 曲面壳点载荷 | 半球壳径向点载荷（暂留评分） | [NAFEMS LE3](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le3.htm) |
| 压力与轴对称 | 开口端厚壁圆筒（已入选） | [项目自设](../../src/abaqus_sim_agent/evaluation/public_mechanics_cases.py)；Lamé厚壁圆筒解 |
| 压力与轴对称 | 双曲壳内压（暂留评分） | [NAFEMS LE4](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le4.htm) |
| 压力与轴对称 | 圆柱—球冠壳内压（暂留评分） | [NAFEMS LE7](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le7.htm) |
| 压力与轴对称 | 球冠—圆环过渡—圆柱壳内压（暂留评分） | [NAFEMS LE8](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le8.htm) |
| 压力与轴对称 | 分支轴对称壳内压（暂留评分） | [NAFEMS LE9](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le9.htm) |
| 热弹性 | 圆柱/锥段/球面实体温度载荷（待定） | [NAFEMS LE11](https://docs.software.vt.edu/abaqusv2025/English/SIMACAEBMKRefMap/simabmk-c-le11.htm) |

**来源性质：7题为项目自设解析题，2题移植自Ansys，11题移植自NAFEMS。**自设题的参数、交付和判据由项目定义；引用理论不表示照搬了外部标准原题。

NAFEMS原始出处为[P18《The Standard NAFEMS Benchmarks》](https://www.nafems.org/publications/resource_center/p18/)，Abaqus逐题页注明TNSB Rev.3（1990）。
表中NAFEMS链接为Abaqus 2025文档的公开托管版本，本轮已核对可访问；它们是查阅入口，不替换旧批次绑定的2024/2026资料，也不证明Abaqus 2024运行兼容。

均匀平面应力板、圆轴和厚壁圆筒当前源码仅登记解析关系，没有绑定某个外部原题/教材页，因此明确标为项目自设。
厂商/NAFEMS题已适配为本项目的Agent任务，取数、容差、允许表示和交付仍以[项目合同](domain_mechanics_case_details.md)为准。
LE11是给定温度场的热弹性应力问题，不是传热求解。

## 计数与边界

VM184的三个工况必须全部合格，合计一道题，不拆成三份权重。按物理Case计数；同题输入变体不增加领域仿真总分的题数或权重。若单独做交互行为测评，另行报告。
旧plate/bar/B31 Gold及其独立驱动、专用验收入口、测试和报告已删除，不加入当前案例集。
仍被修改/修复与独立交互试点使用的参考schema、取数及种子合同继续由相应消费者持有，不构成Gold运行入口。

## 需要细查时

- [15道计分题已有结果](basic_linear_results_overview.md)：逐题简表；旧批次自动通过、失败、中断与事后结果分开记录。
- [合同与证据详表](domain_mechanics_case_details.md)：20题的参数、oracle、历史结果和运行入口；已同样按物理能力分组。
- [源码定义](../../src/abaqus_sim_agent/evaluation/public_mechanics_cases.py)：完整Case ID、goal和当前预算。
- [证据摘录](domain_mechanics_evidence_audit_20260927.json)：既有本机记录的摘要与来源hash，不是新成绩。

阅读结果时尤其注意：参考脚本通过不等于Agent通过；事后恢复不回填原批次；results-first自动通过不等于完整人工规格审查。
