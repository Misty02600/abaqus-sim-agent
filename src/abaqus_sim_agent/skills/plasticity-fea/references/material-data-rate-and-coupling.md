# Metal plasticity: material data, rate dependence, and coupling

Source page: `SIMACAEMATRefMap/simamat-c-metalplastic.htm`, sections on direct tabular data, rate dependence, heat generation by plastic work, initial conditions, elements, and output; Abaqus 2026 FD02.

Use this reference when the model family is known but the meaning of the supplied material data or its coupling to the analysis procedure is unresolved.

## Plastic data semantics

The plasticity input represents yield stress as a function of the plastic strain quantity required by the selected model. Raw engineering stress-strain measurements are not automatically the required table: preserve the stress/strain measure and remove the elastic contribution as required by the constitutive definition before treating the data as plastic input.

Keep units, temperature, rate, orientation, test history, and preprocessing attached to the data source. Do not repair missing material data with tutorial constants.

## Rate dependence

Rate sensitivity is a separate constitutive choice. A dynamic analysis step does not make a rate-independent plasticity table rate dependent. Conversely, rate-dependent material data need a procedure and loading history that resolve the relevant time scale.

## Thermal/mechanical coupling and output

Plastic work can contribute to heat generation in procedures that support the corresponding coupling, but the conversion and thermal behavior require their own material/step inputs. A plasticity definition alone does not create a thermomechanical model.

For verification, inspect stress, plastic-strain measures, plastic dissipation/energy when relevant, and the structural load-displacement path. Damage/failure quantities belong to a separate failure model when they control the requested answer.
