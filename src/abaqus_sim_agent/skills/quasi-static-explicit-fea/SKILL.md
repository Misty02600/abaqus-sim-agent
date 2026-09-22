---
name: quasi-static-explicit-fea
description: "Model or review quasi-static analyses solved with explicit time integration: loading-rate design, mass scaling, stable increment, kinetic/internal energy interpretation, and inertia-sensitivity checks. Use when Explicit is a numerical route to a nominally static problem."
---

# Quasi-static Explicit FEA

Use this skill when the physical problem is nominally static but explicit time integration is chosen because of severe contact, deformation, instability, or solver robustness.

## Preserve the quasi-static claim

The artificial event duration and any mass scaling change the dynamic time scale. They are acceptable only if the requested structural response remains insensitive to the introduced inertia.

- Apply loading smoothly enough to avoid unnecessary stress waves.
- Inspect kinetic and internal/work energy histories in the context of the actual problem; no single energy percentage is a universal acceptance threshold.
- Repeat with a slower loading rate or reduced mass scaling when the quasi-static interpretation is material to the claim.
- Inspect deformation mode and reaction/load response, not just an energy ratio.

## Treat mass scaling as a modeling intervention

First determine whether a tiny stable increment comes from necessary physics or from avoidable mesh/pathology. Added mass can change inertia, gravity response, contact timing, and local deformation, so record where and how much mass is added and check sensitivity of the QoI.

## Route genuinely dynamic events separately

Use `explicit-dynamics-fea` for impact, wave propagation, drop, or other inertia-dominated events where kinetic energy and transient propagation are intended physics.

Read the shared theory reference [../explicit-dynamics-fea/references/modeling-principles.md](../explicit-dynamics-fea/references/modeling-principles.md) when the unresolved issue is time-scale separation, wave speed, conditional stability, or why mass scaling can change the quasi-static response.

Read the shared package reference [../explicit-dynamics-fea/references/mass-scaling-and-quasi-static.md](../explicit-dynamics-fea/references/mass-scaling-and-quasi-static.md) for Abaqus 2026 FD02 mass-scaling and quasi-static semantics. Load `explicit-time-integration-and-stability.md` only when the unresolved issue is the underlying stable increment or Explicit time integration. These references do not establish Abaqus 2024 runtime compatibility.
