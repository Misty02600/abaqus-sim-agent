# Abaqus official references: interface and cohesive behavior

Source baseline: local Abaqus 2026 FD02 cohesive-element and interaction documentation. These files summarize product semantics, not Abaqus 2024 API compatibility.

Load only the topic needed by the task:

- [representation-selection.md](representation-selection.md) — cohesive elements versus surface-based cohesive behavior and the modeling consequences of that choice.
- [traction-separation-and-damage.md](traction-separation-and-damage.md) — intact stiffness, initiation, evolution, mixed mode, compression, and post-degradation behavior.
- [cohesive-elements-thickness-and-connectivity.md](cohesive-elements-thickness-and-connectivity.md) — geometric/constitutive thickness, orientation, neighboring mesh connection, and nonconforming interfaces for cohesive elements.

These topics share traction-separation concepts but should not collapse the topology, constitutive law, and cohesive-element connectivity into one always-read note.
