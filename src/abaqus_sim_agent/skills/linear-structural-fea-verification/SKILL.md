---
name: linear-structural-fea-verification
description: Challenge the credibility and claim boundary of a completed linear-static structural FEA result using equilibrium, independent references, linear scaling, symmetry/deformation sanity checks, and correct code-verification/solution-verification/validation terminology. Use when judging whether solved results support an engineering claim; it does not replace the task-specific host verifier.
---

# Linear Structural FEA Verification

Use this skill to challenge a completed linear-static structural result before treating it as engineering evidence. Select checks that match the task and its QoI. A long checklist is not a substitute for a clear acceptance question.

The project's host verifier remains the authority for the concrete CodingRun acceptance contract. This skill helps the Agent reason about result credibility and claim discipline; it must not let the Agent self-declare host verification or physical validation as passed.

## Verify the same QoI used for the engineering claim

Verification consumes the QoI already defined by formulation and postprocessing. Preserve its region, step/frame, result position, component/invariant, section point or orientation where applicable, and aggregation.

Do not call a mesh converged because element count increased, mesh-quality metrics passed, or a different result happened to stop changing.

## Check static force and moment balance

For a converged linear-static model, compare applied external resultants with reactions transmitted through the constraints. Include moments about a common reference point when rotational equilibrium matters.

Reduce distributed loads to consistent resultants and include the effects of couplings, MPCs, rigid bodies, and multiple supports when interpreting reactions. One support-node reaction is not automatically the total model reaction.

Failure of equilibrium is a strong signal to investigate the load, constraint, solve, or extraction path. Passing equilibrium is only a global consistency check; it does not prove local stress accuracy or the correctness of material, geometry, or support idealization.

## Compare with an independent reference when available

For simple cases, compare the same idealization and QoI with an analytical solution, hand calculation, trusted benchmark, or other independent reference whose assumptions actually match the model. Axial stiffness, elementary beam response, resultant balance, and symmetry relations can provide inexpensive checks when applicable.

Match the reference's load distribution and constraints, not just its geometry or title. A cantilever under surface pressure cannot be checked using an end-force formula merely because both examples are cantilevers.

Do not copy a tolerance from an unrelated benchmark. Acceptable discrepancy depends on the reference quality, discretization, formulation, and the engineering decision being supported.

Solver benchmark suites can support code-verification claims for the tested implementation/formulation. They do not establish that the current user's geometry, material data, loading, or supports represent reality correctly.

## Use proportional scaling as a low-cost linear sanity check

Within a genuinely linear-static model, scaling an imposed force or displacement by a factor should scale the corresponding linear response by the same factor.

Use this only when the formulation is known to be linear. It can expose accidental nonlinear settings, inconsistent load cases, or extraction mistakes, but it does not replace discretization verification or physical validation.

## Inspect symmetry and deformation mode

When the complete boundary-value problem and expected response are symmetric, corresponding reactions and deformation patterns should respect that symmetry within appropriate numerical tolerance. Unexpected asymmetry can indicate a mesh, load, constraint, orientation, or assembly error.

Also inspect whether the deformation mode and load path match the intended structural mechanism. Numerical convergence does not rescue the wrong physical idealization.

## Treat mesh convergence as solution verification owned by discretization

Mesh convergence supports a **solution verification** claim about discretization error for the current computational model. Use `linear-structural-fea-discretization` for the refinement procedure, refinement region, formulation risks, and QoI-based convergence study.

Use a task-specific error criterion; `change < 5% => converged` is not a universal rule. Keep the verification/validation distinction below.

## Keep verification, validation, and host acceptance separate

Use these terms consistently:

- **code verification** asks whether the numerical implementation/software solves the equations correctly for suitable reference problems;
- **solution verification** estimates numerical/discretization error for the current computational model;
- **validation** asks whether the model represents the real system adequately for its intended use;
- **host acceptance** is the project's task-specific check that the CodingRun satisfied its declared artifact, execution, and numerical/physics contract.

An analytical comparison or private host oracle can support a task-specific computational check without constituting validation against physical reality.

## Evidence and maintenance boundary

Read [references/evidence.md](references/evidence.md) when auditing, extending, or disputing these rules. It records the terminology sources and their limits, including the fact that the project has not acquired the paid NAFEMS R0135 body.
