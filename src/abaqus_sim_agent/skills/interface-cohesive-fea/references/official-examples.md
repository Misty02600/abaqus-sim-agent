# Official examples: interface and cohesive behavior

- `scripts/alfano_2d_model.py`, from `SIMACAEBMKRefMap/simabmk-c-alfanodelamination.htm`: topic-typical delamination benchmark using cohesive traction-separation behavior, damage initiation/evolution, and finite-sliding contact. This is the canonical shared-pool script for `interface-cohesive`.
- The same example is cross-referenced by `contact-fea` because contact and post-debond interaction are present, and by `geometric-nonlinear-fea` because `nlgeom=ON` is active. Those topics should not own duplicate copies of the script.

Use this example after the representation and parameter meanings are understood. It is a benchmark implementation reference, not a generic source of cohesive stiffness, strength, or fracture-energy values.
