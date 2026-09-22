# Abaqus official references: contact

Source baseline: local Abaqus 2026 FD02 interaction documentation. These files summarize product semantics, not Abaqus 2024 API compatibility.

Load only the topic needed by the task:

- [contact-formulation-and-topology.md](contact-formulation-and-topology.md) — interacting surfaces, contact assignment routes, solver/formulation distinctions, and topology choices.
- [contact-mechanical-behavior.md](contact-mechanical-behavior.md) — normal response, tangential behavior, friction, pressure-overclosure, clearance, and separation assumptions.
- [contact-diagnostics-standard.md](contact-diagnostics-standard.md) — initialization and convergence diagnosis for Abaqus/Standard contact.
- [contact-diagnostics-explicit.md](contact-diagnostics-explicit.md) — penetration, contact-force/work, stable-increment interaction, and other Abaqus/Explicit diagnostics.

Bonded traction-separation and progressive interface damage route to `interface-cohesive-fea`; this package keeps ordinary contact topology, mechanics, and diagnostics separate from cohesive constitutive decisions.
