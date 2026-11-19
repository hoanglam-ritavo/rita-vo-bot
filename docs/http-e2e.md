# External HTTP E2E scenarios

## Status and boundaries

This constructed harness targets a manually started bot. Live execution belongs
to a separate execution plan. Implementation checks use fictional HTTP transports
and database connections only: no private environment, live HTTP/PostgreSQL,
model startup or actual E2E run.

The scope is stateless `/api/v1/retrieve` and `/api/v1/answer` over socket HTTP.
The harness never starts/stops the bot, edits `.env`, seeds/resets databases,
publishes documents, retries requests, follows redirects or sends conversation
identities. Backend/portal authorization, remote HTTPS, durable conversations,
load and failure-injection testing are outside this scope. Exposed implementation
regressions are not independent business acceptance.

The domain package is `src/evaluation/http_e2e/`; the entrypoint is
`src/cli/evaluate_http_e2e.py`. The harness reads the current question bank and
uses endpoint-specific assertions. This client is separate from the
[existing evaluators](business-topics.md#authoring-evaluation-and-release).

## Scenario matrix

Each bank case appears exactly once per endpoint. Current-source one-turn cases
are candidates. Multi-turn cases remain
`not_applicable / conversation_required`, without splitting turns or using a
client reducer. Cases requiring source/feature changes or injected failures
remain `not_applicable / controlled_fixture_required`. Missing profiles or
publications block an applicable case; they never count as passed or excluded.

Each endpoint adds eleven protocol scenarios: missing/wrong key;
missing/null/string/boolean/zero HRM reference; blank text; and zero/large/boolean
top-K. Five additional HRM-role scenarios per endpoint cover complete work fields,
inactive profiles, duplicate user references, resolved managers and a reference
confirmed absent in the snapshot. Missing suitable roles are blocked.

| Endpoint | Expectations |
| --- | --- |
| Both | Expected HTTP status, no-store, strict schema 2, exact employee fields/subjects/values/nulls, declared application states, required/forbidden sources and input provenance |
| Retrieve | Endpoint-specific status and lookup expectations, top-K 5, unassessed matches, completeness warning, snapshot provenance and first linked task/aspect evidence for authored positive targets |
| Answer | Claim-to-display consistency, declared exact policy claims and limitations, task/aspect evidence, citation quotes/metadata, graph integrity and forbidden-task evidence |
| Calculation | Independent work-date arithmetic, expected value and unit, HRM date and `as_of` inputs, and a claim linked to the calculation. Retrieve rejects calculation evidence |

The socket harness uses authored aspects when selecting task evidence and checks
exact policy claim text and required limitations where the oracle declares them.
It checks the displayed answer against the response's claims or limitations.
For explicitly authored lookup cases, it checks the `document_lookup` fallback,
unassessed limitation, source metadata and full excerpt hash against the frozen
publication. Template claims use authored references. Scores and timings are
outside the scoring rules.

Reviewed Retrieve cases can require a specific outcome and forbid document
lookup. Platform contrast cases verify the expected first candidate while
allowing unassessed secondary candidates. Inventory reports the number of
applicable Retrieve checks still using the broad default `unresolved` rule.

The 2026-11-19 local inventory covers 867 question cases and 1,766 checks. It
lists 1,692 pending preparation and 74 outside the stateless run (50 conversation,
24 controlled fixture). The 25 multi-turn cases are marked
`conversation_required` for each endpoint. Their per-turn conversation checks
run in the offline [question fixture](../artifacts/questions/README.md#record-format).
Of the applicable Retrieve checks, 65 retain the broad default. Counts are
recomputed from the selected inputs and describe planning coverage.

## Configuration

`inventory` does not read dotenv or process connection/key values. `prepare` and
`run` read only the following keys: process values override an optional explicit
`--env-file`, without interpolation or mutation of `os.environ`.

| Key | Purpose |
| --- | --- |
| `CHAT_BOT_SERVICE_KEY` | Credential matching the manual bot; never a CLI argument/report field |
| `TEST_HRM_DATABASE_URL` | Preprovisioned isolated PostgreSQL HRM test source with the existing schema/readonly role checks |
| `TEST_HRM_DATABASE_SCHEMA` | Validated schema identifier, default `public` |
| `TEST_HRM_DISPOSABLE_CONFIRMED` | Must equal `true` |
| `DATABASE_URL` | Corpus connection for SELECT-only publication preconditions; different database from the HRM test source |

Database URLs require `postgresql+psycopg`; startup-option overrides are rejected.
The harness does not read `HRM_DATABASE_URL` or call the application dotenv loader.
Separation checks compare host/port/database, not username/schema. The existing
isolated HRM integration-suite guard is unchanged.

The [configuration example](examples/http-e2e.env.example) contains placeholders
only. The [fictional binding illustration](examples/http-e2e-binding.example.json)
shows the private output shape; it deliberately does not cover the bank and is
not executable. Do not hand-author bindings or copy real personnel into docs.

For a later run, configure the bot separately: its `HRM_DATABASE_URL`/schema must
point to the test source, Type B and Answer must be enabled, and its model,
artifacts and corpus must match the authored local set. Existing `serve_api`
`.env` precedence still applies. The harness never changes the running process
or its configuration. Use a dedicated bot test configuration/checkout.

The default URL is `http://127.0.0.1:8000`; only literal loopback IPs over HTTP
are accepted. Credentials, paths, query fields, fragments and redirects are
rejected. `localhost` is deliberately not resolved. Remote HTTPS is a separate
scope; no TLS-verification bypass is introduced.

## Future execution sequence

These commands describe the later execution plan; they were not run against
live services during construction. Use the repository virtual environment and
new output paths, replacing placeholders without committing private values.

```powershell
# Local matrix only; no dotenv, network, DB or model access.
python -m src.cli.evaluate_http_e2e inventory --output .tmp/e2e-inventory-new.json

# Later: read prepared databases; do not call the bot or change data.
python -m src.cli.evaluate_http_e2e prepare --env-file <test-config-file> --output .tmp/e2e-binding-new.json

# Review/freeze that binding and retain prepare's canonical binding_sha256.
# Start the correctly configured bot manually through its existing serve_api command.
python -m src.cli.evaluate_http_e2e run --env-file <test-config-file> --binding .tmp/e2e-binding-new.json --binding-sha256 <reviewed-canonical-sha256> --output .tmp/e2e-report-new.json
```

`prepare` uses the existing employee-store metadata/privilege check, then surveys
only work/rank fields plus fields required by eligible oracles. It fails above
10,000 rows instead of silently sampling. No full personnel projection, seed or
write probes are used. Source reads own readonly REPEATABLE READ transactions,
timeouts and explicit connection disposal.

Representative selection is independent test-oracle logic over raw SQL rows:
newest timezone-aware timestamp, contract completeness score, employee ID.
Conflicting ties supply no arbitrary winner. It does not call production
selection/calculation functions. Manager selection is ID-first, exact-code
fallback only without an ID; self/conflicting links supply no manager facts.
One selected profile must satisfy every requested null/nonnull condition of a
case. Identities and values are chosen before HTTP, not from bot responses.

The corpus snapshot reads eligible current/public/active publications compatible
with the inventory's E5 pin. Exact chunk hashes, authored quotes and metadata
establish task/aspect prerequisites independently of HTTP output. Missing source
groups/references block relevant cases. Complete template answers require the
whole relevant section. A partial answer with exact claim expectations requires
all references for those claims. Other claim-bearing partial cases require one
fully supported claim and the declared source groups. Retrieve prerequisites
follow the required source groups and expected task evidence. Snapshot preparation
uses hashes and metadata without generating embeddings or loading models.

The private binding contains minimal HRM expectations/references, role
availability, corpus identities/hashes/metadata, input hashes and a snapshot
fingerprint. Review it privately. `--binding-sha256` freezes canonical JSON
content. Run rejects different local inputs, bindings, URL, business day or
source snapshots. Expected values are never refreshed during execution.

`run` probes `/health/live`, `/health/ready`, `/health/answer` in order, using
20-second timeouts and service authentication for protected probes. Failures
block business calls. Health does not prove HRM readiness or corpus completeness;
the independent snapshots and bindings are also checked before requests.

Business requests are sequential, top-K 5, with bound positive `hrm_user_id` and
150-second timeouts. No automatic retry. Transport failure or HTTP 429/503/504
stops subsequent requests to avoid an undrained worker. A final source recheck
turns provisional passes into `blocked` on drift or any snapshot read error.
Observed assertion failures remain in the report, and the command returns 2.

The operator owns the bot's actual source/configuration. Health does not attest
its dataset/version; matching values cannot prove identical database targets.
This limit does not authorize exposing configuration/secrets through health.

## Reports and construction checks

Outputs are exclusive-create files below `.tmp/`; existing linked/reparse paths
are rejected. Bindings contain personnel expectations and must stay private.
Reports contain case IDs, groups, endpoints, hashes, fixed reasons/assertion names
and counts, never credentials, DB URLs, personnel values, question text,
source bodies or response bodies. Exceptions are not printed. Preparation prints
only counts and its canonical binding hash.

Every case/endpoint appears once as `passed`, `failed`, `blocked` or
`not_applicable`, grouped by endpoint and business group. Exit 0 requires every
applicable check passing and none blocked; exit 1 means assertions failed;
exit 2 means invalid/unavailable/incomplete execution, including missing
roles/publications. Prepare may write a partial binding and exit 2 to expose
missing prerequisites for review.

Local construction regression:

```powershell
python -m pytest src/tests/test_http_e2e.py src/tests/test_answer_evaluation.py -q
```

Tests use fictional configuration, `httpx.MockTransport` and fake SQL connections.
They check import/config isolation, source preparation, endpoint assertions,
protocol errors, health, drift, reports and CLI boundaries. They do not execute
live E2E or establish DB compatibility or business correctness. See
[current verification](../README.md#current-verification-status).
