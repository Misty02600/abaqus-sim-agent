# Abaqus official references: hyperelasticity

Source baseline: local Abaqus 2026 FD02 `SIMACAEMATRefMap/simamat-c-hyperelastic.htm` (*Hyperelastic Behavior of Rubberlike Materials*). These files summarize product semantics, not Abaqus 2024 API compatibility.

Load only the topic needed by the task:

- [constitutive-models-and-compressibility.md](constitutive-models-and-compressibility.md) — strain-energy families, isotropy, compressibility, and Standard/Explicit differences.
- [calibration-and-test-data.md](calibration-and-test-data.md) — homogeneous test modes, fitting, smoothing, prediction checks, stability, and calibration range.
- [element-formulation-and-procedures.md](element-formulation-and-procedures.md) — hybrid versus displacement formulations, element consequences of near-incompressibility, and analysis-procedure implications.

Finite-strain output conventions (`S`, `E`, `NE`, `LE`, ODB defaults, and experiment/FE strain-measure matching) are owned by `geometric-nonlinear-fea/references/finite-strain-output-measures.md` because they apply beyond hyperelastic constitutive models.

Thermal expansion, viscoelastic/hysteretic extensions, and user subroutines remain secondary topics until a task requires them; they are not silently folded into the basic hyperelastic model.
