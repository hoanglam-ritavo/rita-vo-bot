# Cross-domain tasks, evidence ordering and follow-ups

## Current scope

The inventory covers 29 packages / 530 chunks, with 279 tasks and 320 language
concepts. Topics include labor/leave/attendance/overtime/termination,
benefits/referrals, travel/expenses, administration/seals/assets/uniforms,
vehicles, security/building rules and Workit, E-Leave, E-Workdate, E-Postal,
Outlook and Bravo manuals. Counts describe authored scope, not complete answers
or target database publication. Partial sections remain explicitly partial.

Administrative context, historical tables and conversation clarification records
retain explicit excerpt dispositions. Clarification records are not issued
policies. Same-label sources retain distinct IDs and require a source choice;
neither filenames nor equal source bytes establish policy precedence.

## Registry and execution

Retrieval schema 2 replaces the closed leave-only enums with declared business
families, platforms, subtypes, operations, guarded routes and source bindings.
Source index envelopes remain schema 1; answer and lexical schemas remain 2.
The loader retains retrieval schema-1 compatibility for isolated older sets; the
selected artifact is authored as schema 2. No automatic artifact migration,
training, learned scoring profile, hosted model or additional env is introduced.

Routes declare aliases/concepts, context guards, optional required platform/type
slots and prompts. Bindings may share addresses across tasks, retaining exact
references, source roles and answer aspects. Build validation rejects unknown
declarations/tasks/concepts, duplicate bindings and oversized per-aspect bundles.
More specific overlapping phrases supersede shorter phrases. Separate topics
remain ambiguous. Punctuated contrasts can exclude another topic; ordinary
negation is preserved. Recognizing a task never performs an operational action.

After raw reranking, stable ordering prioritizes exact valid task evidence,
requested aspects, platform and role. Unbound/stale candidates stay neutral;
other bound topics follow. Raw cosine distances and reranker scores remain
unchanged. Preserve server response order. Candidates remain unassessed.

One resolved task may supplement retrieval with at most 10 declared references
for its requested aspects. Addressed reads and vector searches share one readonly
REPEATABLE READ transaction and identical access/current-active publication,
effective-date and E5-compatibility filters. Injected records must also match
source/content hashes, quote and metadata. Distances use the original query and
stored vectors; no re-embedding occurs. The union keeps linked records, then
nonduplicate original candidates, at most 30 total. Ambiguous tasks never inject
evidence. Oversized bundles are not silently truncated. Public top-K is unchanged.

Answering reads required references directly, independently of top-K. It renders
requested aspects and preserves conditions/citations. Partial answers display
at most three independent claims; complete answers retain the existing 12-claim
bound. Missing evidence never comes from disk/history. There is no entitlement
computation, business mutation or live software-status lookup.

## Conversation and personnel boundaries

Context JSON schema 2 stores task/candidate/aspect state, bounded pending queries
and user-text slot values with their origin. Exact slot replies finish a pending
question; explicit changes replace previous slots. Unknown clauses, negation or
third-person requests are not appended to positive tasks. Source UUIDs survive
normalization; topic changes discard incompatible state.

Identity includes reducer version, answer/lexical catalogs and retrieval
fingerprint. Incompatible context starts fresh without rewriting completed
messages/results. Existing JSONB columns need no migration. Reservations,
ownership, idempotency, versions, atomic completion, checkpoints, cache recovery
and purge/tombstones are preserved. See [conversations](conversations.md).

Instructions mentioning phones/email/family/departments do not automatically
request personnel reads. Personal requests retain the nine-field registry and
backend-authorized identity, without name/email lookup. Mixed answers preserve
policy evidence when HRM is unavailable, exposing no invented facts. The additive
`employee_evidence.status=unavailable` represents that mixed-source outcome;
pure personnel reads retain sanitized 503/504 errors. See [Type B](type-b-http.md).

## Authoring, evaluation and release

Author declared source JSON, then build/check generated artifacts. `impact`
reports dependent tasks and question IDs. `refresh` relinks only uniquely
identical evidence under its existing guards. Meaning/revision/provenance changes
require targeted review and tests. Disk edits do not update DB publication.

The question bank preserves the original 416 cases / 433 turns and adds direct
task wiring/aspect cases: currently 832 cases / 849 turns. Separate per-turn
oracles assert task/source choices, forbidden targets, requested employee fields,
nulls and failure fixtures. Original prose/support labels remain intact.
Expansion wiring is not an independent paraphrase holdout or business acceptance.

```powershell
python -m src.cli.build_artifacts check
python -m src.cli.check_answer_catalog
python -m src.cli.evaluate_questions --output .tmp/business-fixture-new.json
python -m src.cli.evaluate_questions --readonly --retrieval-limit 40 --output .tmp/business-readonly-new.json
python -m scripts.verify_runtime_assets
```

Default evaluation uses source-package evidence fixtures, fictional HRM values,
isolated HTTP owners and the real reducer. It starts no models, loads no private
env and writes no database. It does not prove DB publication or durable SQL
conversation behavior; disposable integration suites own storage boundaries.

Readonly mode loads local configuration/models and uses real Type A SELECT-only
paths, without personnel reads or durable turns. Personnel/failure-injection
cases and missing/changed eligible publications are explicitly deferred. A bounded
sample measures recall@30, evidence at top five and expected-first ordering. Its
tasks are selected round-robin across declared business families, rather than
taking the first software cases in file order. Its
baseline is the same supplemented pool before metadata sorting, not old-code or
vector-only recall. Reports contain IDs/hashes/metrics, not source/response bodies.
Input/output reports stay under `.tmp`; existing outputs are refused.

Exit 0 means no observed contract failures, not that deferred cases passed;
exit 1 records failures, exit 2 is invalid input/unavailable execution. Neither
mode issues independent quality/deployment acceptance. Release all three
artifact/manifest pairs with code in an isolated checkout. Verify target
publications and consumer handling of the unavailable enum before deployment.
This implementation does not publish application data, migrate or restart services.

Final verification on 2026-11-18 passed 1,010 non-integration tests and all 849
fixture turns. Readonly Type A passed 785 turns, deferred 64 isolated/personnel
cases and recorded no failures. Forty stratified retrieval tasks had 100% recall
at 30 and expected-first 24/40 before to 40/40 after ordering; metadata p95 was
10.81 ms. All 1,346 source references matched, and 22 runtime assets verified.
Disposable-DB integration and deployment remain unexecuted. See
[current verification](../README.md#current-verification-status) for command and limits.

A subsequent dependency-report follow-up passed 191 focused tests: targeted
source updates include related ambiguity/negative scenarios even when their
oracles do not name a positive task. The broader run above predates that small
offline-reporting change; serving artifacts and evaluated routing are unchanged.
