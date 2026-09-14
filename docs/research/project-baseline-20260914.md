# 2026-09-14 当前项目基线整理

用户授权将当前大量未提交内容整理为项目基线，再创建独立Web工作树。基线保存当前单Coder产品、测试、冻结评测证据、调研和维护计划；它不是把历史开发过程拆成追溯性的功能提交，也不表示FastAPI/Web产品已经实现。

## 保存与提交范围

- 整理前的941个非忽略文件约17.12 MiB已备份到本机 `D:/Projects/abaqus-agent/cleanup-backups/project-baseline-20260914-095736`，包含文件清单/hash、工作树zip、原tracked diff和index；原HEAD为 `9af5ba2`。
- 源码、测试、配置、项目文档及benchmarks中已冻结的证据纳入基线。`output/playwright`的9张截图均已在benchmarks有相同hash副本，因此保留原地但不重复提交。
- 现有Git忽略策略保留：`.venv`、`var`、依赖/运行缓存不入库，个人scratch/code-notes/tours继续按原有个人规则处理；不复制到新工作树。
- 凭据模式扫描仅命中测试中两处示例AWS key，核查为脱敏测试样本；未发现本轮扫描覆盖模式下的真实密钥。不将模式扫描描述成全面安全审计。

## 必要整理

- `.gitattributes`为benchmarks、tests/fixtures和docs/research/evidence关闭文本换行转换，保留hash绑定的原始字节；暂存区逐文件与工作树原始blob核对。
- Ruff及提交钩子排除冻结证据/个人scratch；生产src和测试代码仍完整检查，不用格式化改写历史样本。
- 提交钩子Python版本显式对齐项目3.12。首次默认3.13环境创建失败，固定版本后可运行；EOF钩子曾改动4个vendor fixture，已从备份恢复并逐hash核对，随后加入冻结fixture排除。
- 修正evaluation两个模块的4项类型推断问题：为Verdict、Completion、Termination的集合/映射补充精确类型，不改变评分逻辑。
- 格式化当前edit_scope/reproduction及相关测试，规范py.typed的结尾；不重写任何旧报告、trial、seal或fixture。

这些源码整理改变了实现文件hash。后续源绑定评测必须新seal；旧报告中的实现hash保留当时值，不能回填为当前基线。

## 验证

- 整理前全仓非持证测试：1078 passed、27 deselected。
- 类型/格式整理后再次全仓非持证测试：1078 passed、27 deselected，196.55秒；无真实Provider/Abaqus调用。
- 全仓Ruff check/format通过；BasedPyright 0 errors、0 warnings、0 notes。
- 提交钩子trailing whitespace、EOF、YAML、大文件、冲突标记、Ruff均通过。
- 所有冻结证据/fixture与整理前清单hash一致，未为了通过检查修改历史数据。

本机检查原始输出保存在 `var/baseline-pytest-final.xml`、`var/baseline-pytest-final.txt`、`var/baseline-types-after.txt`、`var/baseline-hooks-pass.txt`；清单和备份位置见 `var/audits/project-baseline-20260914/`。未运行持证测试，不新增物理闭环或模型成功率结论。

后续从该Git基线创建 `codex/web-workbench` 工作树，按PLAN-0009/0010实施，Web增量与本基线分开审查。此记录没有创建新的实施计划或改变现有优先级。
