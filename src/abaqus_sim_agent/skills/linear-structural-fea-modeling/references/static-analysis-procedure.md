# Static stress analysis procedure

Source pages: `SIMACAEANLRefMap/simaanl-c-staticproc.htm` and `SIMACAEANLRefMap/simaanl-c-static.htm`, Abaqus 2026 FD02.

Use this reference when the unresolved question is whether a static procedure is appropriate, what makes a static analysis linear or nonlinear, or which step-level inputs matter.

## Procedure meaning

A static stress procedure neglects inertia and solves equilibrium for the active loads, boundary conditions, interactions, material response, and geometry. Calling a step `Static` does not make the model linear.

A linear-static baseline requires the modeling assumptions themselves to stay linear: small displacement/rotation, linear constitutive response, a fixed interaction state, and loads whose direction or magnitude does not change with the configuration. Material plasticity, contact-status changes, large deformation, follower loading, and similar effects make the equilibrium problem nonlinear even inside a static procedure.

## Inputs that must remain explicit

Preserve the load and boundary-condition history, predefined fields, initial conditions, material options, and requested outputs. For nonlinear static response, time period and incrementation are numerical/load-history controls rather than evidence that the physical problem is dynamic.

Do not select a nonlinear static procedure merely to hide rigid-body motion, poor constraints, or an ill-conditioned model. Fix the physical model first.

## Checks

For a basic static model, verify reaction/load balance, expected symmetry and deformation mode, adequate rigid-body suppression without overconstraint, and mesh convergence of the requested quantity of interest.
