# Cohesive-interface modeling principles

Theory anchors: cohesive-zone and interface-fracture mechanics, with the package's official Abaqus cohesive references providing product-specific semantics. This note records reusable modeling decisions and deliberately does not provide adhesive constants or Abaqus defaults.

Use this reference when the unresolved issue is whether a traction-separation idealization can represent the intended interface physics, how its parameters interact, or how to judge mesh and structural sensitivity.

## A cohesive law represents a fracture process zone

A traction-separation model replaces detailed microscale fracture processes with an interface law relating traction to relative opening/sliding. The model therefore introduces a finite process zone whose size depends on bulk stiffness, interface strength, fracture energy, geometry, and mode mix.

The interface should be interpreted as a constitutive idealization of that process zone, not merely as a numerical connector between two meshes.

## Intact stiffness, strength, and fracture energy play different roles

Intact normal/tangential stiffness controls pre-damage compliance. Damage-initiation parameters control the onset of degradation. Damage-evolution parameters such as critical separation or fracture energy control the subsequent softening and dissipation.

These quantities are not interchangeable calibration knobs. Raising stiffness to reduce artificial compliance does not justify changing strength or fracture energy, and matching peak load alone does not establish the correct dissipated energy or failure path.

## Penalty stiffness needs a sensitivity argument

An interface stiffness that is too low adds artificial compliance to the structure. An excessively high stiffness can make an implicit system poorly conditioned or reduce an explicit stable increment. Choose a value from measured interface compliance when available; otherwise use a documented penalty rationale and demonstrate that the structural quantity of interest is insensitive over a defensible range.

## Softening creates a length-scale problem

Once a cohesive law softens, energy dissipation and the resolved fracture process zone depend on how the interface is discretized. The mesh must resolve the traction-separation process adequately; a mesh that contains too few interface elements inside the process zone can distort peak load, crack growth, and energy dissipation.

Do not judge mesh convergence only from the smallest element or the largest damage variable. Check global response, damage-zone length, energy dissipation, and crack/debond path over refinement.

## Mixed-mode failure needs an explicit interaction assumption

Opening and shear contributions may interact during initiation and evolution. A mixed-mode criterion states how those components combine; it cannot be inferred from separate Mode I and Mode II strengths/energies without an interaction law.

Preserve the exact convention and calibration range of any mixed-mode parameter. Different criteria can fit the same pure-mode data while predicting different mixed-mode paths.

## Interface failure competes with bulk deformation and contact

The surrounding adherends can yield, undergo large deformation, or contact after local debonding. A predicted interface crack path is credible only if the competing bulk and contact mechanisms are represented consistently.

After substantial degradation, compressive contact and friction may control the response even though the original bond carried tensile/shear tractions. Avoid double-counting the intact cohesive stiffness with overlapping ties, constraints, or contact penalties.

## Verify simple modes before structural failure

Start with opening, shear, and mixed-mode coupons that exercise the calibrated law. Check intact compliance, damage onset, peak traction/load, area under the traction-separation response when energy-based evolution is used, mode mix, unloading/reloading behavior if relevant, and post-failure contact. Only then interpret a complex structural debonding simulation.
