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

Conversation context stores one active request, its task candidates and aspects,
and bounded slot values with their origin. The routing registry recognizes
platform, type and duration in explicit follow-ups. See the [conversation rules](conversations.md#context-and-evidence)
for corrections, ambiguities, date input and when task changes discard context.

Identity includes reducer version, answer/lexical catalogs and retrieval
fingerprint. A reply that depends on incompatible context requires a complete
restatement. Completed messages and results remain available. Existing JSONB
columns need no migration. Reservations,
ownership, idempotency, versions, atomic completion, checkpoints, cache recovery
and purge/tombstones are preserved. See [conversations](conversations.md).

Personnel reads require declared contextual task requirements or explicit supported
self-fact requests and Backend-authorized identity, without name/email lookup.
Unified answers preserve policy evidence when HRM is unavailable and expose no
invented facts. `application.state=unavailable` reports source failure; disabled
and missing profiles remain distinct states even for a pure fact lookup. See
[contextual answers](contextual-answers.md). Question oracles retain document,
forbidden-source and numeric assertions while declaring the newly allowed task
context fields; fixture passes are contract checks, not independent business approval.

## Authoring, evaluation and release

Author declared source JSON, then build/check generated artifacts. `impact`
reports dependent tasks and question IDs. `refresh` relinks only uniquely
identical evidence under its existing guards. Meaning/revision/provenance changes
require targeted review and tests. Disk edits do not update DB publication.

The question bank preserves the original 416 cases / 433 turns and adds direct
task wiring/aspect cases (832 cases / 849 turns before the coverage pilot).
The coverage pilot brings the public bank to 839 cases / 857 turns. Separate per-turn
oracles assert task/source choices, forbidden targets, requested employee fields,
nulls and failure fixtures. Original prose/support labels remain intact.
Expansion wiring is not an independent paraphrase holdout or business acceptance.

The template coverage pilot adds a `paperwork` section to the annual and unpaid
Workit tasks and the annual and session-based E-Leave tasks. It distinguishes
form fields from supporting documents. The cited instructions do not establish
which documents are mandatory for every situation, so these sections are partial.
The E-Leave unpaid procedure still lacks a corresponding template. See the
[coverage and holdout workflow](#template-coverage-and-holdout).

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

## Template coverage and holdout

Run `python -m scripts.inventory_template_coverage --output .tmp/<new-report>.json`
to map the five aspects for every task. The inventory records declarations, task
and route IDs, platforms, context modes, source addresses, chunk dispositions and
input hashes. An undeclared aspect may be inapplicable. The inventory cannot
verify the meaning of claims, natural language coverage or DB availability.

Classify each actionable gap as language recognition, missing or partial answer
content, insufficient source, or target publication unavailable. Compare adjacent
tasks before adding an alias. Reuse the current task ID for equivalent wording
and add a section only when current source packages support it. Keep missing and
unpublished sources in the backlog for separate source or publication work.
Business completeness requires comparison with the source, including subjects,
conditions and exceptions. Readiness and publication are separate checks.

An independent reviewer prepares and freezes an indexed question directory under
`.tmp`, using the same groups and per-turn oracle format as the public bank. The
reviewer retains the questions and compares the same set on the pinned baseline
and candidate. The implementer receives aggregate results. If a case is disclosed
for debugging, replace it for the next holdout and keep the disclosed variant as
a regression when suitable. Use fresh report paths for both runs.

```powershell
python -m src.cli.evaluate_questions --questions-dir .tmp/<private-set> --output .tmp/<fixture-report>.json
python -m src.cli.evaluate_questions --questions-dir .tmp/<private-set> --readonly --output .tmp/<readonly-report>.json
```

Use the second command with an authorized SELECT-only Type A test connection.
The optional `expected_aspects` oracle compares the requested aspects in the
internal plan. The socket HTTP API has no aspect field. Reports include task IDs,
aspects, cited document IDs, employee field names and per-group counts. They omit
question text, response bodies and field values. Pass, fail and deferred counts
remain separate. New phases require public regressions, at least 90% holdout
success for supported cases in each group, improvement unless the baseline was
already perfect, and no wrong subject, platform, source, HRM field or completeness
claims. All eligible Type A checks must pass before a target release. Report an
offline result separately if target publication checks are still deferred.

## Historical verification

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
