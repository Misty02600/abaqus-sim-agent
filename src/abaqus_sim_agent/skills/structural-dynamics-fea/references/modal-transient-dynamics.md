# Transient modal dynamics

Source page: `SIMACAEANLRefMap/simaanl-c-modaldynamic.htm` (*Transient Modal Dynamic Analysis*), Abaqus 2026 FD02.

Use this reference when time response is represented in a basis of previously extracted modes.

## Retained basis

Modal transient analysis is efficient when the structural response is well represented by the retained modes. Accuracy therefore depends on mode selection, load projection, damping, initial conditions, prescribed motion, and the frequency content of the excitation.

Increase the retained range until the requested response quantity is insensitive to further modes. Participation/effective mass can support that decision but does not replace response convergence.

## Damping and loading

Keep modal/global/material damping definitions distinct and preserve their source. Loads and prescribed motions must project adequately into the modal basis; a load pattern that excites omitted local/high-frequency modes can invalidate an otherwise reasonable truncation.

## Prescribed support motion and bases

Mode-based dynamic response does not accept an arbitrary nonzero displacement or rotation history as an ordinary boundary condition. Support motion must be represented as base motion, and the constrained degrees of freedom that will move must already be assigned to one or more bases during the preceding frequency-extraction step.

The unnamed base is the primary base. Additional named bases are secondary bases, allowing different support histories to be prescribed to different groups of constrained degrees of freedom. A base-motion history can be specified as displacement, velocity, or acceleration; keep the chosen quantity and amplitude provenance explicit.

For the primary base, Abaqus reports the modal response relative to the primary-base motion. When absolute translational motion is required, request the corresponding total-motion outputs rather than interpreting the relative `U`, `V`, or `A` histories as absolute response.

Secondary-base motion is implemented with the Abaqus/Standard big-mass approach for mechanical degrees of freedom. Translational response at a secondary base is still reported relative to the primary base, so multiple-support-excitation results need the same relative-versus-total-motion care as primary-base excitation.

## Excitation sampling and time increment

Choose the modal-dynamic time increment consistently with the sampling and interpolation of the forcing record. If an excitation record is tabulated at a fixed interval and linear variation between samples is intended, using that same interval is the natural starting point; the Abaqus documentation gives a 1 ms seismic record with a 1 ms analysis increment as the representative case. Refine the increment when the requested response quantity or excitation bandwidth requires it.

## Applicability boundary

A fixed modal basis is not a generic replacement for a strongly nonlinear transient analysis. If contact state, constitutive stiffness, or geometry changes materially during the event, use a procedure that represents those changes directly.
