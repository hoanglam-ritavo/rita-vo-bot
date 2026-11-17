# Development exchange contract v1

The input ZIP has exactly `development.json` and `manifest.json`. Output ZIPs
have exactly `candidate.json`, `training-report.json`, `compatibility-report.json`
and `manifest.json`. Members are regular files; encrypted, linked, duplicate,
additional and path-containing entries are rejected, without extracting them.
Maximum total uncompressed input/result size is 64 MiB. Choose a separately
reviewed contract revision for larger collections; do not silently omit cases.
Existing output paths are never overwritten. JSON rejects duplicate keys and
NaN/Infinity. Hashes are SHA-256 of exact file bytes unless defined otherwise.

## Development data

Top-level fields are `schema_version: 1`, `split: development`, `reviewed: true`,
fixed `review_notes`, `version: passage-logistic-v1`, ordered `feature_names`
and `rows`. Each row has only `case_id`, `intent_group`, `arm`, `chunk_id`,
`features`, boolean `relevant`, and `required_groups` (AND of groups, OR within
each group). IDs/group names are retained so local acceptance can detect
development-family leakage. They must not contain confidential free text.

Feature order: window maximum, mean, population standard deviation, count,
`1-cosine_distance`, distinct word-token coverage, numeric-token coverage and
semantic-concept coverage. Definitions and normalization are byte-preserved in
`relevance_kit/scoring.py`. Empty query sets yield coverage one. Source conditions
and exclusions participate in concept matching. Features cannot certify evidence
completeness. Source snapshots use UTF-8/LF for checkout portability; package and
exchange hashes use exact bytes. Never regenerate these vectors with a different GPU reranker and
assume equivalence with the deployed quantized CPU model.

Validation preserves the upstream constraints: >=300 cases, at least five intent
families, paired baseline/semantic arms, finite length-eight vectors, no
duplicate candidates or inconsistent source labels/evidence. Every validation
fold needs both labels. Calibration/holdout rows are rejected. Missing evidence
sentinels remain misses rather than being removed to improve fitting metrics.

## Candidate and report

Candidate fields are exactly `version`, `feature_names`, `means`, `scales`,
`weights`, `bias`, `digest`. There are eight finite values per vector; scales
are positive. Digest uses canonical sorted UTF-8 JSON of every field except
`digest`, compact separators, Unicode preserved and nonfinite values forbidden.
This is a logit, not a probability. No threshold or runtime-profile fields exist.

Training matches the canonical family-hash five-fold algorithm, equal case/arm
and present-class weights, training-only normalization and thresholds, L2 trials
0.01/0.1/1/10, Newton/backtracking and unchanged selection rules. Reports retain
fold assignments, normalizations, trials, input/normalized-row hashes and case/
family ledgers. They declare `training_only=true`, `holdout_evaluated=false`,
`accepted_profile=false`. Import re-fits rather than trusting these declarations.

The compatibility report is diagnostic only: CPU comparison and optional CUDA
FP64 comparison include errors and tolerance; GPU also reports rank-position
changes. Missing GPU is skipped. No GPU result participates in fitting,
calibration or acceptance. Synthetic bundles and results are visibly marked in
their manifests and local import receipts, and are not acceptance evidence.

## Final runtime profile

This kit does not author a runtime profile. Existing local acceptance emits
schema_version 3, evaluated pipeline fingerprint, target corpus fingerprint,
finite threshold, holdout_passed true and the evaluated scoring object. Reports,
review declarations and manifests remain required for promotion. Same source
documents published independently do not guarantee the same corpus identity.
