# Development-only passage scoring and profile v3

The user-approved 2026-11-16 workflow keeps the pinned E5 and ONNX bytes and 30
candidates, but permits a small locally fitted passage scorer. This is an
experimental scoring path, not an accepted relevance profile. Its initial target
is the current source corpus, semantic lexicon **v1**, semantic expansion on and
intent off. Lexicon v2 is preserved and requires its own matching evaluation.

## Features and runtime contract

`src/retrieval/scoring.py` fixes feature order under `passage-logistic-v1`:
window maximum, window mean, population window standard deviation, window count,
E5 cosine similarity (`1 - cosine_distance`), query word-token coverage, query
numeric-token coverage and semantic-concept coverage. Window features use the
same query/passage pairs and batch boundaries as existing ONNX inference. No new
model inference or automatic download is introduced.

Word tokens use NFC/case-folded Unicode words; numeric tokens preserve decimal
separators. Coverage is the proportion of distinct query tokens found in a
passage, or one when the query has no such tokens. Concept coverage uses exact
bounded canonical/alias phrases and explicit required/excluded context terms on
both query and passage. It is one when no concept is requested. Semantic-off
has no concept rules. Coverage is a feature, not proof of policy applicability.

The score is `bias + sum(weight * (feature - mean) / scale)`. It is a logit, not
a confidence, answer-completeness certificate or probability. Scoring never
reads document IDs, case IDs, source labels or an answer key. Schema-v3 profiles
embed the fixed feature version/order, means, positive scales, weights, bias and
canonical coefficient digest. The runtime fingerprint binds that digest in
addition to the existing model/batch identity and semantic pipeline identity.
Changing coefficients or lexicon invalidates the previous profile identity.

Schema-v2 continues to authorize only its original raw-max pipeline. Invalid
v3 coefficients reject loading; mismatched pipeline/corpus profiles cannot
authorize filtering. Profile files are still skipped when filtering is explicitly
disabled. No HTTP request/response fields or environment settings are added.
Numeric feature diagnostics are available only through the private offline
collection path and are removed from public retrieval results.

## Development data and fitting

Existing exposed questions and their families are development/regression data.
Renaming or paraphrasing them does not create an independent holdout. New task
compositions also require explicit source and family-independence review.
Source/alias approval does not approve newly proposed retrieval questions or
candidate labels. Never infer missing candidate labels as false.

Collect numeric development features with the paired semantic benchmark:

```powershell
python -m scripts.benchmark_semantic_retrieval --dataset .tmp/scoring/development-collection.json --model-path <local-E5> --model-revision <40-hex> --semantic-artifact artifacts/semantics/v1/lexicon.json --rerank --collect-only --collect-features --requests 0 --output .tmp/scoring/collection.json
```

This draft collection may use regression splits, but is not an acceptance run.
It records feature order and numeric vectors by chunk UUID, without source text
or questions. Review worksheets must hide scores, features and ranks. Actual
human acceptance must bind the dataset/collection bytes and every union UUID's
source label before exporting development training rows.

Use `scripts.prepare_scoring_review worksheet` to produce the score-free HTML,
source worksheet and null-label review template. Its `export` command rechecks
the entire source snapshot and exact candidate sets, requires actual bound
review of case definitions, source evidence, labels and family grouping, and
rejects calibration/holdout input splits. Required sources absent from the
entire collected pool receive explicit `source:document:revision:index`
sentinels so they remain misses in development metrics. They are not fabricated
publication UUIDs. See [commands](../scripts/README.md#evaluation-and-experiments).

`scripts.fit_relevance_scoring` accepts a private JSON with schema version 1,
`split: development`, an actual `reviewed: true` declaration, nonempty review
notes and at least 300 distinct cases. Each row has `case_id`, `intent_group`,
`arm`, `chunk_id`, eight `features`, boolean `relevant` and `required_groups` of
candidate identities. All arms must be paired. Source relevance for a case/UUID
must agree across arms. A required reference absent from the pool remains a
miss, never a reason to discard a difficult case. Calibration/holdout rows are
rejected. Schema checks validate declarations, not the authenticity of review.

```powershell
python -m scripts.fit_relevance_scoring --dataset .tmp/scoring/reviewed-development.json --output .tmp/scoring/candidate.json
```

The deterministic five-fold split is by intent family. Each arm/case has equal
total weight, split equally between available positive/negative classes. Means
and scales are fitted on each training fold only. Nearly constant scales become
one. Weighted logistic fitting uses L2 values `0.01, 0.1, 1, 10`, an unpenalized
intercept and a finite, converged Newton/backtracking solution. Validation uses
each training fold's threshold strictly above its negative scores; it does not
choose a threshold from validation data. Selection prefers zero false-passage
validation results, then fewer errors and higher complete-evidence recall;
ties choose stronger regularization. Full development data then fits the chosen
candidate. The output and training report are **not accepted runtime profiles**.

## Independent evaluation and release

Freeze a reviewed, family-disjoint acceptance dataset meeting the existing
[semantic evaluation requirements](vietnamese-semantics.md#independent-evaluation-and-promotion).
Supply both `--scoring-candidate` and `--scoring-training-report` to the paired
benchmark. It rejects calibration/holdout case or family overlap with development
and binds both private input files. The same scorer is used by both arms;
thresholds are independently chosen from each arm's calibration negatives.
The coefficient file is never passed as `--rerank-profile`.

All absolute and semantic-comparison gates remain unchanged, including >=90%
complete evidence@5 on calibration/holdout, zero irrelevant passages through
top-K20, no unsupported acceptances, designated critical/top-1 checks and at least
100 warmed public requests with p95 <=5 seconds. Semantic expansion must improve
holdout macro-domain nDCG@5 without evidence regressions. Failed checks emit no
accepted semantic profile. Outcome-driven changes require a fresh holdout.

A successful learned-scoring arm emits schema-v3 with the exact evaluated
coefficients. Promote the unchanged emitted semantic profile only after all
gates and actual human review, following the
[versioned artifact policy](runtime-artifacts.md#add-or-promote-a-version).
Include provenance and both inventory entries, then test profile loading and
public filtering against the matching database and configuration. Presence of
a candidate, a synthetic smoke result or an inventory pass is not acceptance.

`scripts.promote_relevance_profile` performs this gated copy for semantic-v1
schema-v3 reports: it requires the exact sibling emitted profile and a bound
actual human-review declaration, recomputes report summaries/comparison, and
checks every semantic gate plus current model/lexicon inventory. A new version
contains byte-identical profile data and a whitelisted provenance manifest;
private report paths, questions and review free text are not copied. See the
[utility command](../scripts/README.md#evaluation-and-experiments).

Promotion uses a cooperative lock, exclusive new-version writes, byte rechecks,
staging and atomic inventory replacement. The version pair and inventory are
not a global filesystem transaction; external writers must coordinate. A
partial/uncertain promotion retains its lock and private reconciliation receipt.
Do not retry or delete the partial version automatically. Successful storage
verification still needs the planned matching-corpus public-path smoke before
declaring the end-to-end task complete. Independent backup remains a separate
operational check and is not established by a second directory on the same disk.
