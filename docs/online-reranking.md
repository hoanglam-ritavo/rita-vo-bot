# Online CPU reranking

For durable model/profile paths, backup/restore, review handoff and profile promotion, see [runtime artifact operations](runtime-artifacts.md). Benchmark workspaces remain private under `.tmp/`; accepted runtime profiles belong outside that temporary workspace.

This opt-in pipeline retrieves and filters candidates without modifying packages, publications, database schemas or stored embeddings. It does not generate answers, establish personal eligibility, or certify complete evidence. Missing policy content still requires a separately authorized offline update.

## Local model and configuration

The current fresh database uses corpus fingerprint version 2, binding retrieval metadata as well as publication IDs/configuration. Historical profiles/collections must not be relabeled as compatible. See [database identity and retrieval](database-design.md#evidence-consistency-and-retrieval) before provisioning/calibration.

The supported model is `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, pinned to revision `1427fd652930e4ba29e8149678df786c240d8825`. Place `model_quint8_avx2.onnx` directly in `models/`, alongside `config.json`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json` and `sentencepiece.bpe.model` from that revision. These selected files use byte-preserving Git/LFS storage; run the [runtime inventory check](runtime-artifacts.md#git-lfs-checkout-and-verification) after LFS hydration. Startup validates every file against the pinned SHA-256 allowlist in `src/retrieval/reranking.py`; renaming an unrelated model does not make it compatible.

The ONNX SHA-256 is `6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b`. The official model is available at [Hugging Face](https://huggingface.co/cross-encoder/mmarco-mMiniLMv2-L12-H384-v1/tree/1427fd652930e4ba29e8149678df786c240d8825). Download matching files explicitly during provisioning, never at application startup. Preserve the original downloaded file when preparing another environment.

Install `requirements-example.txt`. ONNX Runtime uses `CPUExecutionProvider`, four intra-op threads and one inter-op thread. The machine must support AVX2. ONNX and tokenizer initialize once; files must remain immutable until shutdown. Missing/corrupt files fail startup. A configured reranker failure returns a sanitized 503, with no automatic vector-only fallback.

| Setting | Default | Meaning |
| --- | --- | --- |
| `RELEVANCE_FILTER_ENABLED` | `true` | HTTP API relevance gate; `false` returns unassessed top-k candidates without loading a profile |
| `RERANK_ENABLED` | `false` | Explicit opt-in; keep disabled until rollout gates pass |
| `RERANK_MODEL_PATH` | `models` | Relative to the bot repository root |
| `RERANK_MODEL_FILE` | `model_quint8_avx2.onnx` | Only the pinned artifact is supported |
| `RERANK_CANDIDATE_COUNT` | `30` | Exactly 30; independent of requested top-K |
| `RERANK_BATCH_SIZE` | `4` | 1–16 windows per inference batch |
| `RERANK_PROFILE_PATH` | unset | Local per-passage profile: schema-v2 raw-max or schema-v3 learned scoring; missing/stale profile returns an empty fallback when filtering is enabled. See [scoring profiles](relevance-scoring.md#features-and-runtime-contract). |

The API reads these settings with the existing explicit `.env` loader. CLI and evaluation use explicit arguments instead: `--rerank`, `--rerank-model-path`, `--rerank-candidates`, `--rerank-batch-size`, `--rerank-profile`. Use identical settings for API/CLI comparisons. Restart with `RERANK_ENABLED=false` to stop rerank inference. This does not disable relevance filtering: with the default filter setting, vector candidates are withheld as `relevance_unavailable`.

`RELEVANCE_FILTER_ENABLED` accepts case-insensitive `true`/`false` only and defaults to `true`; other values fail configuration loading. It is read at HTTP startup, with process environment taking precedence over `.env`. Restart the API to apply changes. `RetrievalService(..., relevance_filter_enabled=False)` provides the same explicit behavior to direct callers; the CLI, benchmarks and evaluators do not read this setting and keep their existing defaults. There is no request parameter to override it.

| Filter enabled | Rerank enabled | Public behavior with candidates |
| --- | --- | --- |
| true | true | Matching profile required; apply threshold then top-K |
| true | false | Withhold candidates with `relevance_unavailable` |
| false | true | Rerank up to 30 candidates and return at most top-K without a threshold |
| false | false | Return at most top-K in vector-search order |

When filtering is disabled, the profile file is not loaded or validated, even if configured and malformed. Reranker artifacts and inference still undergo normal validation. Access, publication, date and embedding-compatibility checks always apply; operational failures do not become successful matches or vector-only fallback. This mode is not calibration acceptance.

## Retrieval and response semantics

Enabled retrieval normalizes NFC and whitespace. An exact phrase-boundary allowlist creates at most one additional query: `thai sảng` becomes `thai sản`; `nguyên đáng` becomes `nguyên đán`. Original and corrected queries both retrieve candidates. No general spelling model, hosted service, or semantic rewriting is used.

Each query retrieves up to 30 candidates in one shared read-only REPEATABLE READ snapshot. Eligibility/model compatibility filters remain unchanged. Results are deduplicated by chunk ID and fused using reciprocal rank fusion (constant 60), with ID tie-breaking. Inventory/table roles remain searchable. The selected pool is reranked by the corrected query when present.

The reranker consumes XLM-R query/passage pairs, without E5 prefixes. Each pair fits 512 tokens. Long passages use overlapping windows (64-token overlap) and the maximum window score; all passage tokens are processed. Questions leaving 64 or fewer passage tokens return 422 rather than being truncated. This is an additional enabled-mode limit. Complete original chunk content and provenance are returned, including conditions outside the highest-scoring window. A high window score therefore does not certify every condition or full evidence coverage.

`top_k` remains 1–20. Matches sort by descending `rerank_score`, then ascending original-query `cosine_distance`, then chunk ID. Cosine distances are always relative to the normalized original query, including candidates discovered only through its corrected variant; they no longer determine the final order.

The additive HTTP fields are:

- `matches[].rerank_score`: raw finite logit, or null in vector-only mode. Neither logits nor sigmoid values are calibrated confidence probabilities.
- `relevance.status`: `unassessed`, `low_relevance`, `relevant_candidates`, or `no_candidates`.
- `relevance.warnings`: always includes `evidence_completeness_not_assessed`; low relevance also includes `low_relevance`.
- `timings.rerank_seconds`: tokenizer and ONNX scoring time; included in existing retrieval/total timing, not an additional interval to sum.

With filtering explicitly disabled, returned candidates use `relevance.status: unassessed`; empty outcomes use `no_candidates`. Both include `relevance_filter_disabled` and `evidence_completeness_not_assessed` warnings. Nonempty output is `matches_found` with `fallback_reason: null`, not a claim that relevance was validated. `rerank_score` is null and `rerank_seconds` is zero if reranking is disabled. The HTTP schema is unchanged. The backend mapper currently accepts these responses but does not forward relevance metadata to the portal; enabling this mode does not add a portal warning.

Existing top-level statuses remain unchanged. `top_k` is an upper bound, never a target to fill. With filtering enabled, after reranking retain only passages with raw score **greater than or equal to** the calibrated threshold, then take at most top-K. The remaining passages retain their original content and provenance. With filtering enabled (the default), a matching profile is required even for a very high raw score; there is no universal cutoff or automatic vector-only fallback.

Every HTTP/CLI result includes required nullable `fallback_reason`:

| Status | Matches | Reason |
| --- | --- | --- |
| `matches_found` | 1 through top-K | `null` |
| `no_matches` | empty | `below_relevance_threshold`, `relevance_unavailable`, or `no_candidates` |
| `no_eligible_documents` | empty | `no_eligible_documents` |

Eligibility/empty candidate outcomes take precedence over unavailable assessment. With filtering enabled, disabled reranking, missing profiles, legacy profiles, failed holdout or mismatched fingerprints produce `relevance_unavailable`, not proof that no policy exists. Invalid configured profile JSON/fields fail model startup when both reranking and filtering are enabled. Model/database/timeout failures remain operational errors. Relevance metadata describes the candidate assessment; it never certifies evidence completeness. With filtering enabled, low-scoring and unassessed candidates are not exposed through HTTP or the retrieval CLI.

Schema-v2 profiles contain `schema_version: 2`, `fingerprint`, `corpus_fingerprint`, finite raw-logit `threshold`, and `holdout_passed: true`. Model artifacts, revision, pipeline version, candidate count, batch size and ONNX Runtime version participate in the model fingerprint. The corpus fingerprint covers compatible eligible active run IDs, revision IDs and publication configurations, computed in the same read-only snapshot as retrieval. Publications must remain immutable; changed publications/eligibility require new calibration. Legacy top-1 profiles cannot authorize per-passage filtering.

One request runs at a time in the API; concurrent calls still receive 429. Cancellation and shutdown drain active model work; the existing 120-second deadline is retained. The five-second p95 is a rollout target, not the HTTP deadline. Operational logs contain counts, status and timings, not question/document content.

## Evaluation and rollout

The user-authorized learned-scoring experiment adds an optional local scoring
layer and schema-v3 profiles without changing the pinned models or acceptance
gates. It uses the paired semantic evaluator, not the baseline preparation CLI.
See [development-only passage scoring](relevance-scoring.md) for feature,
training, review and compatibility requirements. No accepted v3 profile is
supplied by the implementation itself.

### Diagnose empty results before recalibration

First distinguish unavailable assessment (`relevance_unavailable`) from scored rejection (`below_relevance_threshold`). Check the effective process configuration separately from `.env`: environment overrides and a previously started process can differ from the current file. Do not expose credentials or assume readiness establishes profile compatibility.

Use the existing offline benchmark's `--collect-only` mode for known failed questions, keeping them and typo comparisons in regression with shared intent groups. Required groups are source-reviewed evidence proposals, not proof of complete answers. Keep unrelated published data, model configuration and runtime settings fixed. Collection also runs at least 100 latency requests, never emits a profile, and is not a successful calibration.

`python -m scripts.diagnose_retrieval` then consumes that saved collection; see the [utility reference](../scripts/README.md#evaluation-and-experiments). Its diagnostic compares required evidence ranks before/after reranking and, optionally, scores against a compatible historical cutoff. That cutoff is never accepted as a runtime profile. Its separate private worksheet sorts candidates by package/index and includes retained extraction excerpts, unresolved review labels and no scores/ranks/distances. It does not change existing labels or infer user approval. Read-only package validation may create workspace lock files under `.tmp/`.

The tool checks manifest identity, target-package case pins, candidate sets, complete existing labels when supplied, and input hashes. It does not query PostgreSQL or independently prove that current non-target source bytes match a historical publication. Collect against a verified immutable corpus and retain before/after package and database receipts; do not use the worksheet as an independent publication-integrity check.

Review high-scoring negatives and rejected direct evidence against the source before changing any labels. Similar vocabulary in different forms/workflows does not establish relevance. A threshold above every calibration negative can reject many correct passages when scores overlap; lowering that threshold is not evidence that quality gates pass. Do not rerun the same failed configuration simply to obtain another report. Any development informed by old holdout results requires a new independent holdout before acceptance.

Technical parity should compare native tokenizer pairs/windows and ONNX outputs with identical inference batching. Quantized batch-versus-singleton score differences alone do not prove an owner-mapping or tokenizer defect. Keep calibration/runtime batching aligned and record tolerances. Model-quality acceptance remains separate from implementation parity.

`scripts.evaluate_retrieval` evaluates the public filtered result. Without a valid profile it observes empty fallbacks. `scripts.benchmark_online_retrieval` has a separate offline-only candidate path; no HTTP parameter or public retrieval CLI option can bypass filtering. Only the HTTP server configuration or an explicit direct-service option can disable the gate; benchmark calibration and acceptance remain unchanged.

Prepare a `.tmp/` manifest with `datasets` containing `package` and `cases` paths. Retain the existing pinned revision/chunks hash and `required_groups` schema. Each case requires a globally unique ID/question, `split` (`calibration`, `holdout`, `regression`), `relevant` boolean, and `intent_group`. Related paraphrases must share one intent group and never cross splits. Known inspected questions belong in regression. All evidence and relevance labels require source review, not model-score inference. Use the [baseline preparation CLI](#prepare-baseline-calibration-inputs) to import and validate a draft before collection.

The gate requires at least 100 questions: at least 20 supported and 20 unsupported each in calibration and holdout, plus 20 regression questions. Use `--collect-only` first to obtain candidate identities for labeling; this permits smaller exploratory sets, emits no profile and never reports rollout readiness. Every case in a final evaluation needs `candidate_labels`, a dictionary mapping **every** retrieved top-30 chunk UUID to a boolean (directly supports the requested answer versus merely related/irrelevant). The exact UUID set must match the current retrieval. Source review may accept multiple chunks/documents; required evidence groups alone are not exhaustive relevance labels. Do not tune against holdout rankings. Reports retain IDs, ranks, scores and decisions, not question/excerpt text.

```powershell
.\env\Scripts\python.exe -m scripts.benchmark_online_retrieval --manifest .tmp/fallback-evaluation/manifest.json --model-path models/embeddings/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --rerank --collect-only --output .tmp/fallback-evaluation/candidates.json
```

After source labeling and freezing the dataset, omit `--collect-only` and choose a new output filename. Evaluation is read-only: no migration, publication or stored-vector changes. Package/storage/model/corpus identities must remain stable throughout. Real manifests/reports remain under ignored `.tmp/`; committed tests use fictional content.

Calibration picks the lowest finite threshold strictly above all calibration negative-passage scores. Gates require no irrelevant returned passages (checked through top-K 20), no unsupported acceptances, and at least 90% supported complete evidence at top-K 5 on both calibration and holdout. All critical and required-top-1 cases must pass after filtering. Reports include false-empty counts, removed/retained counts, candidate-pool completeness and post-filter group ranks. At least 100 sequential warmed requests must have p95 at most five seconds. Latency uses the public filtering path with a provisional in-memory threshold; the profile is not written until all gates pass.

A schema-v2 profile is emitted only if every gate passes. Otherwise leave assessment unavailable; do not lower gates, widen candidate counts, substitute a model or introduce document-specific rules. The historical 40-question top-1 evaluation is not sufficient for this gate. Passing synthetic tests does not establish real retrieval quality.

### Prepare baseline calibration inputs

`src.cli.prepare_rerank` prepares baseline E5/ONNX evaluation files; it does not
generate reviewed questions, infer labels, access the database, load models or
issue profiles. Semantic expansion and intent assistance need their separate
paired workflow. The existing storage-calibration draft is an optional local
starting point, not shipped with a clean clone and not accepted evidence.
Do not import historical inspected holdout questions as a new independent holdout.

Run from the repository root with the activated environment. Choose a new output
directory; existing destinations are never overwritten:

```powershell
python -m src.cli.prepare_rerank init --manifest .tmp/storage-calibration-20261114/manifest.json --output-dir .tmp/rerank --json
python -m src.cli.prepare_rerank validate --manifest .tmp/rerank/manifest.json --stage collection --json
```

`init` preserves source files, validates package eligibility and canonical pins,
copies cases to `cases-000.json`, etc., and writes `manifest.json`, `receipt.json`
and `review.json`. Proposed labels remain proposals. A stale revision/hash is
rejected rather than silently replaced. Receipt hashes record provenance, not
semantic accuracy. Initialization defaults to the pinned E5 directory/revision,
`models` for ONNX and batch size 4; override `--model-path`, `--model-revision`,
`--rerank-model-path` or `--rerank-batch-size` explicitly when needed. These paths
are recorded, not loaded or verified at this step.

Complete source review of questions/evidence and split independence before
collection. Keep known inspected families in regression, add fresh independent
families and preserve at least 20 supported/20 unsupported cases in each of
calibration and holdout, plus 20 regression cases. Do not simply shuffle cases
to meet counts. Validate the final authored files again.

The receipt's `collection_arguments` is a JSON argument array, not shell-quoted
text. Current preparation receipts include `--rerank-model-file`, while the shared
CLI pins that filename and does not expose the flag. Verify the value is exactly
the supported default, omit only that flag/value pair for execution, and preserve
the original receipt. This does not change the effective model configuration or
skip artifact validation. Apply the same normalization to frozen
`benchmark_arguments`. In PowerShell:

```powershell
$rerankReceipt = Get-Content .tmp/rerank/receipt.json -Raw | ConvertFrom-Json
$rerankArgs = @($rerankReceipt.collection_arguments | Select-Object -Skip 1)
$rerankFileFlag = [Array]::IndexOf($rerankArgs, '--rerank-model-file')
if ($rerankFileFlag -ge 0) {
    if (($rerankFileFlag + 1) -ge $rerankArgs.Count -or
        $rerankArgs[$rerankFileFlag + 1] -ne 'model_quint8_avx2.onnx') {
        throw 'The receipt does not select the supported pinned reranker file.'
    }
    $rerankArgs = @(
        for ($rerankArgIndex = 0; $rerankArgIndex -lt $rerankArgs.Count; $rerankArgIndex++) {
            if ($rerankArgIndex -ne $rerankFileFlag -and
                $rerankArgIndex -ne ($rerankFileFlag + 1)) {
                $rerankArgs[$rerankArgIndex]
            }
        }
    )
}
& $rerankReceipt.collection_arguments[0] @rerankArgs
```

This separate step loads E5/ONNX and reads the application database; it needs
installed runtime dependencies, compatible active publications and matching
model files. It performs at least 100 latency requests even in collection mode.
Collection exit `1` with a written report is expected and does not mean acceptance;
exit `2` means operational/input failure. Output names must be fresh.

Before editing candidate labels, create an unranked source worksheet:

```powershell
python -m scripts.diagnose_retrieval --manifest .tmp/rerank/manifest.json --collection .tmp/rerank/collection-01.json --output .tmp/rerank/review-01
```

Review every candidate against the source and populate `candidate_labels` in the
copied case files with exact candidate UUID keys and boolean values. Null is
unresolved, not false. Only `candidate_labels` may change after collection; edits
to questions, evidence, split, case metadata or packages require a fresh collection.
Do not edit the manifest. Complete the five boolean declarations and nonblank
`review_notes` in `review.json` only after actual review. In particular, confirm
source evidence, split independence, inspected families in regression and all
candidate labels. The CLI cannot independently attest human review.

```powershell
python -m src.cli.prepare_rerank validate --manifest .tmp/rerank/manifest.json --stage calibration --json
python -m src.cli.prepare_rerank freeze --manifest .tmp/rerank/manifest.json --collection .tmp/rerank/collection-01.json --review .tmp/rerank/review.json --output-dir .tmp/rerank-frozen-v1 --json
```

`freeze` requires a new-format baseline collection with input binding and
reproduction arguments, unchanged manifest/packages/authored case fields, exact
candidate labels, full split counts and completed review. Older collections
remain valid diagnostic inputs but must be recollected for this freeze workflow.
The new directory contains copied manifest/cases, review and a receipt with input
and output hashes, collection identity and `benchmark_arguments`. Preserve it as
a frozen private snapshot; hashes detect drift but do not make files immutable.
Execute its `benchmark_arguments` array in the same way as above to run full
calibration, with the collection's exact model/batch configuration and no
`--collect-only`. Database/corpus/model drift is still checked by the benchmark;
preparation does not establish live compatibility or successful quality gates.
No existing profile is supplied to this benchmark.

All preparation paths resolve against the repository root. Manifests, cases,
review, collection and output stay under `.tmp/`; packages stay under `chunks/`.
Errors use fixed codes and file/case-index locations without question/excerpt
content. Package coordination locks may be written under `.tmp/`. Exit `0` means
the preparation step completed, `1` a validation/review rejection, and `2` an
operational/usage failure. No `.env`, service, publication or model bytes change.

The fictional fixtures in `src/tests/test_prepare_rerank.py` demonstrate complete
manifest/case/review/collection structures and failure cases. They are synthetic
contracts, not a real calibration dataset. See [artifact promotion](runtime-artifacts.md#promote-and-verify)
only after the independent benchmark emits a genuinely passing profile.

Deploy bot and backend together during maintenance, draining old processes first. The backend requires the new reason field; an old bot response is invalid, not empty evidence. With filtering enabled, until a matching profile is provisioned, available documents yield `no_matches/relevance_unavailable`. `/health/ready` still measures dependencies, not calibration quality. Existing portal status handling remains compatible but does not yet display reason-specific messages. Logs include only the filter-enabled setting, counts, status/reason and timings.
