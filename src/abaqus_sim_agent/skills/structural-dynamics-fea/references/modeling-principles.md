# Structural dynamics: modeling principles

Use this reference when the unresolved question is the mechanics of inertia, natural modes, damping, time resolution, or modal reduction rather than an Abaqus-specific procedure.

## Dynamic equilibrium

After spatial discretization, structural dynamics balances inertia, damping, internal force, and applied loading. In a linearized setting this is commonly written as `M u_ddot + C u_dot + K u = f(t)`. Mass and damping therefore become part of the represented physics rather than optional numerical decorations.

The relevant time scales come from both the structure and the excitation. Natural periods, load rise time, pulse duration, base-motion frequency content, contact duration, and requested output bandwidth can all control the resolution needed for a useful result.

## Modes and the base state

For a linearized undamped system, natural modes follow the generalized eigenproblem `K phi = omega^2 M phi`. Frequencies and mode shapes therefore depend on stiffness, mass, constraints, and the state about which the system is linearized.

Prestress or a preceding nonlinear configuration can change the effective stiffness and thus the modal basis. A frequency value without its mode shape and base-state definition is incomplete evidence.

## Time resolution is an accuracy question

A time-integration scheme can be numerically stable while still resolving the excitation or response poorly. Choose the time increment from the highest frequency and shortest event time that materially affect the quantity of interest, then verify the result by refinement. Do not encode one universal number of increments per cycle as a general rule.

Sampling of input histories matters as well: a finely integrated model cannot recover frequency content that was already lost or aliased in the supplied excitation data.

## Modal reduction and damping

Modal reduction assumes that the retained basis spans the deformation patterns needed by the load and requested response. Participation or effective-mass measures can help judge completeness, but the decisive check is convergence of the actual quantity of interest as the retained basis grows.

Damping is a physical/modeling assumption and is frequency dependent for many common representations. For example, Rayleigh damping produces different damping ratios at different frequencies. Calibration should therefore be tied to the frequency range and experimental evidence relevant to the task rather than chosen only to suppress oscillations.

This file contains general structural-dynamics principles. Abaqus eigensolvers, modal/direct procedures, damping options, and procedure-specific controls remain in the official topic references.
