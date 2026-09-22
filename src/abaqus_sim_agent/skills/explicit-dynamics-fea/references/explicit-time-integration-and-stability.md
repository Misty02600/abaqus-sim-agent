# Explicit time integration and stability

Source page: `SIMACAEANLRefMap/simaanl-c-expdynamic.htm` (*Explicit Dynamic Analysis*), Abaqus 2026 FD02.

Use this reference for impact, wave propagation, drop, severe dynamic contact, or another inertia-dominated Explicit problem.

## Stable increment

Abaqus/Explicit uses conditionally stable explicit integration. The stable time increment depends on the discretized system, especially local element size, stiffness/density through wave speed, and other high-frequency features. A tiny or very stiff local region can dominate total cost.

The automatically stable increment is a stability limit, not an accuracy guarantee. The mesh, output interval, and event duration must still resolve the physical wave/contact time scales of the quantity of interest.

## Cost and controls

Before changing mass, inspect which elements control the stable increment and whether their size/material definition is physically necessary. The procedure provides automatic/fixed time incrementation, time-increment scaling, selective subcycling where applicable, bulk viscosity, deformation-speed diagnostics, and monitoring of extreme variables.

Treat bulk viscosity and other numerical controls as explicit modeling/numerical choices. They can alter high-frequency response and should not be used to conceal a poor mesh or unstable physical definition.

## Checks

Inspect stable-increment history, critical elements, deformation-speed warnings, kinetic/internal/artificial energy as applicable, momentum/reaction behavior, contact work, and mesh/time-resolution sensitivity.
