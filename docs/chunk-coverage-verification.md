# Offline package coverage verification — 2026-11-13

The preparation phase below produced source packages without database publication. The initial authorized publication is recorded in [PostgreSQL publication](#postgresql-publication--2026-11-13); the later [clear-content follow-up](#clear-content-publication-follow-up--2026-11-13) supersedes that checkpoint's held-package counts. None of these phases establishes that every employee question can be answered.

## Current working-tree boundary

The preparation and publication sections below describe their original 2026-11-13 operational snapshots. Subsequent on-disk changes observed during docs/test reconciliation contain 28 `include/active` packages and 524 chunks (27 v2 packages and one v3). The earlier 620-chunk inventory and fifteen held packages are historical. File inspection does not establish who authorized metadata changes, whether these bytes are published, or whether a calibration profile matches them. This reconciliation neither changed packages nor connected to PostgreSQL. See [current verification](../README.md#current-verification-status).

## Prepared corpus

The selected corpus contains 28 PDFs and 334 physical pages: 16 regulations and 12 software manuals. Original assets, existing source snapshots and revision identities are preserved. New manual snapshots match their assets. The final package set contains 620 chunks:

| Work | Result |
| --- | --- |
| Twelve software manuals | 118 manually authored chunks, with page references, procedure steps, roles, required fields and exceptions |
| Labor rules | 64 chunks covering Articles 1–46 and the chapter inventory; leave submission/approval and resignation notice/handover conditions kept together |
| Benefits policy | Fourteen detail chunks shortened while preserving subject, eligibility, amount/unit, required documents and relevant exceptions; schema-v3 IDs and structured evidence retained |
| Fourteen other regulation packages | Existing manual authoring retained, current source identity and structural references validated; previous visual review evidence reused |

The source-unit matrix links extracted units, page locations, chunk references, exclusions, warning decisions and selected evaluation cases. It is not an automatic semantic-completeness proof. In particular, the inherited regulation work is not presented as a newly independent, exhaustive review of every clause.

Manuals describe software operations; policies describe entitlements and conditions. Screenshots and example forms do not establish current balances, salaries, stock, real approvers or account access. Workit and E-Leave remain separate sources. The user's Workit preference is an authoring note, not a fabricated source supersession relation or an online routing rule.

## Local checks and evaluation

Technical checks load the actual local E5-small tokenizer and model on CPU, using revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`. Token counts include the passage prefix and special tokens without truncation. Vectors are generated in RAM, checked for 384 finite dimensions, and discarded. Individual changed-package preflights and the full-corpus diagnostic are distinct from database publication.

The diagnostic uses exact in-memory cosine search, 30 candidates, the existing typo variants/RRF implementation, and the pinned local mMARCO MiniLM AVX2 ONNX reranker with its existing windowing and batch size 4. It intentionally includes hold/unknown packages to test prepared content. It does **not** simulate publication eligibility, pgvector query plans, HTTP behavior or production latency.

The source-authored set has 134 cases: 129 supported, 2 partial and 3 unsupported; at least three cases per PDF. Intent groups were assigned before scoring to regression (57), calibration (38) and heldout (39). Identical travel PDFs share intent groups and alternative evidence, rather than counting their duplicate chunks as independent proof. There are 130 distinct question texts. Expected evidence is source-labelled, not inferred from model scores.

The initial run found complete evidence in top 5 for 126/129 supported cases, including 7/8 supported critical cases. The missed critical leave procedure retrieved submission instructions but omitted the separate approval-role passage. A second run exposed the same fragmentation problem for resignation and handover. Source corrections make both procedures self-contained and update the evidence mappings to recognize their newly included clauses. Initial and second-round cases/results are retained separately. Repeated scoring after these corrections is a regression diagnostic, not a fresh unseen release test.

The final run generated and validated all 620 embeddings; the longest passage used 421/512 tokens. Candidate recall for supported required evidence groups was 129/129 (100%). Dense top-5 coverage was 119/129 (92.25%); reranked top-5 coverage was 127/129 (98.45%), including all 8 supported critical cases. The two strict misses concern duplicate travel-policy questions: returned chunk 14 also contains the complete group definitions labelled only as chunk 4. The frozen primary score is retained without a post-hoc increase. Observed reranking p50 was 1.72 seconds and p95 was 2.76 seconds on this local sequential diagnostic, not a production SLA.

No calibrated relevance profile is emitted: these case labels assess required evidence groups, not every returned passage's relevance. Partial and unsupported questions never receive complete-answer credit. Passing the diagnostic evidence-ranking target does not certify empty-fallback behavior or answer generation. Detailed final measurements and fingerprints are recorded in the task report described below.

## Publication boundaries and remaining uncertainty

Fifteen packages remain on hold: twelve manuals with unverified lifecycle/access metadata, the labor rules with source ambiguities/conflicts, and two previously held regulation packages. Existing confirmed active/public metadata is preserved separately from indexing holds. Preparation and model success do not override these decisions.

Warnings retain conflicting source provisions, ambiguous wording/handwriting, unclear cross-references, inconsistent screen labels and historical sample data. No date-only precedence or legal-currentness claim is introduced. A policy's generic holiday duration is not a year-specific company calendar, and a childbirth cash benefit alone is not the complete maternity entitlement.

No database connection, dry-run publication, migration, stored-vector update, public API request or hosted-model call was used for this verification. There is no new unit-test result claimed for unrelated online changes already present in the working tree.

For eventual rollout, resolve each held package's factual blockers, publish eligible changed packages through the existing workflow, then evaluate the actual eligible publication corpus and create a matching passage-relevance profile through its separate validation gate. Existing profiles cannot be assumed valid after publication changes. See [package publication](offline-indexing.md#scope-2-package-reading-embedding-and-publication) and [online reranking](online-reranking.md).

## Local artifacts

Private source-derived questions and detailed reports remain under ignored `.tmp/chunk-improvement-20261113/`:

- `inventory.json`: source/page/package inventory and original fingerprints.
- `coverage-matrix.json`: source-unit traceability, exclusions, warnings and review limits.
- `evaluation-cases.json`, `evaluation-report.json`: final diagnostic inputs, fingerprints, rankings and measurements.
- `evaluation-initial-cases.json`, `evaluation-initial-report.json`: pre-correction evidence.
- `preflight-*.json`: individual package checks; older reports are historical when their package fingerprint differs.
- `REPORT.md`: final aggregate results, critical-case outcomes and remaining gaps.

These local artifacts are not additional package files and are not deployment inputs. Each package continues to contain only metadata, extraction, chunks and its source snapshot.

## PostgreSQL publication — 2026-11-13

Executed against the existing local bot database using the working-tree packages and pinned E5 model. All 28 packages still match their initial source/package fingerprints. Thirteen eligible packages contain 324 active vectors: twelve were already identical to their active publications, and the benefits package produced one new run with 29 vectors. Fifteen held packages containing 296 chunks remain unpublished. No metadata authorization was inferred or hold removed.

Before writing, the agent verified migration `0005_structured_evidence`, pgvector `0.8.7`, `vector(384)`, metadata compatibility, actual CPU embedding preflight and database-aware dry-run. No migration, `--force` or `--update-metadata` was needed. Benefits source pages 1, 3 and 5 were visually rechecked for eligibility, death-benefit subjects, childbirth support, holiday tiers and payment conditions.

Post-publication read-back passed for all thirteen packages: run/configuration fingerprints, unchanged content and source spans, finite normalized 384-dimensional vectors, and schema-v3 identities/structured evidence. Repeating publication skipped every package as `identical_active_run`. Historical chunks/evidence were preserved; total stored chunks including history increased from 453 to 482 while active chunks remained 324. Package/source hashes and unrelated active revisions remained unchanged.

The read-only actual-PostgreSQL diagnostic evaluated 57 cases; 77 were deferred because required evidence belongs to held packages. Among 53 supported cases, candidate recall was 53/53, cross-corpus reranked top-5 evidence coverage was 51/53, and package-scoped dense top-5 coverage was 49/53. Both eligible supported critical cases passed, with family bereavement and childbirth support ranked first. Broader maternity coverage remains partial. The two strict misses are the previously documented travel-policy label omissions; the score was not increased post hoc. Storage identities and package/model fingerprints remained stable across evaluation. This is regression evidence, not independent acceptance or a per-passage calibration gate.

Private artifacts are under `.tmp/publication-20261113/`: manifest, preflight/dry-run outcomes, publication/run IDs, final storage audit, database retrieval results and `REPORT.md` with every held-source blocker. A repeatable-read logical data snapshot of bot-owned tables was saved with counts/checksum before writing; it is not a full schema/roles backup or a tested restore. The prior benefits run remains stored inactive. No automatic recovery/restore was performed.

This publication task changed no production code, source packages, application environment, API settings or relevance profile. No profile was configured in the loaded environment; independently running processes may differ. New publication IDs change the corpus fingerprint, so any prior profile must be revalidated separately. No API request/restart, generated-answer test or unit/integration test-suite result is claimed for this operational task. A future batch must resolve held lifecycle/access and source ambiguities before publication; no unsupported metadata defaults are introduced here.

## Clear-content publication follow-up — 2026-11-13

The user subsequently confirmed that all twelve software manuals remain applicable and authorized their sanitized text for the existing public retrieval flow. The user selected publication of clear content only, with vector loading before relevance calibration. The fifteen formerly held packages now contain 200 searchable chunks, alongside the unchanged thirteen packages / 324 chunks. All 28 packages are structurally eligible; this does not make every source clause searchable or establish legal precedence. Unlike the separate documentation/test reconciliation above, this operation includes actual model and database verification.

### Authored subset and exclusions

- The 2017 ethics/disciplinary package retains five readable ethics chunks and the blank orientation form. Its 96 header, disciplinary-procedure, sanction and related-note chunks are excluded because of unresolved signatory information or disciplinary applicability relative to the 2023 labor rules. No sanction precedence was inferred.
- The labor rules retain clear surrounding material while excluding conflicting unused-leave payment clauses, the inpatient benefit threshold that differs from the benefits policy, and the ambiguous Article 30.4 decision-issuance clause. A visual recheck of physical page 11 resolved the previously unclear Article 13.6 phrase; extraction and the associated chunk were corrected to the readable source.
- The disposal procedure retains clear rules, blank form fields and signatory roles. Its uncertain process identifier and filled example rows/totals are excluded from searchable references/content. Applicability was taken from source section II, covering departments handling unusable company assets/equipment/goods.
- The maternity manual retains request declaration but excludes the incomplete handover cross-reference. E-Leave retains approval actions but excludes conflicting exact reminder schedules. Workit part 3-2 retains readable screen actions while excluding the conflicting project-menu paths and copied PO step. These excerpts do not establish complete missing procedures.
- The HR manual keeps its existing sanitized extraction/chunks; HSM example values, credentials and private profile examples were not added. Source snapshots remain repository source material, not downloadable assets published by this operation.

Original transcription is retained apart from the source-verified Article 13.6 correction. Exclusions and warning decisions identify unresolved portions without claiming they were resolved. Existing historical hold notes remain as provenance, with dated follow-up notes describing the retained subset. Twelve manuals now use user-confirmed active/public metadata; unknown effective dates remain null. Other confirmed metadata is preserved. All source snapshot hashes and document/revision identities remain unchanged. Existing source reviews were reused with targeted visual rechecks of blocker pages; this is not a new exhaustive transcription audit of all 334 pages.

### Publication and storage verification

All fifteen selected packages passed real local E5 preflight, including in-memory embedding checks: 200 vectors, 384 dimensions, longest passage 421/512 tokens, no metadata blockers. The final database-aware dry-run passed for all fifteen packages. Publication used the existing CLI without migrations, `--force` or `--update-metadata`; each package created one successful active run.

Read-back verified current revision metadata, access groups, run/package/model configuration fingerprints, exact chunk content/source spans, author IDs/roles, finite 384-dimensional vectors and unit norms within 1e-5. The local database now contains 28 current active publications / 524 active chunks; stored chunks including history increased from 482 to 682. Every pre-existing row in all nine bot-owned tables remains unchanged, including the original thirteen publications and schema-v3 evidence. Repeating publication skipped all fifteen packages as `identical_active_run`; a second audit confirmed no additional rows or state changes.

Before writing, a repeatable-read logical snapshot of all bot-owned tables was saved privately with counts and a checksum. It is a data snapshot, not a full schema/roles backup or a tested restore. No rollback or history deletion was performed.

### Checks, artifacts and operational limits

Focused existing package, publication, structured-evidence and preflight suites passed: **106 passed, 1 deselected**. Initial sandbox execution hit Windows temporary-directory permission errors; rerunning outside that permission limitation with a fresh `.tmp/` temporary directory passed. Disabling pytest's cache plugin produced an `Unknown config option: cache_dir` warning. No new tests, production-code changes, dependency installation, disposable-database integration suite, or migration execution was part of this task.

An actual-PostgreSQL offline reranking diagnostic found the source-labelled required evidence in the top five for **12/12 cases**, including the three reported questions. For the broad leave-procedure question, the policy procedure ranked first and Workit declaration fourth. Childbirth cash support ranked first for the maternity-benefits question; that is partial evidence, not full maternity coverage. The dedicated Mid-Autumn benefit ranked second. Remaining cases cover leave notice, maternity declaration, VPN, Outlook room booking, mail, attendance correction, shared-document access, readable ethics and disposal applicability. Labels were authored from the retained source chunks before scoring, and no ranking rules or thresholds were tuned. This small regression set does not establish passage relevance, unsupported-question rejection, independent acceptance or production latency. A separate call to the public filtered function still returned `relevance_unavailable`; no HTTP request was made in this task.

Private artifacts are under `.tmp/held-publication-20261113/`: package/working-tree baseline, original package JSON, authored-change manifest, source-page renders, logical database snapshot, final preflight and dry-run reports, per-package publication results, repeat-publication report and storage audit. Source-derived regression questions and ranking results are private diagnostics, not an independent acceptance dataset or passage calibration profile.

No application environment, API settings or relevance profile was changed, and no API restart was performed. Newly published run IDs change the corpus fingerprint. The public service continues to withhold unassessed candidates until a matching validated relevance profile is provisioned separately; publication success is not evidence that the API will return accepted matches or complete answers.
