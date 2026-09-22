---
name: contact-fea
description: "Define or review structural contact and interaction: contacting surfaces, normal/tangential laws, friction, clearance/overclosure, sliding assumptions, and contact-result meaning. Use contact-diagnostics-fea when the main task is convergence, penetration, or initialization troubleshooting."
---

# Contact FEA

Use this skill when load transfer depends on surfaces engaging, separating, sticking, or sliding. Contact is an **interaction nonlinearity** and deserves its own package because it introduces geometry, constitutive choices at the interface, state changes, and solver controls that are not captured by a generic nonlinear label.

## Define the physical interface first

- Identify which surfaces can interact and whether separation is allowed.
- Specify the normal behavior and the source of any clearance, interference, or pressure-overclosure relation.
- Treat friction coefficient or another tangential law as physical input with provenance; do not assume frictionless or a convenient coefficient without authorization.
- Choose small/finite sliding and contact formulation according to expected relative motion and the solver/procedure in use.

## Preserve coupled mechanisms

Contact may coexist with plasticity, hyperelasticity, or geometric nonlinearity. Load path and state history then matter across mechanisms; do not analyze each one as though the others were absent. Use `contact-diagnostics-fea` when the physical interface is already defined and the unresolved work is initialization, convergence, penetration, or contact-force troubleshooting.

Read [references/modeling-principles.md](references/modeling-principles.md) when the unresolved issue is unilateral contact mechanics, frictional path dependence, constraint enforcement, discretization sensitivity, or the physical meaning of small/finite sliding. Use the official topic references for Abaqus algorithms, defaults, and troubleshooting behavior.

## Verification focus

Check active contact regions, gap/penetration measures, normal and tangential tractions or forces, reaction/load balance, sliding/sticking pattern when relevant, and sensitivity to mesh/contact discretization near the interface.

## Official implementation reference

Use [references/abaqus-official-notes.md](references/abaqus-official-notes.md) as the official-reference index. Load [references/contact-formulation-and-topology.md](references/contact-formulation-and-topology.md) when the unresolved decision is the interacting regions or contact route, and [references/contact-mechanical-behavior.md](references/contact-mechanical-behavior.md) for normal/tangential interface behavior. Route troubleshooting to `contact-diagnostics-fea` so ordinary modeling work does not load both diagnostics references. Use `interface-cohesive-fea` when bonded traction-separation and damage are central. Read [references/official-examples.md](references/official-examples.md). The lap-joint example is the canonical first modeling script.
