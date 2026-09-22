---
name: modal-dynamics-fea
description: "Model or review eigenfrequency and modal-response tasks: base state, natural modes, mode shapes, modal truncation, participation, damping, and modal transient assumptions. Use when the task is fundamentally modal rather than direct time integration."
---

# Modal Dynamics FEA

Use this skill when the main question is natural frequencies, mode shapes, prestressed modes, or response represented in a retained modal basis.

## Define the modal base state

- Establish mass, stiffness, constraints, and the state about which the eigenproblem is linearized.
- If frequencies depend on preload or a preceding nonlinear configuration, preserve that step sequence explicitly.
- Inspect mode shapes as well as frequency values so rigid-body, local, or unintended support modes are not mistaken for useful structural modes.

## Treat modal reduction as an approximation

For modal transient response, the retained basis must cover the frequency content and deformation patterns needed by the excitation and QoI. Check truncation, participation/effective mass when relevant, damping assumptions, and convergence as the retained modal set grows.

Do not use a fixed modal basis as a generic substitute for a strongly nonlinear transient analysis whose stiffness, contact state, or material state changes materially during the event.

## Route direct dynamics separately

Use `structural-dynamics-fea` when the task requires direct implicit time integration or when choosing between direct and modal transient procedures. Use `explicit-dynamics-fea` for explicit time integration.

Read the shared theory reference [../structural-dynamics-fea/references/modeling-principles.md](../structural-dynamics-fea/references/modeling-principles.md) when the unresolved issue is the eigenproblem, base state, modal completeness, excitation bandwidth, or damping meaning.

Read [../structural-dynamics-fea/references/natural-frequency-extraction.md](../structural-dynamics-fea/references/natural-frequency-extraction.md) for eigenfrequency/basis questions and [../structural-dynamics-fea/references/modal-transient-dynamics.md](../structural-dynamics-fea/references/modal-transient-dynamics.md) for retained-mode response. Use `procedure-selection-and-damping.md` when procedure routing or damping families are unresolved. [../structural-dynamics-fea/references/official-examples.md](../structural-dynamics-fea/references/official-examples.md) provides the curated official examples.
