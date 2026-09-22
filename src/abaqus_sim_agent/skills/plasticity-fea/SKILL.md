---
name: plasticity-fea
description: "Model or review elastic-plastic structural problems: yield and hardening data semantics, load path, plastic strain, unloading, and localization checks. Use when irreversible material response controls the QoI; keep geometric nonlinearity and contact as separate mechanisms."
---

# Plasticity FEA

Use this skill when yielding and irreversible deformation materially affect the response. Plasticity is a **material nonlinearity**; it should not be collapsed into a generic nonlinear bucket together with large-deformation kinematics or contact.

## Interpret material data before entering it

- Establish the yield criterion and hardening law represented by the available data.
- Distinguish total strain, elastic strain, plastic strain, engineering measures, and true measures according to the constitutive model being used.
- Preserve the source and temperature/rate conditions of tabulated data. Do not manufacture a hardening curve from one yield-strength number.
- If cyclic loading, anisotropy, rate dependence, damage, or another mechanism matters, a simple monotonic isotropic hardening model is insufficient.

## Preserve the load path

Plastic response depends on history. Increment order, unloading/reloading, preload, contact engagement, and imposed displacement can change the state even when the final external load magnitude is the same.

Geometric nonlinearity is a separate choice. Enable it when deformed geometry materially changes equilibrium or kinematics; do not infer it merely because the material is plastic.

Read [references/modeling-principles.md](references/modeling-principles.md) when the unresolved issue is constitutive adequacy, path dependence, calibration range, localization, or the meaning of a mesh-sensitive plastic result. When measured tensile data are given as engineering/nominal stress-strain and must become a plastic hardening table, load [references/stress-strain-data-preprocessing.md](references/stress-strain-data-preprocessing.md). Use the other official topic references for Abaqus-specific model families and input semantics.

## Verification focus

Check reactions, the extent and location of yielded zones, equivalent plastic strain or another physically relevant internal variable, sensitivity to mesh where localization is possible, and whether the computed stress/strain path stays within the intended constitutive range. A mesh-dependent plastic hot spot should not be promoted to a converged material prediction without a suitable regularization or QoI definition.

## Official implementation reference

Use [references/abaqus-official-notes.md](references/abaqus-official-notes.md) as the official-reference index. Load `yield-flow-and-hardening.md` for constitutive-family/loading-path questions, `stress-strain-data-preprocessing.md` for nominal/engineering tensile-data conversion, and `material-data-rate-and-coupling.md` for broader table semantics, rate effects, or thermomechanical coupling. Read [references/official-examples.md](references/official-examples.md). The connecting-lug script is the canonical first example because it isolates a tabulated elastic-plastic material in a solid structural model. The lap-joint contact example is a secondary cross-reference because plasticity, frictional contact, and geometric nonlinearity are active together.
