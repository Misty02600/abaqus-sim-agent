---
name: explicit-dynamics-fea
description: "Model or review genuinely dynamic explicit analyses: stable time increment, wave propagation, impact/contact, transient energy, and time/mesh resolution. Use quasi-static-explicit-fea when Explicit is being used as a numerical route to a nominally static problem."
---

# Explicit Dynamics FEA

Use this skill when the physics involves short-duration transients, wave propagation, impact, drop, severe dynamic contact, or another inertia-dominated problem naturally suited to explicit time integration.

## Treat solver choice as part of the model

The explicit stable time increment is controlled by the discretization and wave speed. Small elements or very stiff regions can dominate cost. Changing mesh size, density, stiffness, or mass scaling can therefore alter both runtime and the represented physics.

Do not apply quasi-static energy heuristics as a universal acceptance rule; wave propagation and kinetic energy can be the intended physics. Use `quasi-static-explicit-fea` when the purpose of mass scaling or event acceleration is to approximate a static response.

## Verification focus

Check stable increment behavior, total simulation time, energy histories, wave travel time or impact duration when relevant, contact penetrations/forces, mesh sensitivity, and whether artificial energy or mass modifications remain acceptable for the intended claim.

Read [references/modeling-principles.md](references/modeling-principles.md) when the unresolved issue is conditional stability, characteristic wave/time scales, mesh-wave coupling, or the physical effect of changing mass.

## Official implementation reference

Read [references/explicit-time-integration-and-stability.md](references/explicit-time-integration-and-stability.md) when stable time increment, wave resolution, or time-integration diagnostics matter. Load [references/element-controls-and-energy-diagnostics.md](references/element-controls-and-energy-diagnostics.md) when reduced-integration hourglass behavior, distortion control, `ALLAE`, `ALLDC`, or artificial-energy interpretation affects the result. Use `quasi-static-explicit-fea` and `mass-scaling-and-quasi-static.md` for nominally static Explicit work. [references/abaqus-official-notes.md](references/abaqus-official-notes.md) is the package index. Read [references/official-examples.md](references/official-examples.md). The stress-wave example is the canonical first genuinely dynamic script.
