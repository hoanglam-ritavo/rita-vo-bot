# Runtime artifacts and calibration operations

Runtime dependencies must survive deletion of `.tmp/`. Keeping files outside Git does not make them disposable or provide a backup. This guide owns local storage, verification, recovery and profile promotion; [online reranking](online-reranking.md) owns calibration semantics and quantitative gates.

## Local layout

| Location | Role | Lifecycle |
| --- | --- | --- |
| `models/embeddings/multilingual-e5-small/<revision>/` | Pinned E5 model files | Immutable while running; provision before startup |
| `models/` | Existing pinned reranker ONNX and tokenizer files directly in this directory | Preserve the supported layout and artifact allowlist |
| `artifacts/rerank/<version>/` | Accepted `relevance-profile.json`, report and provenance manifest | Immutable version; switch configuration explicitly |
| `artifacts/storage-preparation-<date>/` | File inventory and migration/smoke receipts | Local operational evidence, not an independent backup |
| `.tmp/` | Evaluation workspaces, private draft cases, candidates, caches and package staging | Not a runtime dependency; reviewed evaluation inputs still need backup |

`models/`, `artifacts/` and `.tmp/` are ignored by Git. Do not commit real questions, excerpts, credentials or `.env`. Optional accepted intent artifacts may later live under `artifacts/intent/<version>/`; do not create that directory or enable observation before the separate intent gates pass. Experimental answer-model paths under `.tmp/` are outside this runtime migration.

The API defaults to the durable E5 path above. An explicit `E5_MODEL_PATH` still overrides it, including legacy paths; there is no silent fallback to the old directory. API environment paths resolve against the repository root, and process environment variables take precedence over `.env`. The retrieval CLI uses explicit model/rerank arguments. Restart existing services explicitly after changing their configuration.

## Copy, verify and recover

1. Record the source revision and inventory **every** model file by relative name, byte length and SHA-256. Inventory the pinned reranker files too when backing up the full runtime. Store receipts outside model directories: adding a receipt inside E5 changes its recursive file fingerprint.
2. Copy into a new, non-existing durable directory. Reject symlinks/junctions, do not overwrite a conflicting destination, and retain the source. Verify identical relative filenames, lengths and SHA-256 values; recheck the source after copying. Keep the E5 revision and model bytes unchanged so indexed identity remains compatible, without re-embedding or publication.
3. Update only the intended `.env` assignments and current defaults/examples. Check all unrelated parsed settings remain unchanged and keep `INTENT_MODE=off`. A missing profile must remain unconfigured rather than be replaced with a placeholder JSON file.
4. Load E5 from the new location, encode a fictional query and check 384 dimensions. Load the pinned ONNX/tokenizer and verify its startup smoke. Retain the original model until validation and the independent backup/restore check are complete. Do not clean up old `.tmp/` trees incidentally.
5. Obtain an explicit independent destination (another physical disk, external drive or controlled network share). Copy the model files, accepted artifacts, receipts and frozen private evaluation inputs into a new versioned backup directory. Do not include `.env` or credentials; manage credential recovery separately. A second directory on the same physical disk is only local redundancy.
6. Restore from that backup to a new scratch directory, compare the complete file inventory and load the restored model/profile there. Never restore over the running model. Record the destination, inventory hashes and restore results in a local receipt. Until this succeeds, report **independent backup unverified**.

Moving bytes alone is not proof that retrieval works. Confirm package/publication identity using read-only database checks and use the public retrieval path after a compatible profile is available. Migrations, republication and application-data writes are not part of this procedure.

## Prepare, review and freeze calibration

Use the source-review and split requirements in [online reranking](online-reranking.md#evaluation-and-rollout). The offline benchmark writes reports only under `.tmp/`; retain that interface. It does not consume `RERANK_PROFILE_PATH` as an existing calibration input: run it with explicit `--rerank`, model identity and matching batch settings, without `--rerank-profile`.

The 2026-11-14 task workspace is `.tmp/storage-calibration-20261114/`. Start with `HANDOFF.md`. `existing-label-review.json` presents the independent calibration task's 111 questions and 3,330 proposed boolean passage labels for user review, with source content/references and no ranking scores. Original frozen hashes and case-to-label mappings were verified; no user approval was inferred. That separate evaluation failed quality gates and emitted no profile. Do not tune holdout labels to its results.

The supplemental new draft contains `manifest.json`, package-pinned `cases/`, `source-review.json`, `REVIEW.md` and matching read-only corpus snapshots. Collection completed with 3,000 candidate pairs; `candidate-review.json` removes ranks/scores and preserves 2,941 unresolved labels alongside 59 direct-evidence proposals. It is not ready for a full benchmark. These are local, unreviewed artifacts. Source-extraction excerpts aid review; the unchanged original source snapshots remain authoritative. Unsupported cases mix disconnected live facts with out-of-corpus controls and need user review for realism and coverage. Structural validity and minimum split counts do not establish representative semantic quality.

```powershell
.\env\Scripts\python.exe -m scripts.benchmark_online_retrieval --manifest .tmp/storage-calibration-20261114/manifest.json --model-path models/embeddings/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --rerank --collect-only --output .tmp/storage-calibration-20261114/candidates.json
```

Choose a fresh output name when repeating: the tool refuses overwrite. `--collect-only` never emits an accepted profile; an exit code of 1 with a written collection report means rollout gates did not pass, not successful calibration. Exit 2 is an operational/validation failure. Collection still performs the configured latency-request loop; it is not a cheap JSON-only preflight.

Before the full run:

- Review each question, its evidence groups, supported/unsupported status and source fidelity. Confirm that related intents, including duplicate-source/paraphrased cases, never cross calibration/holdout/regression splits.
- Review **every** collected candidate against the source. `candidate_labels` must map exactly the returned chunk UUID set to booleans. A required evidence chunk is not automatically the only relevant passage. Unresolved labels must stay unresolved; never default them to false or derive them from model scores.
- Keep holdout ranking scores out of question/label tuning. Record user approval, hash the manifest/case files and freeze inputs. The current benchmark does not enforce human approval itself: complete this operational gate before invoking it without `--collect-only`.
- Preserve the reviewed dataset and candidate worksheet in the independent private backup. Recollect and review again if candidate identities, packages, corpus or runtime configuration change.

Only after user review, run the same command without `--collect-only` and with a fresh report output, for example `accepted-evaluation.json`. A successful all-gates run writes `accepted-evaluation.profile.json` beside it. A report without a profile is a valid outcome when quality or latency fails; do not hand-author a threshold or set `holdout_passed` to true.

## Promote and verify

Copy an emitted schema-v2 profile into a new `artifacts/rerank/<version>/relevance-profile.json`. Preserve the evaluation report and a manifest recording file hashes, frozen dataset/manifest hashes, E5 revision/fingerprint, reranker fingerprint, corpus fingerprint and exact runtime configuration. Keep private cases/source excerpts in the private backup, not committed documentation.

Verify hashes and compatibility before setting `RERANK_PROFILE_PATH` to that existing file. Restart the API at an agreed time and exercise supported and unsupported questions through HTTP. Distinguish `relevance_unavailable`, `below_relevance_threshold`, `no_candidates`, `no_eligible_documents` and operational failures. Inspect counts/reasons/timings without recording question/document content in operational logs.

`/health/ready` retains its dependency-availability contract. A ready response does not prove profile/corpus compatibility, semantic quality or complete evidence. Perform the profile/retrieval checklist separately; no health or retrieval response schema is added by this storage change.

Corpus eligibility/publication changes or model/pipeline fingerprint changes invalidate reuse until compatibility is checked and calibration repeated where needed. Roll back only to an artifact compatible with the current corpus/runtime. Otherwise preserve unavailable assessment and withhold candidates; neither disabling reranking nor a high raw score authorizes an unfiltered fallback.
