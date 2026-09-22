# Explicit dynamics: modeling principles

Use this reference when the unresolved question is why explicit time integration behaves as it does: conditional stability, wave propagation, mesh/time-scale coupling, inertia, or quasi-static time-scale separation.

## Conditional stability and wave transit

Central-difference explicit integration is conditionally stable. A useful physical intuition is that information should not propagate across an element faster than the numerical update can resolve it, so the stable increment scales with a characteristic element length divided by an appropriate wave speed. The exact solver estimate is formulation dependent.

For a simple one-dimensional linear elastic material, wave speed scales like the square root of stiffness divided by density. This explains why smaller elements and stiffer/lighter material regions tend to reduce the stable increment.

Stable does not mean accurate. A simulation can satisfy its stability limit and still under-resolve a stress wave, impact pulse, contact event, or requested high-frequency response.

## Mesh, mass, and represented physics

Changing element size, stiffness, or density changes more than runtime. It can change wave speed, inertia, contact timing, and local deformation. A tiny group of small or stiff elements can dominate cost, so diagnose the controlling region before modifying physical inputs.

Mass scaling raises the stable increment by changing mass and therefore inertia and wave propagation. Its acceptability must be judged from sensitivity of the requested response, not from computational speed alone.

## Dynamic and quasi-static time scales

For genuinely dynamic work, preserve the physical event duration, wave travel times, impact/contact duration, and relevant natural periods. For nominally quasi-static Explicit work, the imposed loading time must be sufficiently slow relative to the structure's important dynamic time scales that inertia does not materially change the requested response.

There is no universal kinetic-to-internal-energy percentage that proves quasi-static validity. Energy histories are diagnostic evidence and must be interpreted together with deformation mode, reactions, load-displacement response, loading-rate sensitivity, and mass-scaling sensitivity.

## Verification

Check mesh and time-resolution sensitivity, energy balance, momentum/reaction behavior, wave arrival or impact timing when relevant, and the effect of any artificial mass or numerical dissipation on the quantity of interest. Use simpler wave, impact, or quasi-static benchmarks before treating a complex result as validated.

This file contains general explicit-dynamics principles. Abaqus stable-increment estimation, mass-scaling controls, bulk-viscosity options, and product diagnostics remain in the official topic references.
