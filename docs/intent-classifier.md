# Observation-only intent classifier

The optional classifier reuses the original normalized 384-dimensional E5 query
vector. A multiclass logistic head identifies the required source; five binary
heads identify Type A evidence needs. It does not load another transformer,
encode another query, change retrieval, or invoke business tools. mMiniLMv2 ONNX
still ranks passages and the existing relevance profile still authorizes their
release. Intent scores are not evidence relevance or calibrated confidence.

## Runtime contract

`INTENT_MODE=off` is the default. `observe` loads `INTENT_ARTIFACT_PATH` once at
service startup. Relative paths resolve against the repository. CLI equivalents
are `--intent-mode observe --intent-artifact-path <artifact>`. There is no routing
mode, endpoint, response field, database migration, or automatic promotion.

The artifact is JSON containing finite linear weights, intercepts, thresholds,
label order and schema version 1. Its identity binds E5 revision/file fingerprint,
384 dimensions, normalization, `query: ` prefix and preprocessing mode. With
reranking enabled, features use the NFC/whitespace-normalized **original** query,
not the typo-corrected variant. With reranking disabled they use the stripped
original query. A mismatched preprocessing mode makes observation unavailable;
it must never trigger a second embedding or change existing query processing.

Only artifacts declaring reviewed data and all acceptance gates passed are
eligible. This is an operator-reviewed local artifact, not a cryptographic
attestation of human review. Provision immutable files from the evaluation
workflow, retain their reports, and restart after replacement. Runtime uses
NumPy, not pickle or a second ONNX session. Artifact loading errors or classifier
inference errors log fixed safe codes and preserve retrieval. Existing E5,
reranker and database failures retain their existing behavior. Cancellation
drains workers before closing models.

Observation logs contain status, source/style labels, artifact hash and elapsed
seconds only. No questions, source excerpts, embeddings, IDs of people, or
per-class scores are logged. The API entrypoint enables INFO logging; the
retrieval CLI emits only the dedicated safe intent logger to stderr in observe
mode and retains its existing JSON stdout. Python callers configure logging
themselves. No logs or conversations are automatically persisted.

## Labeling guide

| Source label | Meaning |
| --- | --- |
| `type_a` | Requires documentary policy/procedure evidence |
| `type_b` | Requires authoritative operational facts, including current or historical individual records |
| `mixed` | Requires both documentary evidence and operational facts |
| `conversation` | Social interaction without a substantive information request |
| `out_of_scope` | Outside the bot's documented business scope; not merely absent from the current corpus |
| `needs_context` | The request cannot be interpreted without missing references or prior context |

Use `mixed` only when answering actually requires both sources. “I want to know
the leave policy” is A, “How much leave do I have left?” is B, and comparing an
individual balance with policy can require both. Mentioning “I” does not itself
make a question Type B. A policy question whose evidence is missing remains A.
Future B tasks are classification examples, not claims that tools already exist.
The proposed B scope is read-only internal personnel/project/workflow lookup;
execution and permissions remain future backend responsibilities. Unsupported
write requests belong outside this v1 scope.

For `type_a`/`mixed`, assign all explicitly requested styles:
`detail` (specific value/fact), `conditions` (eligibility/exceptions), `procedure`
(steps/order), `enumeration_count` (exhaustive list/count), and `comparison`
(evidence from both sides). Other sources have an empty style list. Do not add
`detail` to every question just because an answer would have details. A procedure
is not automatically a list/count request. Review combined styles against the
actual wording. Unresolved references belong to `needs_context`, while a model
unable to choose a class returns runtime `uncertain`; these are different.

Real datasets live under ignored `.tmp/`. A manifest has `schema_version: 1`,
`reviewed`, nonempty `review_notes` after review, and `cases`. Each case has unique
`id`, `question`, `intent_group`, `split` (`train`, `calibration`, `holdout`),
`source`, `styles`, and boolean `critical`. Minimum 600 cases: per source at least
60/20/20 across splits; each style needs at least 40/15/15 positive examples.
Each training source spans at least three groups. Calibration and holdout must
contain critical cases; every style also needs negative A/mixed examples in each
split. Preserve entire semantic/paraphrase/template families
within one split, including cross-source variants. Automated validation detects
declared group/duplicate leaks, not undeclared semantic leakage.

Human review must verify labels, prospective scope, diversity, source relevance,
and split independence **before** looking at predictions. Names/IDs are fictional.
An agent-authored draft or model-generated labels are not acceptance evidence.
Freeze the reviewed manifest; keep inspected holdout out of future tuning. When
changing the model after inspecting holdout errors, replace that holdout before
claiming acceptance. Record this in review notes. No tool can verify that review
occurred merely because the boolean was changed.

## Training and evaluation

### Unified offline preparation CLI

Use `python -m src.cli.prepare_intents` for repeatable Stage 2 batches. Processing
lives in `src.intent.dataset`, `src.intent.preparation` and `src.intent.training`;
the historical `scripts.train_intent` and `scripts.intent_contract` entrypoints
remain compatibility aliases to the shared implementation. The classifier,
thresholds, quality gates and JSON candidate schema are unchanged.

Copy [the configuration example](examples/intent-preparation.json) to a new
private configuration file, then explicitly adjust its paths as necessary:

```powershell
New-Item -ItemType Directory -Path .tmp/intent-v1 -Force | Out-Null
if (-not (Test-Path .tmp/intent-v1/config.json)) {
    Copy-Item docs/examples/intent-preparation.json .tmp/intent-v1/config.json
}
python -m src.cli.prepare_intents validate --config .tmp/intent-v1/config.json --json
python -m src.cli.prepare_intents preflight --config .tmp/intent-v1/config.json --json
python -m src.cli.prepare_intents run --config .tmp/intent-v1/config.json --json
```

Do not overwrite an existing configuration during setup. The example's draft
dataset is a local authoring artifact, not shipped with a clean clone; supply
your own dataset there. The six mandatory configuration fields are
`schema_version` (integer 1), `dataset`, `model_path`, `model_revision` (full
40-hex commit), `preprocessing`, and `output_root`. Unknown fields are rejected.
`preprocessing` is `rerank_original` or `vector_only`; these map exactly to the
existing artifact identities. All relative paths, including `--config`, resolve
against the repository root, not the caller's working directory. Config,
dataset and output resolve under `.tmp`; model may be an external local
directory. Output cannot reside inside the model directory.

`validate` aggregates independent structural errors by zero-based case index,
including counts and declared split leakage. It imports no ML runtime and
loads no model. `preflight` additionally checks installed training dependencies,
loads local CPU E5 once, verifies dimensions/fingerprint and checks every
question's full token length using retrieval's shared query-input helper.
Preflight does not encode, train or write reports. Both commands can inspect an
unreviewed draft and return technical success with `review_required: true` and
`training_allowed: false`. Structural checks do not verify semantic labels or
undeclared paraphrase/template leakage.

`run` creates a new UTC timestamp/UUID directory and requires reviewed labels
before dependency checks/model loading. It performs preflight before encoding
any question, reuses the loaded model for training and rechecks dataset/model
fingerprints before completion. It neither loads `.env` nor connects to
PostgreSQL, starts ONNX or downloads anything. Vectors remain in RAM; there is
no persistent embedding cache, resume mode or implicit rerun after failure.

The run directory contains `manifest.json`, `validation.json`, `preflight.json`,
`training-report.json`, and `candidate.json` as the corresponding steps finish.
Each file is written through a temporary file and atomic replace inside the
owned run directory. The manifest records normalized config, input hashes,
tool/dependency versions, UTC times, completion, safe error and output hashes.
Only a complete manifest certifies that all final outputs were written; a
leftover report or candidate after interruption is not a completed batch.
`quality_failed` is a complete diagnostic run, not an approved model.
`candidate_ready_for_benchmark` means the offline quality gates passed, not that
runtime may use the candidate. There is no `accepted.json` from this CLI.

For a passed run, the manifest includes benchmark arguments with explicit
profile/output placeholders. Supply an independent matching relevance profile
and run the existing benchmark separately. A `vector_only` candidate is useful
for offline analysis but is incompatible with the calibrated reranking
benchmark; prepare with `rerank_original` before taking that promotion path.

`--json` writes one structured result to stdout; fixed progress stages go to
stderr. Without it, the CLI prints status, errors, training eligibility and the
run directory. Dependency diagnostics are suppressed; questions, vectors and
source excerpts are never printed. Exit codes for the **new CLI** are 0 for a
successful requested step, 1 for structural/token/quality failure, 2 for invalid
configuration, operational failure or a review-blocked run, and 130 for user
interruption. The legacy trainer retains its previous exit-code contract below.

Install offline training dependencies explicitly:

```powershell
python -m pip install -r requirements-intent.txt
python -m scripts.train_intent --dataset .tmp/intent/reviewed-cases.json --model-path .tmp/models/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --rerank-preprocessing --output-prefix .tmp/intent/run-01
```

The command requires reviewed data, uses local CPU E5 with the existing token
limits and never truncates. It makes no database connection. Omit
`--rerank-preprocessing` only for offline analysis of the vector-only path; the
promotion benchmark requires calibrated reranking and thus the matching mode.

Training uses L2 logistic regression, `lbfgs`, maximum 2,000 iterations, seed 42,
and three-fold stratified grouped CV on training data only. Select C from
0.1/1/10 by mean F1 (macro for source, binary for each style), preferring smaller
C on ties. Calibration selects source score/margin thresholds on a 0.01/0.02
grid, maximizing coverage subject to per-class precision >= 0.90 and coverage
>= 0.80; ties prefer lower score then margin thresholds. If infeasible, quality
fails rather than accepting all results. Each style threshold maximizes binary
calibration F1 on 0.05 through 0.95 in 0.01 increments (lower threshold on ties).
Neither coefficients nor thresholds use holdout. No probability calibration is
claimed. NumPy export must agree with sklearn within 1e-6.

Both calibration and holdout require source macro-F1 >= 0.85 before abstention,
accepted precision >= 0.90 **per class**, coverage >= 0.80, and end-to-end style
macro-F1 >= 0.80 on A/mixed cases. Wrong routing/abstention receives no oracle
style credit. Any accepted wrong critical source fails the gate; uncertain
critical cases are counted separately. Reports include confusion matrices,
per-style F1, case IDs/expected and predicted labels/scores, without question text.
The report binds the candidate payload hash and reviewed dataset hash.

Training creates `-report.json` and `-candidate.json`, never an accepted artifact.
Exit codes: 0 for passed quality awaiting benchmark, 1 for failed quality, 2 for
invalid data/model or operational errors. Error output is a fixed code, not
private content. Inputs/outputs must be private, outputs must be new and outside
the model directory. No automatic downloads, `.env` changes, publication or
migrations occur.

## Paired benchmark and promotion

```powershell
python -m scripts.benchmark_intent --dataset .tmp/intent/reviewed-cases.json --candidate .tmp/intent/run-01-candidate.json --training-report .tmp/intent/run-01-report.json --model-path .tmp/models/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --rerank --rerank-profile .tmp/evaluation/relevance-profile.json --requests 200 --output-prefix .tmp/intent/bench-01
```

The read-only benchmark requires all quality/export gates, matching reviewed
dataset/report/candidate identities, and the existing valid schema-v2 relevance
profile. It never bypasses public passage filtering. Missing eligibility,
candidates, or calibration stops it; below-threshold empty results remain valid.
It warms each mode, then alternates off/observe ordering over at least 200 paired
requests using holdout questions cyclically. Repeated requests measure latency,
not additional independent quality cases. One E5/reranker instance is reused.

All retrieval fields except timing must match exactly. Corpus fingerprint must
stay stable. Model/data/candidate/report inputs are rechecked. Head-only p95 and
paired per-request overhead p95 must each be <= 10 ms. This target can fail from
machine load; report the failure without changing the gate. The benchmark does
not establish a production latency SLA. On success and successful cleanup it
writes `-benchmark.json` and `-accepted.json`; otherwise no accepted artifact.
Synthetic regressions cover corpus drift and both model-service and database
cleanup failures; they do not establish a real paired latency result.
Exit codes are 0 accepted, 1 gates failed, 2 operational/prerequisite failure.

Provision the accepted artifact explicitly, set observe mode, and restart. A
relevance profile remains independently required for returned evidence. Rollback
sets `INTENT_MODE=off` and restarts. There is no automatic routing activation.
Entity/time extraction, conversation state, ID resolution, authorization,
Type B calls, and answer generation require separate implementation and review.

## Implementation verification — 2026-11-13

The initial draft `.tmp/intent-v1/draft-cases.json` has 600 cases (120 seed
questions with five surface variants each) and `reviewed: false`. It is a review
aid, not a representative or accepted benchmark. The shared surface templates,
multi-style labels and proposed family boundaries particularly need review and
replacement where necessary. Only document titles were inspected when authoring;
no policy answer/source coverage has been certified.

See README's [current verification](../README.md#current-verification-status) for the latest tests and its historical intent verification entry for the original implementation results. No reviewed
intent training, real quality claim, accepted artifact, paired production-like
benchmark, runtime enablement, database migration or publication is implied by
synthetic tests.

The subsequent Stage 2 CLI implementation additionally passed real local E5
token-only preflight for all 600 draft questions (none exceeded the model limit).
The private report is `.tmp/intent-v1/preflight-cli-report.json`. Validation
reports `training_allowed: false`; an actual run rejected the unreviewed draft
before model loading and left an incomplete `review_required` manifest, with no
candidate. See [Stage 2 verification](../README.md#stage-2-batch-cli-verification--2026-11-13)
for regression results. Token preflight does not establish semantic label quality.
