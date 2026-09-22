# Dynamic procedure selection and damping

Source page: `SIMACAEANLRefMap/simaanl-c-dynamicproc.htm` (*About Dynamic Analysis Procedures*), Abaqus 2026 FD02.

Use this reference before choosing between direct implicit, modal, or explicit dynamics, or when the damping representation is unresolved.

## Procedure axes

Two separate choices matter: implicit versus explicit time integration, and direct solution versus modal superposition. Do not collapse them into a single `dynamic` procedure choice.

Direct procedures retain the physical degrees of freedom and can represent nonlinear changes supported by the selected solver. Modal procedures reduce the response to a retained modal basis and therefore inherit the assumptions and truncation of that basis.

Use Explicit for problems whose time scale, contact/deformation severity, or other characteristics suit explicit integration; use the Explicit Pack for its stability and mass-scaling semantics.

## Damping

Abaqus supports several damping descriptions across dynamic procedures, including viscous, structural, modal, composite/modal, and material-dependent forms where applicable. Keep the damping source and calibration explicit; do not invent a convenient damping ratio to suppress numerical oscillation.

The selected damping representation must match the procedure. A damping value meaningful in one modal or frequency-domain formulation is not automatically interchangeable with a direct-transient material/viscous definition.

## Checks

Preserve density/mass properties, excitation type, time/frequency range, damping assumptions, and requested outputs. Verify that the selected procedure can represent the nonlinearities and frequency content that matter to the quantity of interest.
