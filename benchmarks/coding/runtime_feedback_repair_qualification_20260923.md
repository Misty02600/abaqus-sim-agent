# Runtime feedback repair v1 资格验证

2026-09-23。该记录只建立 paired pilot 的输入与工具资格，不包含真实模型 trial，也不证明静态反馈带来修复收益。

## 冻结输入

- Case manifest：`runtime_feedback_repair_cases_v1.jsonl`，SHA-256
  `49846b0a283c1e437fe16db1a3c7f32779b278b0338d0037e919e9ee043f513b`。
- Protocol：`runtime_feedback_repair_protocol_v1.md`，SHA-256
  `773aa36fb387b9078f58e4b70c62d9f6db3308779b72345d1ed32254d7d463bb`。
- Runner：`runtime_feedback_repair.py`，SHA-256
  `36e8dc3b422fc2bcb6cd0e2ed502d8eab769c99eeefdd581cbde9b6037b750f3`。
- 共享 repair evaluator：`api_repair.py`，SHA-256
  `d74dbbf91c55652724a84223a1f0a876239366aaa6a9add906738ffc777ce939`。
- 静态来源为固定 `abqpy-2024.10` wheel；引擎为 BasedPyright 1.39.10。

四个 Case 分成两个 dev、两个 holdout。每个 fixture hash 由 manifest 绑定，坏脚本和目标结果进入工作区，
修复答案不进入模型上下文。

## 静态资格

使用当前 `abqpy-runtime-feedback-v1` 检查原始坏脚本：

| Case | 原始静态结果 |
|---|---|
| `undefined_result_name` | `reportUndefinedVariable`，指出 `n_total_elements` 未定义 |
| `field_output_missing_step_import` | clean；静态分析没有发现其 runtime 错误 |
| `closed_stream_write` | clean；作为 runtime-only 负对照 |
| `assembly_set_scalar_edge` | `reportArgumentType`，指出单个 `Edge` 与 `Sequence[Edge]` 不匹配 |

这建立了两项静态命中和两项未命中，不把 clean 解释为脚本正确。对应离线测试为 10 passed、4 licensed
tests deselected。

## Abaqus 2024 坏样本基线

设置 `ABAQUS_LICENSED_TESTS=1` 后运行四个 fixture；4/4 均在 CAE noGUI 中非超时失败，错误输出包含
manifest 声明的错误词，且均未生成目标结果 JSON。测试耗时约 20 秒。该过程调用 Abaqus 2024，但没有
调用模型、Solver 或 oracle 外的可信脚本。

因此四个 Case 具备“原始脚本真实失败、目标产物不存在”的资格。正式 paired trial 仍须分别记录模型修复、
成功 CAE 生成结果和宿主 verifier；不能用本记录替代 trial 结果。

## 后续状态

资格验证后从 Windows 用户环境取得 OpenCode 凭据，小型 doctor 通过，并按 protocol 完成 16 个真实模型
paired trial；结果见 `runtime_feedback_repair_paired_v1_20260923.md`。本资格记录本身仍只说明输入与工具边界。
