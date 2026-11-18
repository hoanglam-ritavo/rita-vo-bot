# Contextual document answers

HTTP uses one pipeline: Type A provides policy evidence; Type B provides current
employee facts through `src/type_b/employee_store`. Facts are never document chunks
or policy quotations. No generative model or external model service is added.

## Identity and failure behavior

Every `/api/v1/answer` and `/api/v1/retrieve` payload requires `hrm_user_id`, a strict
positive safe integer matching HRM `user_id` (Backend `odooId`), not `employee_id`.
Omitted/null/string/boolean IDs fail with 422 before reads. All business routes
require `X-Rita-Service-Key`. Backend derives the reference from the current active
employee account; browsers cannot select it. Missing linkage returns Backend
`422 CHAT_BOT_EMPLOYEE_LINK_REQUIRED` before contacting the bot.

`TYPE_B_ENABLED=false` remains the default. Configure the existing readonly source
as described in [employee source](employee-source.md). Missing, disabled or failed
HRM reads yield explicit application states while eligible document evidence
remains usable. Null facts never establish ineligibility. Failed startup HRM
preflight requires a restart after provisioning. No environment file is changed.

```json
{"text":"What is my current job title?","top_k":5,"hrm_user_id":123}
```

Stateless text remains 1-8,000 characters, conversation text 1-2,000; top-K is 1-20.
Responses are no-store. The single admission slot, 120s deadline and worker draining
cover both sources; cancellation does not release an undrained worker.

## Task requirements

Catalog schema 3 declares `context` for every task under
`artifacts/answers/source/cards/`. Build/check regenerates the runtime catalog.
`scripts/author_context_cards.py` is an explicit offline authoring utility; it is
not runtime routing or business approval. Review its generated source inputs.

| Mode | Supported behavior |
| --- | --- |
| none | General instruction; no employee read. |
| fill | Fill declared placeholders with current work facts. |
| branch | Show relevant facts and ask for missing operational/event inputs; conditional guidance. |
| calculation | Explicit declared work-date operation; incomplete inputs remain partial. |

Groups permit fields; `facts`, template placeholders and `date_field` select the
actual projection. Subject and one-hop manager reads share a readonly snapshot.
Manager resolution uses employee ID first, exact code fallback only without ID.
The linked manager's work email has its own record timestamp. A relationship does
not prove approval authority. Named coworkers never become the current employee.
An explicit general-only request disables personalization.

Leave/attendance uses work identity and manager while requesting dates, leave type
and schedule. Contract/resignation uses employment milestones but requests actual
historical contract/notice details. Benefits requires event dates/proof; relevant
family tasks can show bounded family data, without assuming event or eligibility.
Insurance fields do not prove contribution history. Travel/uniform/referral needs
job mapping, trip, issuance or recruitment facts missing from this table. Site and
authority tasks require location/assignment rather than inferring rights from a
job title. General software instructions retain an explicit none disposition.

Exact calendar-day/completed-month arithmetic requires an explicit work-date
milestone and examination date (or today in Asia/Ho_Chi_Minh). It is labelled as an
interval, never leave balance or policy tenure. The pure calculation module also
validates age and Decimal arithmetic; no monetary entitlement or policy threshold
is inferred from prose. Sensitive identity/bank groups require a relevant authored
task and are not injected into ordinary answers. Family presentation is bounded to
three records with an explicit limitation when more exist.

## Version 2 evidence contract

Only schema 2 is served by HTTP. Old source_type/employee_evidence/employee_claims
responses are retired, without a compatibility parser.

Answers contain schema_version=2, status, answer, claims, sources, evidence,
application, limitations, fallback_reason, relevance and timings. Claims have
id/text/evidence_refs, combining any supported evidence kinds in one answer:

| Kind | Fields/provenance |
| --- | --- |
| document | id, source_id, checked quote; source preserves origins, version, dates and applicability. |
| employee | id, allowed field, subject, label, nullable value, observed_at, nullable updated_at. No internal employee/account IDs. |
| user_input | id, slot, label, value, hypothetical; explicitly unverified. |
| calculation | id, operation, label, value, unit, input_refs, rule_refs, nullable as_of. Rule references must identify document nodes. |

Duplicate IDs, dangling references, cycles and private/unregistered employee fields
are rejected. Bounds are 12 claims, 128 evidence nodes, 12 references per claim or
calculation input list. Bounded output reports partial coverage and prunes orphaned
employee values. Backend and portal independently validate and allowlist output.
Backend maps the root to schemaVersion and source metadata to camel case; evidence
node property names remain unchanged.

Application mode is general/personalized/conditional/hypothetical. State is
not_needed/applied/partial/unavailable/disabled/not_found/conflict. It includes
bounded missing_inputs and conflicts. User assertions do not overwrite HRM facts.

Retrieve returns the same evidence/application envelope with matches,
document_status, fallback_reason, relevance and timings. Overall statuses are
evidence_found/partial_evidence/no_evidence/needs_clarification/unsupported.
document_status remains matches_found/no_matches/no_eligible_documents, or null
without document lookup. Retrieve never computes or generates an answer. Ranking
remains unassessed, independent of employee lookup.

## Conversations and rollout

Reducer schema 4/cache namespace v2 stores bounded user-input slots, task identity
and document references. Each turn re-reads personnel facts. History and assertions
are not authoritative evidence. See [conversations](conversations.md).

For the original schema-1 to schema-2 contract replacement, deploy compatible bot
and Backend before portal; incompatible old development results require the
explicit stopped-service reset below. No production erasure or startup reset is
authorized. The subsequent request-planning update retains schema 2: deploy code
and its generated catalogs together after draining the previous worker, without
resetting schema-2 history or running migrations. Derived reducer context is
invalidated by its identity; stored answers and message fingerprints are retained.
Rollback must use a compatible code/catalog set.

The shared request analysis keeps profile subjects local to their clauses and
does not discard a recognized policy from a mixed question. Unresolved requests
ask for clarification before HRM reads. Explicit document lookup does not require
a unique task and never authorizes a personnel read. Neutral childbirth-policy
wording asks which declared policy is intended; numbered/exact-label choices keep
the original requested aspects. No candidate score certifies an answer.

HRM availability can recover through a serialized check on a later personnel
request (15-second deadline, at least 30 seconds between checks). Disabled HRM
is not checked. No automatic answer replay occurs. Conversation-wide deadlines
and transaction-local lock/statement limits are described in the
[architecture](../ARCHITECTURE.md#request-planning-and-completeness).

```powershell
# Explicit local development targets; never copy private URLs into docs/logs.
$env:CHAT_RESET_BOT_DATABASE_URL = 'postgresql://<user>:<password>@127.0.0.1/bot_dev'
$env:CHAT_RESET_BACKEND_DATABASE_URL = 'postgresql://<user>:<password>@127.0.0.1/backend_dev'
python -m src.cli.reset_dev_chat
# After inspecting counts and stopping both services:
python -m src.cli.reset_dev_chat --apply --services-stopped --confirm RESET_DEVELOPMENT_CHAT
```

Dry-run is the default; application .env files are never loaded. Both schemas
preflight before deletion. Separate per-database transactions emit commit markers;
inspect them after failures and rerun if needed. Only bot
chat_messages/chat_turns/chat_conversations and Backend chat_purge_jobs/chat_rooms
are deleted, in dependency order, without CASCADE/DDL. Targets must be loopback
PostgreSQL databases with a dev/development/test name segment. Output contains
counts/commit markers, never credentials or row content. Corpus, HRM and accounts
are outside the command's table allowlist.

Fictional tests do not establish live schema/permissions, source correctness,
provisioning, real-model quality or personal policy eligibility.
