# Readonly Type B HTTP integration

The HTTP service can provide bounded work-profile facts alongside Type A policy
evidence. It uses deterministic tasks/templates, not a generator, arbitrary SQL,
employee search, aggregates or inferred benefit eligibility. The underlying
[employee source](employee-source.md) and every relation on its connection remain
readonly. No cache, embedding publication, history, schema/data migration or
background synchronization is introduced.

## Trusted caller and configuration

TYPE_B_ENABLED=false is the default. HTTP always requires CHAT_BOT_SERVICE_KEY;
TYPE_B_SERVICE_KEY is retired. Enable the source with a separately provisioned SELECT-only HRM_DATABASE_URL,
optional HRM_DATABASE_SCHEMA (default public), and API restart. HTTP retains its
existing .env-before-process precedence and loopback-only bind.

The internal backend must authenticate the end user, check current access, derive
hrm_user_id from that user's local odooId, and send
`X-Rita-Service-Key: <SERVICE_KEY>`. The key authenticates the calling service, not
the end user's right to read arbitrary personnel. Never expose it to browsers or
accept a browser-selected ID without backend authorization. Backend/portal changes,
public end-user authentication and direct remote Python binds remain outside the
source adapter. See [shared HTTP authentication and HTTPS](http-service-auth.md).

Both POST endpoints accept:

```json
{"text":"Ngày chính thức của tôi là ngày nào?","top_k":5,"hrm_user_id":123}
```

hrm_user_id is employee_sso_store.user_id (backend odooId), not employee_id,
account_id or an internal UUID. It is optional/null without employee context;
supplied values must be integers 1..2**53-1, not bool, string or float. employee_id
is not an accepted alias. top_k limits documents only. Names/emails do not resolve
identity. All HTTP business requests require the shared header, including A without an ID.
B/mixed without an ID requires authentication, then returns 422
hrm_user_id_required. Missing/wrong key returns 401 service_authentication_required
without an employee query. Disabled Type B plus an ID returns 503 type_b_disabled.
Disabled Type B without an ID uses legacy execution. A/null-ID requests preserve
the prior caller behavior if no personal task is identified. Validation never
echoes private body/header values. Context responses and errors use no-store.

## Version 1 routing and tasks

| Task | Source field |
| --- | --- |
| Họ tên | employee_name |
| Mã nhân viên | employee_code |
| Chức danh | job_title |
| Phòng ban | department_name |
| Quản lý trực tiếp | manager_name |
| Ngày bắt đầu thử việc | probation_date |
| Ngày kết thúc thử việc | probation_end_date |
| Ngày chính thức | official_date |
| Ngày nghỉ việc | resignation_date |

The versioned matcher in src/type_b/tasks.py is separate from generated policy
catalog/lexicon artifacts. It requires recognizable self-reference and explicit
supported fields, supports bounded Vietnamese/accentless aliases and multi-field
requests, and does not claim arbitrary language understanding. Ambiguous "ngày
vào làm", uncertain subjects, other employees and unsupported personal requests
are clarified/rejected before employee reads. Policies are not personal tasks
merely because they contain "tôi". Salary, balances, tenure, bank, identity,
health, family, education, complete profiles and cross-employee lookups are outside
v1. Unsupported portions must not be silently treated as completed requests.

The coordinator uses the explicit deterministic work-task registry without a learned source-hint observer. No intent artifact or classifier mode is read. Explicit self-reference, supported fields and service authorization remain required. Catalog policy answers read linked current evidence independently of retrieval top-K; unavailable policy templates leave supported Type B facts available. See [catalog answering](catalog-answering.md).

Candidate-retrieval preparation encodes the original query once under the retrieval worker/lock; answer tasks use linked evidence without this embedding.
A/mixed retrieval reuses it and preserves semantic expansion, reranking, corpus
identity and eligibility; candidates remain unassessed. Prepared objects are request-local
and invalid after close/restart. B-only skips document search/reranker scoring and
policy rendering. Existing CLI/direct retrieve remains Type A-only.

The adapter reuses the full validated 64-column employee source and representative
selection, then exposes only requested allowlisted fields. It never attaches full
profiles to document matches, context, logs or responses. No active/eligibility
decision is inferred. Department paths remain complete; manager_name is display
data, not a manager identity. Facts retain ISO dates; answer dates use day/month/year.

## Response variants

Unchanged A responses keep their existing fields. Context responses use separate
strict OpenAPI variants with source_type type_b/mixed and employee_evidence:

```json
{
  "status":"found",
  "facts":[{"ref_id":"B1","field":"official_date","label":"Ngày chính thức","value":"2020-02-02"}],
  "source":{"relation":"employee_sso_store","observed_at":"2026-11-17T03:00:00+00:00","updated_at":null},
  "reason":null
}
```

This is fictional. observed_at is completion of the source read, not an upstream
freshness guarantee; updated_at retains its source value/null. No person/account
IDs or extra fields enter the response origin. Evidence status is found,
not_found, needs_clarification or unsupported; null values remain null.

### Retrieve

Context fields: source_type, status, matches, employee_evidence,
document_fallback_reason, relevance, timings. Status is evidence_found,
partial_evidence, no_evidence, needs_clarification or unsupported. All requested
values null yields no_evidence, some null yields partial_evidence. Missing records
remain distinct from records containing nulls. matches contains documents only;
B-only matches is empty, document_fallback_reason null, relevance no_candidates
with document_retrieval_not_requested. Mixed document relevance/fallback preserves
its original semantics; it never certifies personnel correctness or completeness.
Retrieve does not generate a human answer.

### Answer

ANSWER_ENABLED remains required; B-only rendering needs no policy catalog or
SEMANTIC_MODE=expand. A answering retains its catalog gates. Context answers add
source_type, employee_evidence and employee_claims to existing fields. Each employee
claim has text and fact_refs to B1/B2/etc; document claims/sources keep their
source_id/quote contract. No fictional document citation is created for database
facts. Null facts explicitly say "Hồ sơ hiện chưa có thông tin...". All known
values may yield answered; partial/null-only/missing outcomes remain partial or
insufficient_evidence. Ambiguity and unsupported preserve those answer statuses.

Mixed reads HRM once, then documents once; these are not an atomic cross-database
snapshot. Answers separate "Thông tin hồ sơ" and "Thông tin chính sách" and state
that personal eligibility is not evaluated. Mixed never returns answered for a
combined eligibility decision; it is partial when supported content exists. If
the policy catalog is unavailable, facts remain visible with a policy limitation;
retrieve can still return document excerpts.

## Lifecycle, failure and rollout

Startup runs metadata/readonly-role preflight with a 15-second bound. HRM failure
leaves A available; B reads return sanitized 503 employee_source_unavailable.
Missing records are evidence outcomes, not source errors. Source timeout is 504
employee_source_timeout; conflicts, invalid data, access/schema failures are 503.
No automatic retry or silent fallback to policy answers occurs. Restart after
fixing a failed startup preflight. /health/ready retains Type A dependency checks;
/health/answer reports document-template readiness, not B-only availability/quality.

Both endpoints share one busy slot (429), a 120-second deadline and disconnect
cancellation/draining. Source statement timeout stays 10 seconds. Shutdown waits
for owned work and closes HRM even after failed preflight, then retrieval/Type A
storage. Logs contain technical request IDs/status/timings, never questions, keys,
employee IDs or values. Personnel and error responses set Cache-Control: no-store.

Run `python -m pytest src/tests/test_type_b.py src/tests/test_retrieval_api.py src/tests/test_answering.py src/tests/test_employee_source.py -q`,
then `python -m pytest -m 'not integration'`. These synthetic tests do not establish
live identity permissions, classifier quality or schema/data compatibility.
PostgreSQL integration uses only the separately preprovisioned disposable fixture
described in the employee source runbook, never application HRM.

Leave Type B off until readonly preflight and the trusted backend contract are
verified internally. Backend must send the authorized reference/header before
user rollout; this task does not change backend or portal. Roll back by stopping
contextual calls at the backend and disabling Type B. No .env changes, service
restart, artifact rebuild/promotion, or live database operation was performed as
part of implementing this source change.
