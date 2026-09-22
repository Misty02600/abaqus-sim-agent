# Official examples: contact

- `scripts/gsi_lap_joint_caemodel.py`, from `simagsa-c-appendixlap.htm`: `ContactProperty`, `TangentialBehavior`, `ContactStd`, `nlgeom=ON`, and tabulated plasticity.
- `scripts/gsi_channel_caemodel.py`, from `simagsa-c-appendixchannel.htm`: frictional forming contact, multiple surface-to-surface interactions in Standard, plasticity, large geometry change, and a separate Explicit forming branch.
- `scripts/gxi_circuit_caemodel.py`, from `simagsa-c-appendixcircuit.htm`: Explicit general contact in a circuit-board drop test; canonical ownership remains with Explicit dynamics and contact is a cross-topic.
- `scripts/snubber_model.py`, from `SIMACAEEXARefMap/simaexa-c-snubberarm.htm`: topic-typical finite-sliding frictional contact in a rigid-body/connector mechanism.
- `scripts/pillar_fastener_std.py`, from `SIMACAEEXARefMap/simaexa-c-bucklespotweld.htm`: complex-comprehensive contact/fastener/plastic-collapse example.
- `scripts/rivet_forming.py` and `scripts/cel_bottle_drop.py`: complex Explicit applications using general contact; use them for coupled behavior after the simpler contact cases.
- `scripts/alfano_2d_model.py`, from `SIMACAEBMKRefMap/simabmk-c-alfanodelamination.htm`: cohesive traction-separation, damage initiation/evolution, and finite-sliding contact. Its primary topic is kept as `interface-cohesive` so a future cohesive/interface package can own it without redefining cohesive damage as ordinary contact.

These are intentionally coupled examples. Use them after simpler mechanism-specific material or step examples when the goal is to understand interactions among contact, yielding, changing geometry, and solver procedure.
