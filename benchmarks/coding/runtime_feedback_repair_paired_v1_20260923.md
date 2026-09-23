# Runtime feedback repair paired pilot v1

2026-09-23。按 `runtime_feedback_repair_protocol_v1.md` 执行四个 Case、off/on 两臂、两次反转顺序，共
16 个独立 trial。批次位于 `D:/abaqus-runs/runtime-feedback-repair-v1-20260923`；manifest SHA-256 为
`d3be84928f7a2d1a7a9aa89d9fd0da18872dea38290b289fa36029d1c3dd0a1d`，report SHA-256 为
`bbae632508b4e3cd8077dda52abedd8621467799e50a0108c782097e8af731a3`。

本批标记为 `formal_batch=false` 的 exploratory pilot。批次 manifest 绑定了 evaluator、manifest、fixture
与 wheel，但没有绑定完整 Agent 调用链；同目录的编码任务在批次后段修改了 Web、launch/composition 和
测试文件。时间戳复核显示直接 repair runner 使用的 agent/runtime/service 文件在进程结束前没有落盘变化，
且 Python 进程在批次开始时已加载模块，但这不能替代完整 source seal。结果可用于决定不扩跑，不能作为
严格的 treatment 因果成绩或默认配置依据。

## 合同与有效性

- Provider/model：OpenCode Go / `deepseek-v4-flash`，HTTP retry 0；正式批次前的小型 doctor 通过。
- Catalog off；两臂都使用相同 Skill、Prompt、fixture、预算、Abaqus 2024 runner 和隐藏 verifier。
- treatment 绑定 BasedPyright 1.39.10 与 abqpy 2024.10；唯一 treatment 差异是静态反馈 on。
- 16 条均形成可评分 outcome；0 Provider/evaluator unknown，无补跑、resume 或 post-hoc 替换。
- 输入、runner、共享 evaluator、wheel 和 fixture hash 由批次 manifest 绑定，运行结束后重新核对未变化；
  完整 Agent 源码未被 seal，构成上述证据限制。

## 配对结果

| Case | repetition 1 | repetition 2 |
|---|---|---|
| `undefined_result_name` | pass / pass，tie | pass / pass，tie |
| `field_output_missing_step_import` | pass / pass，tie | pass / pass，tie |
| `closed_stream_write` | pass / pass，tie | pass / pass，tie |
| `assembly_set_scalar_edge` | fail / fail，tie | fail / pass，**win** |

合计为 **1 win / 7 tie / 0 loss / 0 unpaired unknown**。control 通过 6/8，treatment 通过 7/8。

## 成本

| 累计指标 | control off | treatment on | treatment 相对变化 |
|---|---:|---:|---:|
| model requests | 81 | 74 | -8.6% |
| tool calls | 89 | 83 | -6.7% |
| Abaqus calls | 18 | 16 | -11.1% |
| failed CAE calls | 6 | 5 | -16.7% |
| input tokens | 796,899 | 881,215 | +10.6% |
| output tokens | 41,067 | 50,644 | +23.3% |
| task wall 合计 | 723.4 s | 592.8 s | -18.1% |
| Abaqus runtime 合计 | 24.39 s | 21.37 s | -12.4% |

treatment 共运行 16 次静态分析，0 unavailable；只有 `assembly_set_scalar_edge` 的两次 treatment 在实际
执行前收到诊断，共返回 14 条。`undefined_result_name` 虽在离线资格中可被静态检查发现，但两次模型都在
首次执行前自行修好，因此正式轨迹没有投递该诊断。另两个静态未命中 Case 也没有诊断。

## 唯一 win 的解释

`assembly_set_scalar_edge` repetition 2 treatment 首次运行前收到：单个 `Edge` 不能传给要求
`Sequence[Edge]` 的 `Assembly.Set.edges`。模型随后通过两个诊断脚本查证，最终改为 nested `findAt`，在
15 次模型请求、4 次 Abaqus、1 次失败 CAE 后通过。相邻 control 尝试 tuple 与四个探针，16 次模型请求、
5 次 Abaqus、2 次失败 CAE 后因 runtime budget 失败。

该行为支持“静态反馈在这一次动态容器修复中进入了修复链路”，不证明它单独导致成功：同一 treatment 的
repetition 1 仍在 18 次模型请求和 5 次 Abaqus 后失败，且产生 3 次失败 CAE。两轮 treatment 的静态噪声
还包含 probe 对象的 attribute/type 告警。

## 结论

本批给出弱正向观察：没有 paired loss，模型请求和 vendor 调用下降，并出现一次 paired win；但收益只出现
在一个 Case 的一次重复，稳定性不足，同时 token 成本明显增加。普通名称错误没有在正式轨迹中实际触发
静态反馈，runtime-only Case 也无法用于说明静态收益。

因此当前观察支持保留静态反馈为 **默认 off 的可选诊断**，不支持改为默认 on 或宣称普遍提高修复效率。
由于 token 开销和重复不稳定，本批不自动扩跑完整 v6/v8 端到端任务；后续若继续，应先增加同一
`Assembly.Set` Case 的独立重复或寻找能稳定在执行前保留错误的非人工任务，而不是扩大物理题范围。
