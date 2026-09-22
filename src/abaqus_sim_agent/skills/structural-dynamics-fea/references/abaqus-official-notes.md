# Abaqus official references: structural dynamics

Source baseline: local Abaqus 2026 FD02 dynamic-procedure documentation. These files summarize product semantics, not Abaqus 2024 API compatibility.

Load only the reference matching the unresolved procedure:

- [procedure-selection-and-damping.md](procedure-selection-and-damping.md) — implicit versus explicit, direct versus modal procedures, damping families, and procedure-level routing.
- [natural-frequency-extraction.md](natural-frequency-extraction.md) — eigensolvers, prestressed/base-state semantics, normalization, modal participation/effective mass, residual modes, and frequency-result interpretation.
- [modal-transient-dynamics.md](modal-transient-dynamics.md) — retained modes, damping, primary/secondary base motion, excitation sampling, load projection, and transient modal response.
- [direct-implicit-dynamics.md](direct-implicit-dynamics.md) — direct integration in Abaqus/Standard, time resolution, mass diagnostics, nonlinear response, and subspace projection.

The package examples remain in [official-examples.md](official-examples.md).
