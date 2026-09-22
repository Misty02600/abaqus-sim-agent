---
name: contact-diagnostics-fea
description: "Diagnose contact initialization, convergence, penetration, chattering, and contact-force anomalies in Standard or Explicit. Use when an existing contact model behaves incorrectly or fails, after the physical interface definition has been established."
---

# Contact Diagnostics FEA

Use this skill when a contact model already exists and the main task is diagnosing why it initializes, converges, penetrates, sticks, slips, or transfers load incorrectly.

## Diagnose the represented interface before solver controls

Check the intended interacting surfaces, normals, initial gaps/overclosures, constraints, contact-property assignment, sliding assumption, and mesh near the interface. A numerical symptom can be caused by a wrong physical interaction definition.

For Abaqus/Standard, inspect contact status changes, severe discontinuities, increment history, excessive overclosure, constraint conflicts, and stiffness/friction assumptions before changing generic convergence controls.

For Abaqus/Explicit, inspect initial penetrations, contact forces/work, stable time increment, local mesh size, contact stiffness effects, and whether a high-frequency contact response is physical or numerical.

## Preserve the failure evidence

Keep the original warning/error text, step and increment/time, affected regions, and the model change used in each diagnostic attempt. Do not convert one successful convergence tweak into a general contact rule without checking that it preserved the intended mechanics.

## Route model definition separately

Use `contact-fea` when the unresolved task is defining the surfaces, normal/tangential law, friction, clearance, or contact formulation. Use `interface-cohesive-fea` when bonded traction-separation and damage are central.

Load only the matching official diagnostic reference: [../contact-fea/references/contact-diagnostics-standard.md](../contact-fea/references/contact-diagnostics-standard.md) for Abaqus/Standard or [../contact-fea/references/contact-diagnostics-explicit.md](../contact-fea/references/contact-diagnostics-explicit.md) for Abaqus/Explicit. If diagnosis reveals that the actual interaction topology is wrong, return to [../contact-fea/references/contact-formulation-and-topology.md](../contact-fea/references/contact-formulation-and-topology.md). These Abaqus 2026 FD02 references do not establish Abaqus 2024 API compatibility.
