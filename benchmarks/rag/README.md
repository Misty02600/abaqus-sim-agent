# Abaqus documentation retrieval benchmark

## Current storage implementation

The product retrieval path now uses SQLite documents/metadata plus an aligned
NumPy exact-cosine matrix; historical Chroma indexes remain immutable evidence.
The storage migration, dependency boundary, local scan measurement, and post-hoc
ranking replay are recorded in
[sqlite_numpy_flat_storage_v1_20260916.md](sqlite_numpy_flat_storage_v1_20260916.md).

## Current explicit evidence baseline

The integrated `rag-evidence-baseline-v1` entry, fixed requests, quality scores, and
usage are documented in [the baseline report](rag_evidence_baseline_v1_20260911.md).
It includes equation-context delivery and separate candidate/reranking/delivery
scores. The historical experiments below remain unchanged evidence, not current
product defaults. The new baseline also remains opt-in and is not a quality gate
or an Agent-benefit claim.

A later [fixed-pool reranker comparison](fixed_pool_bge_v1_20260911.md) retained
legacy scores alongside task-aligned labels. Neither original pool order nor
BGE v2-m3 met the predeclared replacement criterion; the baseline remains unchanged.

## Historical retrieval benchmark

`abaqus_2026_retrieval.jsonl` is a small, hand-authored smoke benchmark for the
local Abaqus 2026 HTML index. It contains no copied documentation text.

This benchmark measures retrieval only. It does not evaluate generated answers,
answer faithfulness, Abaqus code quality, or downstream `CodingRun` success.

The four modes isolate different responsibilities:

- `concept`: semantic search over general documentation questions;
- `dense`: semantic-only baseline for a paired identifier-heavy query;
- `augmented`: the same paired query with caller-supplied Abaqus symbols ranked first;
- `symbol`: exact lookup for keywords, keyword parameters, element names, and
  output variables.

`augmented` is not a BM25+dense arm. The benchmark supplies `exact_terms`
directly; the implementation uses metadata-filtered exact hits as a priority
prefix and fills the remaining slots with dense results. It has no sparse
full-text scorer, score fusion, or automatic symbol extraction.

Run it after building the local index:

```powershell
uv run python -m abaqus_sim_agent.retrieval evaluate `
  --index-dir var/rag `
  --benchmark benchmarks/rag/abaqus_2026_retrieval.jsonl `
  --output var/rag/evaluation.json
```

The report includes paired Recall@k and MRR deltas for `dense` versus
`augmented`. The 42 rows contain 31 unique queries and 19 unique gold documents;
40 rows have one gold document, so their Recall@5 is equivalent to Hit@5. The
field named `symbol_accuracy` is also mean Hit@5, not a strict top-1 metric.

## Frozen smoke result

`abaqus_2026_retrieval_evaluation_20260821.json` is a byte-for-byte frozen copy
of the local report produced on 2026-08-21. The evaluated index was bound to:

- Abaqus documentation release `2026 FD02`;
- collection `abaqus_docs_2026_fd02`;
- 1,609 files seen, 1,480 loaded, and 38,913 records indexed;
- `chromadb.DefaultEmbeddingFunction/all-MiniLM-L6-v2`;
- 1,000-character chunks with 100-character overlap;
- `top_k=5`.

The 42-case smoke result was:

| Mode | Cases | Recall@5 | MRR / accuracy |
| --- | ---: | ---: | ---: |
| `concept` | 10 | 1.000 | MRR 0.825 |
| `dense` | 11 | 0.727 | MRR 0.655 |
| `augmented` | 11 | 1.000 | MRR 1.000 |
| `symbol` | 10 | - | accuracy 1.000 |

Overall query latency in that report was approximately 315 ms p50 and 527 ms
p95. These are small, hand-authored smoke measurements, not stable production
accuracy or latency estimates.

A 2026-09-03 replay against the same local index reproduced every aggregate
accuracy metric and all 42 ranked document-id lists exactly. Its p50 was about
367 ms and p95 about 1,067 ms because the first embedding query incurred a
roughly 9.2-second cold start. This confirms the ranking snapshot, not a stable
latency claim.

Provenance checksums:

- benchmark JSONL SHA-256:
  `1a4edc8bd900533186dfb52426eb1a71beec322e093cacd981523f459e1f4213`;
- local index manifest SHA-256:
  `9671bd588940438ef1edfc067ac9bb8b26b96d6be57c0c4740b70bd2aafb5cc1`;
- frozen report SHA-256:
  `d20548caa43c8824870a5b245442e47e16b5b73ef88f5ef7374f849723cb5869`.

## Downstream status

Concept retrieval remains off by default. The frozen
[`bar_tension_3d` Concept experiment](../coding/bar_tension_3d_concept_evaluation_20260824.json)
observed 3/3 pre-CAE lookup adoption after adding a prompt-only policy, but no
closed-loop success improvement over the 2/3 control. Merely exposing the tool
produced zero Concept lookups in 3/3 runs. Retrieval quality and tool adoption
therefore do not establish downstream task benefit.

This result closes the historical exact-symbol-routing baseline. It does not
justify adding a reranker, GraphRAG, an answer-generation layer, or making
Concept retrieval the default `CodingRun` mode. The separate BM25/Dense/RRF
experiment below must not be inferred from these `augmented` cases.

The corpus is Abaqus 2026 FD02 while the product runtime is Abaqus 2024. It is
usable as version-labelled modeling evidence, but cannot establish Abaqus 2024
API or keyword compatibility. The current report format also does not embed the
benchmark/index hashes or dependency versions, and the manifest does not hash
the Chroma database bytes; the checksums above are therefore a manual local
provenance binding rather than full index-content attestation.

## BM25, Dense, and RRF comparison

`abaqus_2026_bm25_dense_rrf.jsonl` is a query-centric development smoke derived
from the 31 unique queries above. It contains 10 concept questions, 11
identifier-in-context questions, and 10 identifier-lookup diagnostics. The
former product filters for `U` and `RF` were moved into their query text. The
schema forbids `mode`, `pair_id`, `exact_terms`, and `products`, so all three
retrievers receive the same raw query.

The BM25 path is an application-owned inverted index stored in SQLite:

- NFKC/case-folded `abaqus-lexical-v1` tokens preserve identifiers such as
  `C3D8R`; `*STATIC` emits both `*static` and `static`, while hyphenated and
  underscored terms emit their compound and component forms;
- fixed Okapi BM25 uses `k1=1.2`, `b=0.75`, and
  `ln(1 + (N - df + 0.5) / (df + 0.5))` IDF;
- only the same 32,466 semantic records used by Dense concept retrieval are
  indexed; exact metadata records remain a separate lookup lane;
- opening the combined index recomputes the current Chroma semantic-corpus hash;
  Hybrid queries and comparison batches also check it before and after execution
  and reject drift from the BM25 manifest;
- RRF receives the top 20 parent-document rankings from BM25 and Dense and uses
  `1 / (60 + rank)` without score normalization or post-hoc weights.

Build and compare without recomputing embeddings:

```powershell
uv run python -m abaqus_sim_agent.retrieval build-bm25 --index-dir var/rag

uv run python -m abaqus_sim_agent.retrieval compare `
  --index-dir var/rag `
  --benchmark benchmarks/rag/abaqus_2026_bm25_dense_rrf.jsonl `
  --output var/rag/bm25-dense-rrf.json
```

The local sparse build completed in about 21 seconds and produced a 72.98 MiB
database with 16,690 terms and 1,298,647 postings. The primary 21-query natural
language slice produced:

| Retriever | Recall@5 | MRR | p50 latency |
| --- | ---: | ---: | ---: |
| BM25 | 0.714 | 0.482 | 249 ms |
| Dense | 0.857 | 0.736 | 298 ms |
| RRF Hybrid | 0.762 | 0.643 | 545 ms |

BM25 alone found one query missed by Dense; Dense alone found four missed by
BM25; both found 14 and neither found two. RRF versus Dense had one Recall@5
win, 17 ties, and three losses. A second replay reproduced all 93 ranked lists
and every accuracy metric exactly. The negative aggregate result is retained:
RRF is implemented and testable, but it does not replace the current Concept
tool or become a default Agent path. A later default-off Solver diagnostic
advisory experiment is documented below.

Frozen comparison provenance:

- comparison benchmark SHA-256:
  `233dd81a92d7b6cd01946debfe68e8ea0106e32c927bf61a2dde94f0fea0d11f`;
- BM25 manifest SHA-256:
  `8007bdc46139ffb7bda308c44ef49356b32d3c59810d861e94d81fc469867738`;
- BM25 semantic corpus SHA-256:
  `2f68cc586e829ab068fd173aaaff87a749f3fbfb257b25cee249b5d5ef09acc2`;
- BM25 SQLite SHA-256:
  `75db127594586c96c95f158f5a3266ab570bfe920d85d356eb68102eb4588bd5`;
- frozen comparison report SHA-256:
  `bbe4801d05653b10c9ea17c5731fcb19cc60f544bc94a466cc05049b8fcbe8a3`.

This is still a hand-authored, failure-driven development set with only 19
unique gold pages. No tokenizer, BM25, or RRF parameter was changed after the
first run. The sealed set below evaluates that frozen configuration; any later
tuning would require another unseen held-out version.

## Sealed held-out v3

The held-out set was created in a separate query-first lifecycle. A query-only
file was frozen before direct HTML labeling. V1 was withdrawn because `DC3D8`
and `AC3D8` required a development gold page; v2 was withdrawn because the
fallback C3D8P element index remained in the same declared continuum-element
family. Neither version was evaluated. V3 replaced only the blocked cases and
was sealed before any held-out ranking was run.

V3 contains 30 natural-language queries: 12 concept, 12 identifier-in-context,
and six multi-document cases. Direct official-HTML annotation produced 40 AND
requirements and 38 unique acceptable pages; alternatives within one
requirement have OR semantics. All page hashes, identifiers, body IDs, section
anchors, and semantic-corpus membership were checked. Development and held-out
gold IDs are disjoint, and the manually declared family members are disjoint.
The second review was performed by a separate Codex subagent, not an independent
human annotator; no inter-annotator-agreement claim is made.

Audit the original sealed dataset with:

```powershell
uv run python -m abaqus_sim_agent.retrieval audit-heldout `
  --index-dir var/rag `
  --seal benchmarks/rag/abaqus_2026_bm25_dense_rrf_heldout_seal_v3.json `
  --repository-root .
```

The first and now consumed evaluation produced:

| Retriever | Complete Hit@5 | Requirement Recall@5 | MRR | p50 latency |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 0.767 (23/30) | 0.839 | 0.596 | 460 ms |
| Dense | 0.800 (24/30) | 0.878 | 0.656 | 623 ms |
| RRF Hybrid | 0.833 (25/30) | 0.906 | 0.702 | 1,059 ms |

RRF versus Dense had three complete-hit wins, 25 ties, and two losses. It
recovered the imperfection, geostatic-consolidation, and Explicit-to-Standard
import cases, but lost complete coverage on sequential thermal-stress and
frequency/random-response. All three retrievers completed 12/12 concept cases;
RRF completed 10/12 identifier cases versus 9/12 for each component. On the six
multi-document cases, Dense and RRF both completed 3/6, while Dense had higher
requirement recall (`0.806` versus `0.778`).

This is a one-case net improvement, not stable evidence that Hybrid is generally
better. A post-hoc paired bootstrap gave 95% intervals of `[-0.100, 0.167]` for
complete-hit delta and `[-0.050, 0.122]` for requirement-recall delta; both cross
zero, and the exact sign-test p-value is `1.0`. The dataset is consumed and must
not be used for tuning. The result does not justify replacing the current Agent
retrieval path.

The v3 seal bound the Dense manifest, semantic corpus, embedding name, BM25
database, and five implementation files, but not the Chroma HNSW vector-segment
bytes. A post-consumption snapshot found all five segment mtimes predated the
evaluation and recorded digest `6a4d1d...c42896`; because it was computed after
the first run, it is diagnostic evidence rather than cryptographic proof of the
vectors used then. Future held-out seals must bind this digest before ranking.

To test the remaining reproducibility claim without rewriting the consumed v3
history, a separate `v3-reproduction-r1` seal was created before its first
ranking request. It binds the original seal, report, attempt ledger, and
consumption record; the current five-file HNSW segment; the seven Chroma
MiniLM cache files; Python and retrieval dependency versions; ONNX provider
order; `uv.lock`; and the unchanged BM25/Dense/RRF implementation hashes from
the parent seal. The dedicated wrapper checks these inputs before and after the
batch and publishes report/comparison files with create-new semantics.

The one recorded r1 attempt compared all 30 cases and all three retrievers.
All 90 ordered Top-5 document lists and every non-latency scoring field matched
the first report; latency was intentionally excluded from the identity gate.
This shows an identical replay under the pre-bound current vector/query-encoder
snapshot. It does not retroactively prove which vector bytes were used by the
first v3 run, create a new held-out result, or add evidence that RRF generally
outperforms Dense. The dataset remains consumed and no parameters were changed.

The exact one-shot command was:

```powershell
uv run python -m abaqus_sim_agent.retrieval.reproduction run `
  --index-dir var/rag `
  --seal benchmarks/rag/abaqus_2026_bm25_dense_rrf_heldout_v3_reproduction_r1_seal.json `
  --repository-root . `
  --report-output benchmarks/rag/abaqus_2026_bm25_dense_rrf_heldout_v3_reproduction_r1_report.json `
  --comparison-output benchmarks/rag/abaqus_2026_bm25_dense_rrf_heldout_v3_reproduction_r1_comparison.json
```

The command now fails closed because those output paths already exist; it is
shown to document the completed attempt, not to authorize overwriting it.

Held-out provenance:

- v3 query SHA-256:
  `42e11d2e01e66b9325c5ab85d5625716d6ade31fbade69e9f5d2da07775c46eb`;
- reviewed labels SHA-256:
  `32c51082472265c1c3cbd9871927953e9f256496fb96a4e13be5f3a9be2d1a7f`;
- reviewed family registry SHA-256:
  `a268ef641aa9034da82dd35f9cb1ce9750a205f3d9757ae948cd3bc14a1afaa2`;
- sealed manifest SHA-256:
  `00e43e6dc0274d3ee60cf94c74d457004cf2172ad31d7fd19a0b248b92ff7d39`;
- first evaluation SHA-256:
  `9bb5d7a6e688e0d5a3ecd598c1a252ca3234fb509514886228be9444623fd0a8`;
- post-hoc descriptive analysis SHA-256:
  `6ead11e652ee9b929ea1c6829ac62ee0545069974d715b45ab5390eab56183a7`;
- post-hoc Dense vector snapshot SHA-256:
  `dd502e301fd9670f255b7a905f74854390b4795235e2bc9a1e1f7c99f90e53b6`;
- reproduction r1 seal SHA-256:
  `f2c3552fd4b569847f28c667e5bc4f5c8ff70a2403ba0c6ba0d0ba62310d8958`;
- reproduction r1 attempt ledger SHA-256:
  `f67210b7fa44eb7b2743e82fc0424d0ecb84cd2c7cf82619e0d20da0d7f2d861`;
- reproduction r1 report SHA-256:
  `b06cd0b2478ffa8b9ead954c907e9e3b1ef83547501e2901a2329c36ac3065d4`;
- reproduction r1 comparison SHA-256:
  `7a83be2448dfa934164d24f2151d8e5d6573d490a19bb13c647405b53ff0e42d`.

## Input-surface inventory and diagnostic development stress

The diagnostic work starts from input-generating mechanisms rather than a list
of hand-guessed error strings. `retrieval_input_inventory.py` scans immutable
goals and task revisions, strict Concept/API traces, structured execution
journals, raw stdout/stderr, solver logs, verifier feedback, infrastructure
events, and clarification events. Unclassified structured failures enter an
explicit unknown lane; API exceptions, local-code errors, missing files,
provider failures, and clarification needs are not relabeled as Concept RAG.
Signal occurrences use one normalized family per run, not raw line frequency.

The refreshed 2026-09-04 inventory recognized 273 evaluator/development runs
and 34 unique goals, including one task revision. It found 231 API lookups,
three Concept lookups, and three diagnostic-advisory retrievals. The Concept
lookups all came from one C3D8R/solid-section preflight family; the advisory
lookups came from the RF3 experiment described below. It also found 40
solver-diagnostic run/family observations, 18 verifier-failure families, 63
infrastructure families, and 30 clarification families. Ten runs have the
current strict execution journal, so exhaustive unknown-failure
enumeration applies only to those runs; legacy raw streams remain regex-derived.
This is not production-traffic distribution evidence.

The separate diagnostic development set contains 39 needs and 86 query
variants. Eight needs bind five observed `.dat` files from prior evaluator runs;
four of those eight come from one probe run. The remaining 31 needs are derived
from official diagnostic conditions and intentionally stress lexical retrieval.
Observed needs have a short verbatim excerpt, deterministic normalization, and
reviewed contextualization. Document-derived needs have a condition fragment
and reviewed contextualization. Direct HTML review bound 41 semantic gold pages
and answer sections; audit mapped every Case to the actual index metadata,
yielding 127 ordered per-Case targets and 52 unique
`(document_id, section_id, section_title)` targets.

The primary need-group-macro answer-section results at the product context depth
of four were:

| Retriever | Section Hit@4 | Section MRR@4 | Page Hit@4 |
| --- | ---: | ---: | ---: |
| BM25 | 0.504 | 0.429 | 0.791 |
| Dense | 0.453 | 0.361 | 0.697 |
| RRF Hybrid | 0.521 | 0.427 | 0.838 |

The aggregate hides the useful distinction. On 31 high-overlap official
condition fragments, BM25/Dense/RRF Section Hit@4 was
`0.839 / 0.548 / 0.677`; on the 39 contextualized variants it was
`0.333 / 0.436 / 0.462`. Across the 24 variants of the eight observed log
families it was `0.083 / 0.583 / 0.542`. In particular, BM25 scored `0/8` on
the observed short verbatim excerpts at Top-4: solver messages are not
automatically verbatim copies of the explanatory manual. This directly limits
the common claim that pasted errors always favor sparse retrieval.

The convergence example shows where BM25 is still valuable. For
`too many equilibrium iterations are required for an increment`, BM25 and
Dense both retrieved the exact answer section first. After rewriting it as
`Abaqus/Standard keeps reattempting an increment after excessive equilibrium
iterations`, BM25 kept that section at rank 1, while Dense and RRF missed the
answer section in Top-5. RRF can therefore discard a strong one-sided lexical
hit when overlap among unrelated Dense/BM25 candidates pushes it down.

RRF versus Dense gained `0.068` need-group Section Hit@4 with seven wins,
31 ties, and one loss; Section MRR@4 gained `0.0666` with a 20,000-resample
interval of `[0.0160, 0.1264]`. It did not beat BM25 on MRR, and the set is
development data dominated by document-derived lexical stress. The result is
evidence for preserving separate sparse and semantic lanes and testing a
diagnostic router; it is not evidence to replace the current Agent path.

The first development report was preserved with an explicit attestation gap:
its pre-ranking seal bound vectors and the query encoder but not `lxml`,
Pydantic, or the derived section targets. A subsequent identical-replay seal
bound those dependencies, the 127 ordered targets, the first report and ledger,
and all retrieval parameters before replay. All 86 cases × three retrievers =
258 ordered document/section/title rankings matched, and all non-latency effect
fields had the same canonical SHA-256. This replay is reproducibility evidence,
not a second held-out result and not a retroactive repair of the first seal.

Diagnostic provenance:

- input inventory SHA-256:
  `02d1ee81b77654ad7d8d6fbcc99789188db511eca26f161bc2b20fe2b46efffb`;
- dataset / review SHA-256:
  `9f4b8f386e972049580ae9ae016ac09c59705dd3d86ec630f9147f92ec890e6a` /
  `4a0bfede2e65fca0bc5b52c96ee653b2a2cd7d0846631a0720ddee8e37c464a9`;
- first attempt ledger / report SHA-256:
  `f2af12b60a4a6e8f517555aca70da989e7196420b155b1597fc7bc94c4affd8b` /
  `047ee9adf839d7aec55d95be190a3184a56166bcc843d00e4b0337048fd0a39a`;
- identical-replay seal / attempt ledger / report / comparison SHA-256:
  `8da846bab42fde9b8ada9ccbf0ac2e50aa9cb66b7e11b7f510e62a41be6ff979` /
  `fbf28609eb53cadc2b878a6e3a4ba49d5a390e6016989d9608d9287c364c5c80` /
  `46b034231504341c7f1eff5c02e6d56f4b1d0e8b64534edbf5168a6f14fedf05` /
  `bb7dfbb9fde2565bda8dab3427d58f51a744d2169100c564b54ec3a39662200c`.

## Diagnostic advisory Agent A/B

The offline diagnostic result did not justify replacing the Agent's default
retriever. A narrower, opt-in experiment instead attached RRF evidence only
after a structured Solver diagnostic. Its frozen `goal-identifier-window-v1`
query formulation mechanically combined `CPS4R/plane stress` from the task and
`RF3/OUTPUT/DOF/ACTIVE` from the warning; it did not insert the answer DOFs.
The resulting query ranked the declared Plane Stress Elements answer section
first before the Agent batch started.

The same verified seed, prompt, tools, budgets, model, and private oracle were
used for both arms; both arms also received the same diagnostic-handling system
instruction. The only treatment difference was whether the nested advisory
payload was attached to the Solver tool return.

| Arm | Closed-loop pass | Model requests | Tool calls | Abaqus calls | Solver calls | Advisory calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Control | 3/3 | 42 | 50 | 15 | 6 | 0 |
| Advisory | 3/3 | 38 | 50 | 15 | 6 | 3 |

All six runs reproduced the RF3 warning before editing, changed only the
history-output defect to RF1, removed the final warning, and passed the
independent INP/ODB physics regression. All three treatment traces delivered
the declared gold section at rank 1. The pass delta was zero; Abaqus, Solver,
and total tool calls were identical. Paired model-request deltas were
`-4/-1/+1`, so the aggregate difference of four requests is not a stable saving.
This development Case proves the diagnostic-advisory plumbing and evidence
delivery, not per-run adoption, RAG necessity, or a success-rate improvement.

The expensive six-run Agent batch was fully persisted before the original
finalizer exposed a strict JSON/Python decoding bug. A create-new recovery
program changed only that JSON boundary, retained the pre-run evaluator hash,
and ran the post-batch licensed reference without rerunning an Agent Case. The
phase ledger and 403-file completion seal bind the original evaluator, recovery
program, Agent batch, reference, and all run evidence. The product source now
uses JSON-mode validation and has a persisted-batch finalization regression.

Evidence:

- checked-in [evaluation summary](../coding/plate_output_warning_advisory_evaluation_20260904.json)
  SHA-256:
  `4c4a55b26b052270767f2f381ea5e4c0c8dd806b8e567a193782cbcdc8f6a300`;
- raw Agent batch SHA-256:
  `bf5f159b407db9015a66f695fbfc36aeed8c9337a56c72775a5875f8d188cca2`;
- checked-in [completion seal](../coding/plate_output_warning_advisory_completion_seal_20260904.json)
  SHA-256:
  `26649947fbd5b338e1d3b93e50b7da56c59d463b10823cd51c237fec828779c0`;
- licensed reference result SHA-256:
  `18117b259d74dca408a8aa373c9e2eb625a60c8a3a3b0e95f00b38f74c3e4e8e`;
- recovery program SHA-256:
  `39320c10dcbba7130ccc7059fba2c7cd3237394c43e4f3b094533aa2ac35794c`.

## Product-scope metadata audit

The [2026-09-05 audit](abaqus_2026_product_scope_audit_20260905.json) inspected all
38,913 existing records and their 1,480 source pages, without executing retrieval
rankings or changing index records. Source HTML and indexed IDs/metadata were
rechecked after the audit. Current product labels matched their source-derived
values for every record.

| Label source | Pages | Semantic records | Exact records |
| --- | ---: | ---: | ---: |
| Official Products header | 1,308 | 30,099 | 4,225 |
| Document-ID inference | 26 | 595 | 1,512 |
| No label | 146 | 1,772 | 710 |

All 1,308 official markers were in the page's Products/keygroup block, rather
than arbitrary mentions in prose. An independent text extraction agreed with
all 1,308 parsed product sets. This supports a page-level scope interpretation,
but does not prove relevance to every query or applicability of every chunk.

There are 544 pages labeled for both Standard and Explicit. Of those, 58 have
separate headings naming each product and account for 3,658 semantic records.
The Output Frequency page alone has 27 records whose source-anchor heading
chain names Standard, 20 naming Explicit, and one without a named solver; all
inherit the same dual-product page metadata. Heading names are structural hints,
not automatically validated section-applicability labels.

The audit also detected 333 full-heading/index-title product-name differences
across 132 pages. A minimal splitter reproduction confirmed that a product name
inside a nested span becomes body text while the heading metadata retains only
the direct text. These counts are structural findings, not counts of failed
retrieval queries.

A strict Standard-contains filter would exclude 5,489 semantic records, including
2,289 records with no Standard/Explicit scope. Excluding only pages explicitly
marked for the opposite solver by an official header would exclude 3,019. These
are exclusion counts only; neither policy has been evaluated for relevance or
recall. Comparison questions can require both products.

The audit supports distinguishing official page scope, inferred scope, and
unknown scope, and preserving complete headings before attempting finer filters.
It does not authorize treating dual-labeled pages as uniformly applicable or
missing labels as irrelevant. Production retrieval behavior remains unchanged.
Audit report SHA-256:
`422504e8dc651dd80b022d6ca16829bc1e4365b1ad19c1dced8e9a0f4dced347`.

The subsequent [document-context v2 validation](abaqus_2026_document_context_v2_validation_20260905.json)
implements complete h1–h3 text in a copied HTML tree, full heading paths, explicit
page-product provenance, and separate product mentions from headings. The latter
are context hints and do not overwrite page labels or become applicability gates.
Both active document lookup and Solver advisory expose the new fields. Existing
records without provenance are labeled unrecorded, not retroactively attributed.

All 1,480 source pages were parsed, producing 39,019 records. All 381 product-named
headings inside the configured content roots were preserved. Four source pages
were embedded into a separate 391-record Dense pilot collection at
`var/rag-context-v2-smoke`; reopening it preserved the fields and both evidence
renderers retained the Standard/Explicit chapter distinction. The original
`var/rag` manifest, BM25 manifest, and persistent vector digest remained unchanged.
This is parsing/persistence validation, not a full-index rebuild, a ranking
comparison, or an Agent/Abaqus outcome experiment.

## Full context-v2 index and observed-query replay

The subsequent [2026-09-05 replay](abaqus_2026_document_context_v2_replay_20260905.json)
built a separate full `var/rag-context-v2` collection: 39,019 Dense records from
1,480 pages and 32,572 semantic records in BM25. Dense/BM25 corpus checks passed.
The original `var/rag` remains unchanged and remains the default.

Five actual document queries from the six-scenario behavior smoke were replayed
with their original question/context concatenation, exact terms, product inputs,
Top-4 and exact-symbol-augmented Dense policy. This includes the known false AT
hint. No ranking algorithm or product-filter change was bundled into the replay.
The old index reproduced all 20 historical page/section/match positions.

| Observed query | New-index result relative to old |
| --- | --- |
| ODB assembly node-set access | Same page/section order; member-access answer still missing |
| Riks method and LPF | Material Options replaced by a more relevant Incrementation excerpt, including arc-length information; still partial evidence |
| Riks prescribed-BC follow-up | Loads moves from rank 3 to 2; Boundary Conditions still missing |
| Exact-time warning, first query | Correct Standard explanation absent; AT remains first; wrong-product hits increase from one to two |
| Exact-time warning, second query | Correct explanation absent; AT remains first; one new Explicit chapter appears |

The new wrong-product hits come from an Explicit chapter of the dual-product
`*OUTPUT` page. Its page-level Products list includes Standard: excluding only
opposite-product pages would not remove these two hits. Complete heading context
makes the mismatch visible, but does not itself fix ranking or establish that
the Agent will reject the evidence.

Direct record inspection confirmed the Riks Boundary Conditions and Standard
Time Domain Analysis: Time Incrementation bodies exist in the new index. Their
absence from Top-4 is not a missing-source problem. The returned Explicit
Requesting Preselected Output record is heading-only in both versions. Longer
heading paths also consume the existing 600-character excerpt budget and cut
some Riks body statements earlier; metadata completeness is not body-evidence
completeness.

Both index bindings were saved before their replay and rechecked afterward,
including persistent vectors, all-record content/metadata, and semantic/BM25
hashes. All 1,480 source-page hashes were rechecked. The report preserves queries,
ordered record references, content/excerpt hashes, implementation hashes and
local raw-artifact bindings without copying full official passages here.

This is a post-hoc development comparison of five queries from three scenarios,
not a new held-out test or a single-field causal experiment. BM25 was built and
verified but not used for these active-tool rankings. No model or Abaqus calls
were made; 38 targeted regression tests passed. The evidence does not support an
overall retrieval-benefit claim or a default-index switch. AT extraction,
product filtering, Catalog member coverage and historical reports are unchanged.
