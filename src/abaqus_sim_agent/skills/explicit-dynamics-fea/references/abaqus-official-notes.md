# Abaqus official references: Explicit dynamics

Source baseline: local Abaqus 2026 FD02 Explicit-analysis, section-control, and output-variable pages. These files summarize product semantics, not Abaqus 2024 API compatibility.

Load only the relevant topic:

- [explicit-time-integration-and-stability.md](explicit-time-integration-and-stability.md) — central-difference integration, stable time increment, wave speed, increment controls, bulk viscosity, and Explicit diagnostics.
- [element-controls-and-energy-diagnostics.md](element-controls-and-energy-diagnostics.md) — hourglass control, distortion control, `ALLAE`/`ALLDC`, and energy histories as diagnostics rather than universal acceptance thresholds.
- [mass-scaling-and-quasi-static.md](mass-scaling-and-quasi-static.md) — fixed/variable mass scaling, target increments, contact mass scaling, quasi-static use, and inertia-sensitivity checks.

The time-integration file is the main reference for genuinely dynamic Explicit procedure behavior; the mass-scaling file is the main reference for `quasi-static-explicit-fea`. Load the element-controls file only when the discretization or energy diagnostics make it relevant.
