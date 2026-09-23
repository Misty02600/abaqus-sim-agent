# Runtime feedback repair paired protocol v2

## Purpose

Measure whether optional BasedPyright feedback helps the same model repair known broken Abaqus scripts after the product
Catalog became mandatory. This protocol changes only the static preflight arm inside each pair.

## Fixed comparison

- Control: `static_preflight_mode=off`.
- Treatment: `static_preflight_mode=on` with the pinned abqpy wheel and BasedPyright cache.
- Both arms bind the same immutable API Catalog and use `automatic_verified_only`.
- The automatic Catalog mode exposes no lookup or discovery tool. Eligible runtime failures may receive the same verified
  note in both arms.
- Case, model, Provider, retry policy, prompt/Skill, runtime limits, oracle and repetition stay equal within a pair.
- Arm order reverses by repetition. Provider/infrastructure failures remain `unpaired_unknown`.

The runner seals the Catalog manifest, methods, optional members, wheel, cases and current Python source chain. A run whose
bindings change before completion is invalid. Existing v1 artifacts remain frozen with Catalog off and are not combined
with v2 results.

## Interpretation

A win requires control failure and treatment pass in the same case/repetition; a loss is the reverse. Ties do not prove
equal reasoning quality. Report static detections, model requests, tool calls, Abaqus calls, failed runtime calls, tokens,
wall time and final oracle separately. Small samples do not establish a general success rate.

Run with:

```powershell
uv run --group eval python -m abaqus_sim_agent.evaluation.runtime_feedback_repair `
  --run-root D:\abaqus-runs\runtime-feedback-repair-v2 `
  --api-catalog-dir D:\abaqus-data\api-catalog `
  --static-preflight-cache-dir D:\abaqus-runs\static-preflight-cache
```
