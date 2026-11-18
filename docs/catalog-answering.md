# Catalog tasks and current evidence

## Runtime contract

The bot recognizes bounded internal tasks using source-checked phrases, context
guards and semantic concepts. It has no learned intent classifier, relevance
threshold profile, learned passage-scoring layer, hosted service or generator.
E5 and the optional pinned ONNX reranker still retrieve/rank document candidates.
Neither distance nor reranker score certifies relevance, correctness or completeness.

`POST /api/v1/retrieve` and the retrieval CLI return at most top-K candidates.
Nonempty results have `relevance.status=unassessed` and warnings
`relevance_not_assessed` and `evidence_completeness_not_assessed`.
Empty results distinguish no eligible documents from no candidates. Legacy
relevance enums/fallback literals remain in the wire schema for compatibility;
the new runtime does not produce profile-related fallbacks or assessed relevance.

`POST /api/v1/answer` selects one unambiguous task and the requested aspect.
It reads all required authored references directly from eligible publications
in a single readonly REPEATABLE READ snapshot. Top-K limits candidate lookup,
not template evidence. Each reference must match document/revision/index,
source SHA-256, complete chunk content SHA-256, exact quote and provenance metadata.
Current/active/public access, dates and E5 compatibility are checked in SQL;
an authored reference never grants access. Duplicate addresses do not choose an
arbitrary publication. Missing references cannot be filled from disk or old claims.

Every claim requires all of its references. Independently supported claims may
produce a partial answer; complete declared aspects can produce `answered`.
This is authored scope completeness, not proof of exhaustive policy coverage.
Ambiguous, aggregate, personal-eligibility and multi-task questions ask for
clarification. Unknown tasks return insufficient evidence without nearest excerpts.
An explicit document request such as `tìm tài liệu` may return bounded verbatim
excerpts, always labeled partial and unassessed. No automatic policy precedence
or conflicting-source synthesis is implemented.

Type B retains its bounded self-reference task registry and readonly adapter.
It requires the trusted backend service key and authorized user ID, preserves
nulls and requested-field limits, and has no classifier hints. Mixed answers
separate policy from personnel facts without deciding personal entitlement.
Unavailable policy templates do not block an otherwise supported Type B answer.

HTTP request/response field names and existing answer statuses remain compatible.
Generation/verification times remain zero. Busy limits, deadlines, worker draining
and safe operational failures remain in place. Operational logs contain fixed
codes, generated request IDs, outcomes and timings, never questions/source text.

## Authoring and schemas

Author language, tasks and optional retrieval metadata in their declared `artifacts/semantics/source/`, `artifacts/answers/source/` and `artifacts/retrieval/source/` inputs. See [cross-domain routing and evaluation](business-topics.md) and the [leave regression](leave-retrieval.md).
The source index envelope remains version 1; its metadata selects runtime schema 2.
Schema-1 answer catalogs/lexicons are rejected; the selected retrieval catalog uses schema 2. Deploy the code and all selected artifacts
together; runtime performs no implicit conversion or version discovery.

The lexicon defines guarded concepts and aliases. The catalog defines stable task
IDs, task labels, aliases/concept IDs, required/excluded context, a clarification
prompt, `source_type=type_a`, and aspect sections. Evidence units have stable IDs,
claim text and exact source references. Reuse an evidence ID only with identical
content/bindings. Keep IDs when moving chunks; do not derive new IDs from positions.
Adapters normalize source fields and preserve provenance/nulls; policy facts belong
to source-bound units, not duplicated adapter constants.

Package fingerprints record the authoring snapshot. They do not globally disable
runtime tasks when unrelated packages change. The lexicon hash still binds the
catalog to its exact language artifact. Runtime validates structure/hash pairing;
source drift is checked per reference against current DB evidence. Corrupt artifacts
disable policy answers, while stale evidence only withholds dependent claims.

The maintained inventory has 29 packages / 530 chunks, 279 tasks and 320 concepts,
including cross-domain tasks and recognition-only topic/platform concepts. Partial
sections retain source conditions; existing nine complete sections preserve their
declarations. Optional routing and bounded evidence supplementation are described
in the cross-domain guide. Additions were source-checked by the implementation
agent; no human review or independent live quality acceptance is inferred.
Every chunk has a disposition; `unavailable` means no maintained template binding,
not proof that the source is absent. Held/unpublished sources remain runtime-ineligible.

## Updating chunks

After an authorized package edit, run from the repository root:

```powershell
python -m src.cli.build_artifacts impact
python -m src.cli.build_artifacts refresh
python -m src.cli.build_artifacts build
python -m src.cli.build_artifacts check
python -m src.cli.check_answer_catalog
```

`impact` is readonly and reports task/unit IDs, affected question IDs, reference positions and fixed
states without source content. `refresh` is an explicit source write: it relinks
only unique, byte-identical complete chunks with unchanged revision, source hash
and bound metadata. It refreshes coverage and authoring snapshot fingerprints.
New unbound chunks become unavailable. Generated runtime files are untouched
until `build`. Refresh is not publication, approval or acceptance.

Content changes, changed revision/context/source hash, missing sources and ambiguous
matches require targeted source review. Those references remain stale until the
author deliberately updates the affected units/templates. Split/merged chunks
require manual reconciliation unless a complete identical chunk is uniquely found.
Never repair drift by editing hashes alone. Retain the original quote, conditions,
exceptions and unresolved conflicts. Review dependent claims whenever meaning changes.

Source files are staged and replaced individually, then built as a compatible pair.
The source tree is not a live transactional store: serialize authoring/builds and
use an isolated release checkout, never refresh or build inside a running service tree.
If interrupted, inspect sources and rerun impact/build/check before release.

## Verification and release

Run non-integration tests, artifact build/check, impact, catalog validation and
runtime inventory verification. Exercise supported questions, near misses,
negation, different subjects, missing/withdrawn sources, revision/content drift,
permission loss and independent unaffected tasks. Real cases/reports stay in
ignored `.tmp`; authored examples are exposed regressions, not independent quality
acceptance. The template benchmark records outcomes; it does not issue acceptance.

Release code, lexicon/catalog and manifests/inventory as one immutable set.
Update inventory hashes after an intentional build. Publish packages separately
using the existing reviewed operational workflow; no DB schema migration is needed.
This implementation does not publish application data or restart a service.
Before deployment, verify the target DB publication and run supported/unsupported
HTTP smoke tests. Roll back a compatible code/artifact set, checking DB revisions;
a Git rollback does not restore database content.

The retired environment names `RELEVANCE_FILTER_ENABLED`, `RERANK_PROFILE_PATH`,
`INTENT_MODE` and `INTENT_ARTIFACT_PATH` are ignored, with name-only startup warnings
if inherited from a launcher. Remove them from external launchers during deployment.
Training/calibration/promotion scripts, related Colab kits and the offline intent
dependency file have been removed; Git preserves history. Models, packages,
private experiments and installed environments are not deleted.
