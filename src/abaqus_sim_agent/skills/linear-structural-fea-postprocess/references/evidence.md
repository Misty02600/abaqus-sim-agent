# Evidence for `linear-structural-fea-postprocess`

This reference records the local official documentation used to support result semantics. The current local corpus is Abaqus 2026 FD02, so it does not establish exact Abaqus 2024 API compatibility.

## Result position, extrapolation, and averaging

Checked local pages:

- `SIMACAEOUTRefMap/simaout-c-dboutput-elementoutput.htm`: element-output positions, extrapolation/interpolation, averaged-at-nodes output, section points, derived quantities, and local/material directions;
- `SIMACAECMDRefMap/simacmd-c-odbintroreadregpyc.htm`: field-subset position behavior and extrapolation to element-nodal/centroidal positions;
- `SIMACAEKERRefMap/simaker-c-fieldoutputpyc.htm`: `NODAL` versus `ELEMENT_NODAL`, section-point metadata, and local-coordinate metadata;
- `SIMACAECAERefMap/simacae-c-resconceptaverage.htm`: Viewer result averaging, result regions, averaging threshold, and invariant/component ordering relative to averaging.

These pages support the distinction among node-based results, element results extrapolated to nodes, Abaqus/Standard averaged-at-nodes output, and Viewer display averaging.

## Bounded conclusions

- `NODAL` and `ELEMENT_NODAL` are not interchangeable result positions.
- An element-nodal field can be extrapolated from integration-point data and should not be described as a directly computed nodal stress.
- Solver/output-generation averaging and Viewer result averaging are separate mechanisms.
- Derived quantities can depend on whether tensor components are averaged before or after invariant/component formation.
- Section points and coordinate/material directions can be part of the meaning of a result and therefore part of the QoI contract.
- High-gradient peak interpretation should preserve integration-point semantics before introducing extrapolated or smoothed nodal representations.

## Compatibility boundary

The full local documentation used here is Abaqus 2026 FD02. Exact Abaqus 2024 field-output APIs, arguments, enum/constants, defaults, section-point request syntax, and release-specific behavior must be confirmed by the 2024 Exact API Catalog, matching documentation, or licensed runtime evidence.

## Related project audit

- `docs/scratch/linear-structural-fea-source-topic-matrix.md`
- `docs/scratch/linear-structural-fea-evidence-bindings.md`
- `docs/scratch/linear-structural-fea-routing.md`
