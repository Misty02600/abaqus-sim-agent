# PLAN-0008：assistant-ui 接线资格与官方 UI 同组比较

| 状态 | 最后更新 |
|---|---|
| 已完成：有界接线、静态构建和确定性浏览器比较通过；不代表产品UI或仿真能力已实现 | 2026-09-14 |

## 问题与最终结果

用户先认可FastAPI + assistant-ui方案并要求入计划，随后要求补充调研、实际比较，并明确纳入Pydantic官方UI。最终比较官方ai-chat-ui 2.1.0最小定制、assistant-ui ExternalStore、assistant-ui AG-UI三条接线，全部保留同一CodingRunService/Harness/问答/verifier，不调用真实模型或Abaqus。

三条最终都通过了既定接线场景。保留[ADR-0025](../../adr/0025-local-web-workbench-over-coding-run.md)的ExternalStore主线，理由是项目可通过组件库公开接口拥有任务语义，不需要维护上游应用核心交互fork；不能声称官方UI不可用、ExternalStore总成本最低或AG-UI没有成熟能力。完整[比较报告](../../research/ui-three-way-comparison-20260913.md)拥有方法、差异、失败与局限。

## 最终改动与复用边界

- 新增隔离驱动 `benchmarks/coding/ui_comparison_probe.py`；真实service/submit_answer/verifier配FunctionModel与禁止进程runner，REST/AG-UI委托同一宿主命令。
- 官方fork保留布局、主题、文本/工具渲染，替换会话连接与本地历史为宿主API；改7个上游文件、新增2个宿主文件。源码来源commit、MIT许可证及patch保留。
- ExternalStore和AG-UI共用同一React页面。ExternalStore直接读取宿主快照；AG-UI使用真实HttpAgent、interrupt hooks与history投影，答复走原生resume而不是REST答案接口。
- Python与前端依赖仅在`var/ui-comparison-20260913`隔离安装；产品src/pyproject.toml/uv.lock未改。源码、锁、静态检查结果、原始宿主记录和浏览器截图已归档。
- 修正共用夹具对产品文件的观察方式：活动段显示最后settled证据，宿主结束后刷新。没有修补产品持久化代码。

## 验证结果

| 原验收项 | 结果与证据 |
|---|---|
| 复用/适配/新增清单 | 完成三条同组比较，明确官方核心fork、ExternalStore快照与AG-UI历史/interrupt接线的区别。 |
| registered/running/waiting/completed/failed展示 | 实际页面覆盖，问题待答/已答与宿主结果分开，不从模型文本判定成功。 |
| 工具与问题 | 实际ask_user/write_file卡片、原question ID答复；Abaqus工具仅校对schema，不假称实际调用。 |
| 刷新、独立浏览器、任务切换 | 通过；所有任务使用宿主ID，浏览器不回写权威history。 |
| 运行/terminal禁止操作 | UI不提供Retry/Edit/Fork/Cancel；非法命令4xx，冲突答案409。 |
| 正负真宿主夹具 | 每条PASS/FAIL/SLOW/RESTART四任务，共12任务；9个预期completed、3个预期failed，每个3次假模型、1次write_file、1个问题。 |
| 重放与连接 | 精确答案重放不改变outcome或增加执行；离开页面后宿主完成；三条均在待答后重启后端并同run继续。 |
| schema和范围 | 12份工具schema摘要一致，原三个Abaqus工具/ask_user仍在，无run_command或新增前端模型工具；36次最终假模型、0真实Provider、0Abaqus/Solver。 |
| 构建与检查 | 官方TypeScript/Vite和改动范围ESLint通过；assistant-ui typecheck/build、9项headless检查通过；实验驱动Ruff通过。 |
| 证据与资源 | 所有21份开发/最终run记录、57次假模型调用保留，未删除失败；实验服务和专用浏览器已关闭。 |

[结构化摘要](../../../benchmarks/coding/ui_comparison_20260913/comparison-summary.json)列出最终子集、版本、schema与artifact hash；其余开发记录不回填为最终通过。两个读写干扰中断样本均保存，见报告。原始官方UI实验也保留，不能与定制版混成一项结果。

## 已知缺口与后续

- 该资格只覆盖文件交付和UI接线，不是完整产品、自然模型成功率、物理验收或外部Job恢复。
- 原型没有完整创建幂等、安全产物服务、跨进程协调、逐token输出、完整stream replay或真实Abaqus日志/ODB视图。
- 官方版保留较多富文本功能，assistant-ui夹具未实现同等富文本，不做性能/bundle/整体视觉成熟度排名。
- AG-UI仍使用共用GET/SSE刷新宿主状态；本次未证明它减少此类代码。其pending history使用0.0.59的metadata约定，升级要回归。
- Windows普通读取可与原子replace冲突，FILE_SHARE_DELETE尝试也未解决本机复现；最终活动段缓存避免直接读取。正式API必须独立验证观察者不干扰写入。
- 下一步是[PLAN-0009：任务API](0009-coding-run-web-api.md)，之后[PLAN-0010：工作台集成](../todo/0010-local-web-workbench.md)。两项未实施，9–15工程日仍是未校准粗估。
- [PLAN-0006](../todo/0006-domain-task-evaluation-baseline.md)及其真实运行授权/历史保持独立，本次没有消费它的授权或重跑模型/Abaqus。
