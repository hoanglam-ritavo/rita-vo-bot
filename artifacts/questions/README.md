# Rita VO Bot question sources

This directory contains authored Vietnamese questions and short behavioral
expectations, with separate per-turn oracles for the explicit offline runner.
It is not a runtime artifact, answer catalog, training set or quality receipt.
Serving does not consume these files. See
[execution and limits](../../docs/business-topics.md#authoring-evaluation-and-release).

The structured-conversation revision passes the request-local plan through the
fixture HTTP coordinator instead of concatenating follow-up text and routing it
again. Lookup task labels remain fixture recall annotations, not template-answer
authorization. The user-selected general-guidance-first policy reconciles only
`q.boundaries.ambiguity.002` and `q.policy.leave.012`: both now require a partial
answer with the general leave source and claims, retain privacy/negative-task
exclusions and are conditional on eligible source evidence. Other oracles remain
unchanged. This reconciliation is not live or independent business acceptance.

Start with [index.json](index.json) to find groups, counts and coverage links.
Each group file is a standalone JSON object. There is no combined string list,
generated runtime copy or version directory. Git should preserve future history.

## Organization

| Folder | Purpose |
| --- | --- |
| `policy/` | Leave rules, benefits, labor rules, workplace safety, administration, travel and source guidance |
| `software/` | Workit, E-Leave, Bravo/VPN, Outlook, E-Postal and E-Workdate procedures |
| `employee/` | The nine supported self work-profile fields and their data-availability boundaries |
| `mixed/` | Questions combining policy evidence and self work-profile facts |
| `conversation/` | Cross-topic context, candidate selection and context inheritance boundaries |
| `boundaries/` | Ambiguity, unsupported requests, privacy/injection and evidence availability |
| `oracles/` | Per-turn assertions and isolated fixture choices; not runtime data |

A question has one primary home. A leave policy belongs under `policy/leave`;
creating a leave request in Workit belongs under `software/workit`. Follow-ups
about one business topic stay with that topic. Conversation cases are for context
behavior itself, including changing topics. Tags provide secondary filtering.

## Record format

Every group contains `schema_version: 1`, `group_id`, an English `description`
and an ordered `cases` array. Every case contains these fields:

| Field | Meaning |
| --- | --- |
| `id` | Globally unique, permanent ID, e.g. `q.policy.leave.001` |
| `title` | Short Vietnamese scenario name |
| `tags` | Secondary facets; they do not select runtime behavior |
| `support` | Contract classification described below; never a pass/fail result |
| `references` | Related `task_ids`, `document_ids` and `employee_fields`; arrays can be empty |
| `preconditions` | Vietnamese setup conditions, not executable fixtures |
| `turns` | Ordered user messages, each with `text` and a short Vietnamese `expectation` |

Each case starts a new conversation with no inherited context unless its own
turns establish that context. Turns belong to the same fictional actor and room.
Assistant responses are intentionally absent. A follow-up assumes the preceding
turn met its expectation; this is an authored scenario, not recorded execution.
An expectation concerns meaning, sources and limitations, not exact prose.

An oracle may set `expected_aspects` to a nonempty list of `overview`, `amount`,
`conditions`, `paperwork` or `procedure`. The local question runner compares the
set against the planned aspects for that turn. Private review sets use the same
indexed layout under `.tmp` and run via `--questions-dir`. See the
[holdout procedure](../../docs/business-topics.md#template-coverage-and-holdout).

Support classifications:

- `implemented`: the current contract describes this clarification or protective
  behavior without requiring a successful policy/profile lookup. Individual
  phrasings are still unverified.
- `conditional`: answering or interpreting the scenario requires the stated
  configuration, evidence, context or a sufficiently clear task selection.
- `unsupported`: the requested operation/data/computation is outside current
  capability. The expected behavior describes its limitation, not future work.

References indicate related scope, not guaranteed routing, authorization, source
precedence, availability or completeness. For a negative case the referenced task
can be the task that must not be selected. For multi-turn cases references cover
the whole scenario. Document references are the union of a related card's source
documents; they are not per-turn citation assertions.

## Tags and authoring rules

Core tags are `direct`, `paraphrase`, `situational`, `no-diacritics`, `typo`,
`type-a`, `type-b`, `mixed`, `boundary`, `unsupported`, and `multi-turn`.
Aspect tags are `aspect:procedure`, `aspect:conditions`, `aspect:amount` and
`aspect:paperwork`; a direct task question covers its declared overview.
Other descriptive tags identify context, evidence, privacy or data conditions.
The tags used by a case are literal strings; no runtime matcher is implied.

- Keep IDs when editing or moving a case. Do not renumber existing cases.
  New IDs use the next unused number in their original primary group namespace.
  A moved case may therefore retain an ID whose prefix differs from its new home.
- Write realistic Vietnamese questions. Keep intentional missing accents,
  spelling errors and negations. Avoid creating volume through interchangeable
  polite prefixes alone.
- Keep one direct, two independently authored alternative/situational questions,
  one accent-free variant and one scope-boundary question per observed Type A
  task. Include a separate question for each declared non-overview aspect.
- Keep at least a direct question, a paraphrase and a null-data scenario for each
  supported employee field. Use fictional profile evidence only.
- Repeated wording is intentional only when preconditions, turn context or the
  target source distinction changes. The two same-label travel tasks retain
  distinct source IDs and must not establish automatic policy precedence.
- Do not assert that source inventory coverage means a complete answer. Preserve
  partial declarations, conditions, exceptions and unresolved applicability.
- Do not infer present employee facts from a document, user assertion, historical
  turn or previously cached answer. Never decide individual entitlement.
- Do not add private conversations, personnel values, source quotations,
  credentials, wire identities, transport assertions or expected response bodies.
  The example.invalid email and named employees in questions are fictional.
- Keep future retry, timeout, duplicate-message and infrastructure execution
  scenarios in the separate test-integration plan.

## Index and coverage maintenance

The index records the authoring date, Git revision and SHA-256 hashes of the
readable input files. The working tree already contained uncommitted changes;
the revision alone does not identify the source snapshot. Hashes are provenance
only: they are not runtime fingerprints, evidence authorization or release gates.

`files` uses paths relative to this directory. `baseline.inputs` and task
`source_file` paths are relative to the repository root. `coverage` maps tasks,
their declared aspects, document IDs, employee fields and retrieval route IDs to
case IDs. Update these mappings and file/total counts whenever cases change.
Do not regenerate or edit runtime catalogs to maintain this directory.

Before handing off changes, parse every JSON file, reject duplicate object keys
and IDs, check paths and nonempty turns/expectations, resolve all coverage links,
and recompute counts. Check that each observed task retains its required question
family and each aspect/employee field has coverage. Review duplicate full cases
and ensure every scenario has a distinct purpose. Read the final scope of changes.
These checks validate authoring structure. The separate
`python -m src.cli.evaluate_questions --output .tmp/<new-report>.json` runner
executes HTTP/reducer fixtures; add `--readonly` for real Type A checks. Neither
mode grants quality acceptance. The original snapshot below is historical.

## Current executable coverage

The current set contains **832 cases / 849 turns in 21 groups** and 21 oracle
files. Original 416 cases and their prose/IDs are preserved. Additional direct
wiring and aspect cases cover 279 tasks; source-label alternatives use explicit
source IDs where needed. These additions are wiring regressions, not independent
paraphrase coverage or a claim that all possible business questions are supported.
The original input snapshot remains historical; evaluator hashes identify actual
selected catalog and question/oracle bytes.

Oracles use case ID and zero-based turn, declaring allowed statuses,
required/forbidden tasks and sources, claims, requested fields/nulls, fictional
values and fixture conditions. Case reference arrays are not automatically
citation assertions. Source-failure simulation never applies to an application DB.
Reports stay in `.tmp`; fixtures do not measure vector recall or prove publication.

## Initial inventory and limits — 2026-11-18

The initial set contains **416 cases / 433 user turns in 20 groups**. References
cover **58 readable answer cards, 99 declared task/aspect pairs, 29 document
packages, nine employee fields and six retrieval route declarations**. Each
observed task has its five base questions and each non-overview aspect has a
dedicated question. This does not mean every chunk, alias or business rule is
covered.

Post-authoring data checks passed for all 21 JSON files: unique object keys and
case IDs, nonempty turns/expectations, group paths and counts, coverage references,
all five base-question variants per card, declared aspects, actual employee
registry fields, unchanged readable input hashes, duplicate complete scenarios,
README links and whitespace. These checks did not execute the bot.

At initial authoring, working-tree source indexes and the runtime retrieval
catalog were inaccessible due to filesystem permissions. Source assembly and
runtime agreement were not verified during that authoring step. Its coverage is
an inventory of readable individual cards/routes, including local untracked work;
the initial snapshot hashes and limits remain recorded in the index.

During initial question authoring, no API calls, model startup, database access,
application tests or quality evaluation were performed. The declared behavior of a task does not establish
recognition of every authored paraphrase, safety question or ambiguous request.
Review and execution belong to the next plan; this exposed authored set must not
be the sole independent quality acceptance set.

## Git policy

The repository allowlist permits this README, the index and declared direct JSON
children of the six question folders and the oracle folder. Files appear in ordinary `git status`
and can be added without force. Root-level reports, backup extensions, other file
types and nested or unknown folders remain ignored. An unlisted JSON file inside
an allowed group is still rejected by the checker once tracked; do not use that
filename allowance to add reports or private material.

The runtime inventory checker separately accepts only this README, the index and
group/oracle files declared by the index. It validates schema version 1, their lists,
unique safe paths and regular-file presence, including rejecting linked paths.
It does not inspect case content, coverage links or behavior. Do not add these
sources to the runtime manifest or artifact builder. See the owning
[Git policy](../../docs/runtime-artifacts.md#git-policy-for-questions-and-retrieval).

The later Git-policy change does not modify the initial authoring snapshot or
automatically stage, commit, publish or execute these cases.
