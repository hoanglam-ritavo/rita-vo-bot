# Leave recognition and source-bound ordering

## Scope and API behavior

The leave-query policy improves candidate ordering and registration clarification
without an intent classifier, relevance profile, migration, re-embedding or
publication. It covers leave requests and contrasts them with resignation and
late arrival/early departure. The mechanism now also supports
[cross-domain tasks](business-topics.md); this page retains leave-specific rules
and historical regression. It does not decide entitlement, balances or approval.

For `làm sao để đăng ký nghỉ 2 hôm.`, retrieval recognizes a two-day leave request
without selecting a leave type or platform. Eligible general procedure evidence
comes before platform instructions and session-specific examples. Answering asks:

> Bạn muốn đăng ký nghỉ phép hay nghỉ không lương trong 2 ngày? Bạn đang dùng Workit hay E-Leave để gửi đơn?

An unambiguous supported request reads exact current DB evidence through the
existing template path. Workit annual/unpaid registration, E-Leave registration
and its documented session cases, and general annual/unpaid policy each have a
source-bound task. They remain partial; source inventory is not completeness.
E-Leave's source does not list all leave types, so an E-Leave unpaid request does
not invent a menu option or silently substitute Workit. Existing narrow tasks,
such as E-Leave mandatory fields, keep their explicit supported behavior.

HTTP/CLI shapes and `top_k` are unchanged. Unknown type/platform prompts return
`needs_clarification` / `leave_needs_clarification`; recognized unsupported branches
return `insufficient_evidence` / `leave_task_unsupported`. Both use existing wire
fields. Relevance remains unassessed. Specifying `hrm_user_id` never establishes a
balance or an entitlement; existing service authentication/authorization still applies.
No conversation state is written by this policy.

## Recognition and ranking

`src/retrieval/policy.py` owns typed rules, bounded Vietnamese/accentless matching,
day/session extraction and deterministic stable ordering. Query analysis retains
the original text. Numeric dates do not become leave durations; day counts and
sessions are separate; no workday/calendar conversion or split-session sum occurs.
Ambiguous ranges, multiple families/platforms/types, unsupported negation and
unclear `nghỉ việc` do not silently select a branch. Notice periods are distinct
from requested duration. These are bounded rules, not general language understanding.

Language aliases belong to lexicon concepts. The optional boolean
`recognition_only` is validated in runtime schema 2: these concepts participate in
recognition but never create semantic query rewrites or suppress existing rewrite
rules. This keeps broad terms such as `nghỉ` from rewriting unrelated questions.

After the existing reranker, before top-K, stable buckets prioritize:

1. Valid bindings in the recognized leave family, then unannotated/stale neutral
   candidates, then explicitly different families.
2. Compatible subtype and operation, then an explicitly requested system.
3. General procedures for unspecified-platform requests; system procedures for
   explicit-platform requests; documented session cases for session questions.
4. Matching authored day ranges; original reranker/vector order breaks ties.

No document ID receives a hardcoded score and no raw logit is changed. Resolved
tasks may supplement the pool with bounded exact references under the cross-domain
snapshot/eligibility contract. Unresolved leave requests only reorder their
original pool. Relevance and eligibility remain distinct.
Server order may therefore differ from descending raw `rerank_score`. Callers must
preserve response order. Inference errors remain errors, not a silent model fallback.

## Artifacts and updates

`artifacts/retrieval/catalog.json` is an optional selected schema-2 catalog with a lexicon
SHA-256, authoring snapshot fingerprint, rule collections, source bindings and task
routes. Its declared inputs live in `artifacts/retrieval/source/`; the existing
builder now assembles two or three groups rather than assuming exactly two.
Binding metadata includes family, role, operation, platform, optional subtype/day
ranges and the existing exact reference contract. IDs identify records; they are
not ranking weights. All source/range labels require source review.

At startup the policy validates structure, lexical pairing, binding identities and
routes. Missing/malformed/mismatched policy artifacts log only a fixed unavailable
code and leave baseline retrieval/explicit template behavior available. No env or
request switch is added. At sort time a binding must match current DB
document/revision/index, source/content hashes, exact quote and bound metadata.
Stale bindings become neutral. Internal source hashes are removed before HTTP/CLI
serialization; models, source text and raw scores remain unchanged.

Use the existing `impact -> refresh -> build -> check` commands. Impact reports
answer and ranking references together. Refresh mechanically relinks only unique,
unchanged references and updates coverage/snapshot metadata; content/context/revision
changes remain review-required. Build validates answer coverage after refresh;
runtime still validates coverage strictly. For private import, add optional
`--retrieval <path>` to import all three groups together. Imports never promote files.

Regenerate from source, update the artifact manifests/inventory and deploy code plus
all selected artifacts together in an isolated checkout. A Git rollback does not
roll back DB publication. This change does not publish, migrate or restart anything.
Conversation schema-2 slot handling shares the policy; storage/cache ownership
remains governed by the conversation contract.

## Verification

`python -m pytest src/tests/test_leave_policy.py -q` checks recognition, near misses,
negation, stale source neutrality, stable ordering, serialized HTTP/CLI top-K,
clarification, exact template evidence and three-group authoring/import/refresh.
Run broader non-integration regression, build/check, catalog validation and runtime
inventory verification before release. Integration requires a dedicated disposable DB.

The readonly utility accepts private cases and a new private report path:

```powershell
python -m scripts.benchmark_leave_retrieval --cases .tmp/leave-check/cases.json --output .tmp/leave-check/report.json
```

Cases are a list of unique `id`, `text`, expected evidence `expected_role`, and
optional `expected_system` (`workit`, `eleave`, null). Up to 100 questions, each
1–8000 characters, are allowed. The utility reads local configuration/models and
published Type A candidates only, never employee data, migrations or publication.
It records before/after IDs, validated binding roles, corpus/policy identities and
timings, not questions/excerpts in the report. Input/output stay under `.tmp`, and
existing outputs are refused. Exit 0 means recorded, not acceptance; exit 2 means
invalid input or unavailable dependencies. Warm sort overhead is measured separately
with 100 iterations after 10 warmups; the target is p95 below 50 ms for 30 candidates.

The 2026-11-18 exposed seven-case regression improved expected-first ordering from
4/7 to 7/7, with warm ordering p95 about 1.31 ms. The reported question moved its
general leave procedure from second to first. Cases/reports stay private under
`.tmp/leave-retrieval-20261118/`. These source-checked examples are exposed
regressions, not independent human quality acceptance or a deployment SLA.
