---
name: interface-cohesive-fea
description: "Model or review bonded interfaces, delamination, adhesive layers, and cohesive damage using cohesive elements or surface-based cohesive behavior. Resolve interface representation, traction-separation stiffness, damage initiation/evolution, thickness meaning, and post-failure contact without inventing interface parameters."
---

# Interface and Cohesive FEA

Use this skill when the response depends on a bonded interface, adhesive layer, delamination, debonding, or a traction-separation damage law. Keep it separate from ordinary contact: contact governs interaction after/between surfaces, while a cohesive law supplies bonded traction-separation response and progressive degradation.

## Choose the interface representation first

Decide whether the problem needs explicit cohesive elements or surface-based cohesive behavior.

- Cohesive elements are appropriate when the interface/layer should exist as its own discretized region, including cases where thickness, mesh topology, or layer fields matter.
- Surface-based cohesive behavior is appropriate when the bond is naturally represented between two contact surfaces and a separate cohesive mesh is unnecessary.

Do not pick between them from scripting convenience. Read [references/representation-selection.md](references/representation-selection.md) when this representation choice is unresolved.

## Preserve parameter meaning

Traction-separation stiffness controls the intact interface compliance. Damage initiation determines when degradation may start; damage evolution determines how stiffness/traction degrades and how much separation/energy is required for failure. A damage-initiation criterion without an evolution law is not a complete progressive-failure model.

Do not infer stiffness, strength, fracture energy, mode-mix law, or constitutive thickness from an adhesive/material name. Keep parameter provenance and units explicit.

Read [references/traction-separation-and-damage.md](references/traction-separation-and-damage.md) when intact stiffness, initiation/evolution, mixed mode, or post-degradation contact is unresolved.

Read [references/modeling-principles.md](references/modeling-principles.md) when the unresolved issue is process-zone idealization, the roles of stiffness/strength/fracture energy, penalty sensitivity, mixed-mode assumptions, mesh resolution of softening, or competition with bulk failure. Use the narrower official references for Abaqus representation and implementation semantics.

## Couple cohesive damage with contact deliberately

Compression, reopening, sliding, and behavior after substantial degradation must be modeled consistently with the chosen cohesive/contact formulation. Avoid double-counting the same normal or tangential stiffness through overlapping constraints and interactions.

For cohesive elements, check how the layer is connected to the adjacent bulk mesh and whether a tie or conforming mesh is intended. For surface-based cohesive behavior, check which surface pairs can bond and whether initially separated surfaces are allowed to form a bond.

When cohesive elements are selected, load [references/cohesive-elements-thickness-and-connectivity.md](references/cohesive-elements-thickness-and-connectivity.md) for thickness, orientation, and connection decisions. [references/abaqus-official-notes.md](references/abaqus-official-notes.md) is the package index rather than an always-read reference.

## Verification focus

Useful checks include the intact elastic compliance, onset location of damage, traction/separation or load/displacement history, dissipated fracture energy, mode mix, crack/debond path, mesh sensitivity, and compression/contact behavior after degradation. Verify a simple interface coupon or benchmark before trusting a complex structure.

## Official examples

Read [references/official-examples.md](references/official-examples.md). The Alfano delamination benchmark is the canonical first script in the shared official pool and cross-references geometric nonlinearity and contact without redefining cohesive damage as ordinary contact.
