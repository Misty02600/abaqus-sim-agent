# Abaqus official references: classical metal plasticity

Source baseline: local Abaqus 2026 FD02 `SIMACAEMATRefMap/simamat-c-metalplastic.htm` (*Classical Metal Plasticity*). These files summarize product semantics, not Abaqus 2024 API compatibility.

Load only the topic needed by the task:

- [yield-flow-and-hardening.md](yield-flow-and-hardening.md) — Mises/Hill yield surfaces, associated flow, perfect/isotropic/kinematic hardening, and loading-path implications.
- [stress-strain-data-preprocessing.md](stress-strain-data-preprocessing.md) — nominal/engineering uniaxial test data to true stress and logarithmic plastic strain, including the zero-plastic-strain first point and post-necking limitation.
- [material-data-rate-and-coupling.md](material-data-rate-and-coupling.md) — plastic input-data meaning, rate dependence, plastic-work heating, outputs, and procedure coupling.

Progressive damage, dynamic failure, fracture, and forming-limit behavior appear on the same official page but remain outside this Pack's current classical-plasticity scope unless a later task creates a dedicated boundary.
