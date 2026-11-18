# Readonly employee source

`src.type_b.employee_store` provides explicitly invoked PostgreSQL access to
`employee_sso_store`. Its connection remains independent of the Type A database,
Alembic and ingestion. The opt-in [Type B HTTP adapter](type-b-http.md) uses it
for bounded work-profile evidence in retrieve/answer. The source module performs no
authentication, authorization, user synchronization, HRM account lookup, alias
resolution for authentication, birthday business logic or model inference. Exact manager-code
resolution is internal to the one-hop manager reader; it is not a public employee search API. Callers must establish
identity and access rights before calling this internal service, including manager lookups. Access to all
64 properties through this internal module is not permission to expose them.

## Configuration and operation

- `HRM_DATABASE_URL`: required only when the source is explicitly used;
  `postgresql+psycopg://readonly_user:replace_me@localhost:5432/hrm`.
- `HRM_DATABASE_SCHEMA`: optional simple PostgreSQL identifier, default `public`.
  The relation name is fixed. URL-encode credentials as for the Type A database.
- No fallback to `DATABASE_URL`, `MIGRATION_DATABASE_URL`, JSON or cached profiles.
- Importing or constructing a service does not read `.env`, connect or validate
  schema. The dedicated CLI loads root `.env` without overriding process values;
  embedded callers own configuration loading or pass `EmployeeSourceSettings`.
- A lazy independent engine uses a pool of two, no overflow, a 10-second pool
  wait, 10-second connection timeout and 10-second statement timeout. Each call
  owns a `REPEATABLE READ`, `READ ONLY` transaction; normalization completes before
  commit. Error/cancellation rolls back and closes the connection scope. There
  are no automatic retries, background tasks or application startup hooks.
- Each owner must await `service.close()` in `finally`, on the same event loop,
  before closing the loop. Use `src.database.runtime.run_async` at synchronous
  Windows entrypoints; do not nest event loops. Closing rejects new calls with `source_closed`, drains active reads and disposes
  the pool once. Concurrent close calls share shielded cleanup; cancelling a closer
  does not cancel active reads or disposal. A closed service cannot reopen, including
  for empty batches. Reuse it within one event loop only.

## Internal service interface

The implementation lives exclusively in `src/type_b/employee_store/`; the former
Python import package has been removed. CLI command names remain unchanged.
`EmployeeStoreReader` declares the injected reader protocol. Immutable dataclasses
own full source profiles and manager results. Unified HTTP evidence models live in
`src/answering/context_contracts.py`; source-authored task requirements control
their field projection. Internal full-profile access does not grant HTTP exposure.

Operations:

| Operation | Result |
| --- | --- |
| `find_by_user_id(user_id)` | Immutable `EmployeeProfile` or `None` when absent |
| `find_many_by_user_ids(user_ids)` | Readonly mapping of found IDs to immutable profiles |
| `find_by_employee_id(employee_id)` | Full profile or `None`; `user_id` may be null |
| `find_many_by_employee_ids(employee_ids)` | Readonly mapping of found employee IDs |
| `find_manager_by_user_id(user_id)` | Immutable `ManagerResult` for the authenticated subject reference |
| `find_managers_by_user_ids(user_ids)` | Readonly mapping with one relation result per requested user ID |
| `check_source()` | Metadata-only `{status: 'ok', readonly: True, projection_columns: 64}` |
| `close()` | Dispose this source's pool; does not dispose the Type A engine |

IDs must be positive Python integers at most `2**53 - 1`; booleans, floats and
strings are rejected. Batches accept list/tuple, at most 200 input entries, and
deduplicate IDs. An empty batch does not require configuration or a connection.
Missing IDs are omitted from profile lookup mappings. Manager mappings instead
include `employee_not_found` for absent subjects. A malformed selected profile
fails the entire batch. No arbitrary SQL/session interface is exported.

Run `python -m src.cli.check_employee_source` for connectivity, readonly transaction,
role-capability and required-column checks. It selects zero employee rows, prints
only safe technical status and exits 0 on success or 1 on failure. It does not
require pgvector, write schema, grant permissions or run migrations. A successful
check does not prove business meanings, source completeness, freshness, caller
authorization or schema compatibility of every stored value.

## Readonly boundary and provisioning

**Every relation reached through this HRM connection is readonly, not just
employee_sso_store. Never write data or schema using this connection.**

Provision a dedicated non-owner, non-superuser login outside this application.
It needs CONNECT to the intended database, USAGE on the intended schema and
SELECT on the source relation. Do not grant write/DDL privileges, sequence
USAGE/UPDATE, privileged memberships, or execution of routines that can write.
Review inherited/PUBLIC permissions too, including database CREATE/TEMP and
schema CREATE. PostgreSQL commonly grants TEMP to PUBLIC: restricting only the
login's direct grants is insufficient. A DBA must review any privilege changes;
the application never executes GRANT, REVOKE or source migrations.

The source sets `default_transaction_read_only=on` at connection startup, sets
`READ ONLY` on every transaction and checks `transaction_read_only`. Fixed SELECT
queries use bound IDs and a validated, quoted schema. Source types are plain
dataclasses, never ORM entities, and are not registered in Alembic metadata.

The preflight rejects superuser/role-creation/database-creation/replication/RLS
bypass capabilities, CREATE/TEMP, non-system schema CREATE, owned or writable
non-system relations/sequences and executable volatile non-system routines. It
also examines memberships usable with SET ROLE. This is a conservative diagnostic,
not a complete security proof: stable/immutable routine declarations can be
incorrect, RLS/views/FDWs may have additional behavior, and privileges can change
after checking. A DBA must verify the actual upstream relation and routines.
The service always enforces readonly transactions; a powerful DB account is not
made least-privilege merely by setting a readonly transaction.

## Selection, normalization and failures

Full-profile queries explicitly project 64 columns for requested `user_id` or
`employee_id` groups. Manager queries use a fixed work-only projection including
all selector rank fields, without private/family/financial/health columns. ID reads do not
filter active, limit candidate rows or infer identity from names/emails/codes.
The private manager resolver additionally supports exact-code fallback as described below.
All candidates for each requested ID reach the selector:

1. Newest zoned `updated_at`, comparing microseconds; null is older.
2. Highest completeness: nonblank employee_name, employee_code, work_email, phone,
   avatar_url, job_title, department_name, plus a valid active boolean.
3. Highest employee_id.
4. Same rank with differing full projected values is `conflicting_rows`, before
   trimming or list normalization. Never merge fields from competing rows.

Employee-ID reads group by the exact employee ID, with the same ordering (the
ID tie-break is constant within that group). They do not switch to another employee
record sharing a user ID. For user-ID reads, this matches the backend representative ordering; it does not repair duplicates
or enforce global source uniqueness. Inactive winners remain inactive. Repeated
employee_id across selected user groups is invalid. Every result must belong to
the requested identity set. No profile is cached or persisted.

Every projected key is required. Nullable values remain null, text is trimmed
only at the outside, and blank text and the literal trimmed string `NULL` become null. Full department paths remain
intact. Dates become ISO dates; updated_at retains timezone and six fractional
digits. Numeric height/weight use Decimal without converting database Decimal to
float. Boolean source values and legacy `'true'`/`'false'` are accepted; numeric
booleans are rejected. Unknown enum strings remain strings without interpretation.
Collections accept driver-decoded arrays or serialized JSON arrays; null and
empty tuples remain distinct. Known child fields are validated, extra child keys
are not exposed. Trainings preserves arbitrary finite JSON in deeply immutable
containers until its schema is established. Returned objects suppress field-value
reprs; callers must still avoid logging extracted fields or serializing private
profiles to logs.

Stable errors distinguish invalid_identity, invalid_batch, invalid_projection,
invalid_data, invalid_list, invalid_timestamp, unexpected_identity,
conflicting_rows, schema_incompatible, unsafe_database_role, readonly_required,
source_timeout, source_access_denied, source_unavailable, source_closed and source_cleanup_failed.
Configuration errors name settings without printing URLs. Unknown database errors
are sanitized. Absence is never inferred from errors. No payload, raw SQL error,
parameters or profile content is logged by this module.

## Manager relation resolution

Only an authorized internal caller may use this service. Reads do not establish
end-user permission. No new HTTP route, automatic Type A enrichment, eligibility
calculation, free search, profile cache or recursive management-tree lookup is added.

`find_managers_by_user_ids` uses one readonly repeatable-read transaction and at
most three SELECT queries for up to 200 input subjects: subjects, managers by ID,
and manager-code candidate groups. It never performs one query per employee.
Subjects and manager summaries use the work projection declared in `fields.py`.
The immutable summary includes employee/user IDs, code/name, role/title,
department path, work email/phone, active and source update timestamp. It never
contains account ID, personal email, family, health or bank information.

Resolution is deterministic:

1. Select the representative subject by user ID before reading the link.
2. With `manager_id`, read that exact employee ID. If `manager_code` is present,
   compare the normalized case-sensitive code. A missing ID target never falls
   back to a code or name; disagreement is `conflicting_link`.
3. Only without an ID, use a trimmed case-sensitive manager code. Read complete
   candidate employee-ID groups, select their representatives and recheck the
   current code. Multiple matching employee identities are `ambiguous`.
4. The subject's own employee ID or nonnull user ID is `self_reference`.
   No link is `missing_link`; absent target is `manager_not_found`; absent
   subject is `employee_not_found`. A valid relation is `found` with a summary.
5. Unlinked managers (`user_id=null`) and inactive managers remain readable;
   inactive is retained rather than silently replaced. Missing work email remains
   null. No permission to approve a business request is inferred from the relation.

Malformed selected rows, conflicting source rows, timeout and access failures
remain sanitized exceptions, never absence or a partially successful batch.
Full-profile lookups validate all 64 fields. Manager reads validate only their
work projection; a malformed private list cannot block a work-summary read.

`fields.py` owns ordered projections, PostgreSQL type compatibility, nullable
metadata and the established cm/kg units; `normalization.py` owns value handling.
The dictionary below owns meanings, sensitivity and remaining source uncertainties.

## Property dictionary

This is the backend's **64-column projection**, not verified DDL or a claim that
the live relation has exactly 64 columns. Preflight requires compatible column
types; extra columns are ignored. It accepts integer identity columns, text
columns, numeric height/weight, bool active, timestamptz updated_at, date/timestamp
date fields, and JSON/JSONB/text array fields. Dates are projected with to_char;
updated_at is projected as text. PostgreSQL source checks have not been run merely
by authoring this document.

Types below show the expected source category → Python output. `?` means nullable;
the two identifiers without `?` are required by this adapter. A positive `user_id`
is required when present; source records without an account link remain readable
by employee ID. Blank required
name/code text may normalize to null. All values are internal personnel data.
Sensitivity: **I** = internal identity/work data, **P** = personal/contact,
**R** = restricted personal/financial/health/family data. These are handling
categories for this module, not a legal classification or an access policy.
Meaning basis: **B** = backend mapping/contract supports the description;
**N** = descriptive name-based meaning, not independently verified upstream;
**U** = a specific semantic uncertainty remains. No U/N property authorizes a
derived business decision.

| Property | Source → Python | Meaning / limitation | Sensitivity; basis |
| --- | --- | --- | --- |
| id | integer → int | Source row ID; ID system/relation to employee_id unconfirmed; not lookup identity | I; U |
| employee_id | integer → int | Employee record ID, positive; selector tie-break, distinct from user_id | I; B |
| employee_code | text? → str? | Employee code; not assumed unique | I; B |
| employee_name | text? → str? | Display name; never identity | I; B |
| job_role_name | text? → str? | Job role name; distinction from job_title needs confirmation | I; U |
| job_title | text? → str? | Job title | I; B |
| department_name | text? → str? | Complete organization path; do not discard segments | I; B |
| avatar_url | text? → str? | Avatar reference; module never fetches it | P; B |
| work_email | text? → str? | Work email; not an alternative identity in this module | P; B |
| phone | text? → str? | Backend maps to workPhone; upstream phone category needs confirmation | P; U |
| active | bool? → bool? | Employee-source active flag; not account/session state or eligibility | I; B |
| status_display | text? → str? | Display status; not a decision enum | I; U |
| updated_at | timestamptz? → ISO str? | Source update timestamp used for selection; not a freshness guarantee | I; B |
| old_employee_code | text? → str? | Previous employee code | I; N |
| probation_date | date/timestamp? → ISO date str? | Probation start date according to source | I; N |
| probation_end_date | date/timestamp? → ISO date str? | Probation end date according to source | I; N |
| official_date | date/timestamp? → ISO date str? | Official employment date; no automatic tenure rule | I; N |
| resignation_date | date/timestamp? → ISO date str? | Resignation date; no inferred active override | I; N |
| employee_type | text? → str? | Known backend codes trainee/student/freelance/employee; preserve unknowns | I; B |
| employee_group | text? → str? | HRM employee grouping; not portal authorization group | I; U |
| department_address | text? → str? | Department-associated address | I; N |
| birthday | date/timestamp? → ISO date str? | Birth date, not an instant | P; B |
| place_of_birth | text? → str? | Place of birth | P; N |
| hometown | text? → str? | Hometown | P; N |
| ethnicity | text? → str? | Ethnicity | R; N |
| religion | text? → str? | Religion | R; N |
| nationality | text? → str? | Nationality | P; N |
| marital | text? → str? | Known codes cohabitant/widower/divorced/single/married; preserve unknowns | R; B |
| social_insurance_number | text? → str? | Social insurance identifier; preserve leading zeroes | R; N |
| social_insurance_date | date/timestamp? → ISO date str? | Insurance-related date; exact milestone unconfirmed | R; U |
| health_insurance_number | text? → str? | Health insurance identifier | R; N |
| health_clinic | text? → str? | Registered healthcare facility according to Backend dashboard mapping | R; B |
| blood_type | text? → str? | Blood group | R; N |
| height | numeric? → Decimal? | Height in cm according to Backend dashboard mapping; no conversion | R; B |
| weight | numeric? → Decimal? | Weight in kg according to Backend dashboard mapping; no conversion | R; B |
| permanent_address | text? → str? | Permanent address | R; N |
| temporary_address | text? → str? | Temporary address | R; N |
| emergency_contact | text? → str? | Emergency contact name according to Backend dashboard mapping | R; B |
| emergency_address | text? → str? | Emergency contact address | R; N |
| emergency_phone | text? → str? | Emergency contact phone | R; N |
| notes | text? → str? | Free text; untrusted data, never instructions | R; U |
| tax_id | text? → str? | Tax identifier | R; N |
| identification_id | text? → str? | Identity document number; document type unconfirmed | R; U |
| id_issue_date | date/timestamp? → ISO date str? | Identity document issue date | R; N |
| id_issue_place | text? → str? | Identity document issuing place | R; N |
| bank_account_number | text? → str? | Bank account number; preserve leading zeroes | R; N |
| bank_name | text? → str? | Bank name | R; N |
| bank_branch | text? → str? | Bank branch | R; N |
| bank_account_name | text? → str? | Account holder name | R; N |
| work_history | array/JSON text? → tuple[WorkHistory]? | Work history; completeness/order not guaranteed | I; B |
| degrees | array/JSON text? → tuple[Degree]? | Education/degree records | P; B |
| trainings | array/JSON text? → immutable JSON tuple? | Item schema unestablished; do not invent fields | P; U |
| families | array/JSON text? → tuple[Family]? | Family/dependent records; no benefit inference | R; B |
| manager_id | integer? → int? | Manager employee ID according to Backend mapping; application relation, not a verified DB foreign key | I; B |
| manager_code | text? → str? | Manager source code | I; N |
| manager_name | text? → str? | Manager display name; no identity resolution | I; B |
| user_id | integer? → int? | Nullable positive HRM user ID; user lookup/group key; backend odooId linkage | I; B |
| account_id | integer? → int? | Positive HRM account link in backend alias flow; not internal UUID | I; B |
| private_email | text? → str? | Personal email | P; N |
| contract_kind | text? → str? | Contract category text; vocabulary/rules unconfirmed | I; U |
| private_state_id | integer? → int? | Private-address geographic ID; level/linkage unconfirmed | R; U |
| private_state_name | text? → str? | Private-address geographic label; level unconfirmed | R; U |
| temporary_state_id | integer? → int? | Temporary-address geographic ID; level unconfirmed | R; U |
| temporary_state_name | text? → str? | Temporary-address geographic label; level unconfirmed | R; U |

### Nested properties

All child keys listed below must be present. Child `id` accepts string or integer,
not bool, and is not interpreted as employee/user identity. Other child fields
are nullable. Text uses the same trim/null rule; date strings are validated and
canonicalized as ISO dates. Extra keys are ignored. Source array order is retained.

| work_history[] property | Type | Meaning / basis |
| --- | --- | --- |
| id | str/int | History row ID; I, B |
| date_start | ISO date? | Period start; I, N |
| date_end | ISO date? | Period end; null is not proof of current employment; I, U |
| job_title | str? | Job title; I, B |
| transfer_type | str? | Transfer category code; vocabulary unconfirmed; I, U |
| work_location | str? | Work location; I, N |
| decision_number | str? | Decision reference number; I, N |
| decision_date | ISO date? | Decision date; I, N |
| decision_place | str? | Decision place/issuing location; exact semantics unconfirmed; I, U |
| department_name | str? | Full department path for the history row; I, B |
| description | str? | Free-text description, untrusted; I, N |
| job_role_name | str? | Job role label; distinction from title unconfirmed; I, U |
| transfer_type_display | str? | Transfer category display label; I, B |

| degrees[] property | Type | Meaning / basis |
| --- | --- | --- |
| id | str/int | Degree record ID; P, B |
| degree_type | str? | Degree category code; vocabulary unconfirmed; P, U |
| major | str? | Major/specialization; P, N |
| school | str? | School/institution; P, N |
| level_display | str? | Education level display label; P, B |
| grade_display | str? | Grade/classification display label; P, B |
| grade | str? | Grade code; vocabulary unconfirmed; P, U |
| graduation_year | str? | Source graduation year text; not converted into a date; P, B |
| level | str? | Education level code; vocabulary unconfirmed; P, U |

| families[] property | Type | Meaning / basis |
| --- | --- | --- |
| id | str/int | Family record ID; R, B |
| name | str? | Family member name; R, N |
| birthday | ISO date? | Family member birth date; R, N |
| gender | str? | Gender code; vocabulary unconfirmed; R, U |
| gender_display | str? | Gender display label; R, B |
| relation_type | str? | Relationship code; vocabulary unconfirmed; R, U |
| relation_type_display | str? | Relationship display label; R, B |
| is_dependent | bool? | Source dependent flag; not a computed benefit entitlement; R, B |
| job | str? | Occupation; R, N |
| phone | str? | Contact phone; R, N |
| tax_id | str? | Tax identifier; R, N |
| registration_date | ISO date? | Registration date; registered process unconfirmed; R, U |
| from_date | ISO date? | Effective start; what takes effect is unconfirmed; R, U |
| to_date | ISO date? | Effective end; what takes effect is unconfirmed; R, U |

`trainings[]` intentionally has no declared child properties. It is opaque,
immutable JSON; it cannot support field-specific business answers until a source
contract is supplied. JSON nulls remain null and non-finite numbers are rejected.

## Verification and disposable fixture

Unit checks: `python -m pytest src/tests/test_employee_source.py src/tests/test_employee_store.py -q`.
Regression: `python -m pytest -m 'not integration'`.

Integration: `python -m pytest src/tests/test_employee_source_integration.py -q`.
It skips unless `TEST_HRM_DATABASE_URL` is set. It never loads `.env` and requires
`TEST_HRM_DISPOSABLE_CONFIRMED=true`; schema defaults to public or
`TEST_HRM_DATABASE_SCHEMA`. The database must differ from all configured app,
migration and other test databases. The flag is an operator assertion, not proof
of isolation: never configure shared or production storage.

A DBA/test harness must separately provision a disposable database, the readonly
login described above, and a table with all 64 typed columns. Seed only fictional
data: user 910001 has employee_name `Synthetic HRM Fixture`, official_date
`2020-01-02`; user 910002 has two records with distinct zoned updated_at values,
the newer inactive and the older active; user 910099 is absent. Provide distinct
positive employee IDs across selected users, nonnull integer id and valid known
child structures or null collections. The tests do not seed, migrate, grant,
truncate or clean up source storage. Disposable provisioning/destruction belongs
to its external owner.

Two test-only UPDATE/DELETE probes use WHERE false inside readonly transactions
and must fail with PostgreSQL 25006/42501; no rows are targeted. These probes are
never part of runtime code or preflight. Unit doubles do not establish PostgreSQL
privilege, rollback or live-schema guarantees. Unconfigured integration is skipped,
not passed. Source data and schema have not been verified against a live HRM
database by this implementation task.

### Additional disposable manager fixture

The integration fixture owner must also seed fictional subjects 910003 through 910010
with distinct positive employee IDs (930003 through 930010), valid work fields and all
64 full-profile keys. Subject manager links are:

| User ID | manager_id | manager_code | Expected relation |
| --- | --- | --- | --- |
| 910003 | 920001 | FICT-MGR | found |
| 910004 | null | FICT-MGR | found |
| 910005 | 929999 | FICT-MGR | manager_not_found (no fallback) |
| 910006 | 920001 | FICT-WRONG | conflicting_link |
| 910007 | null | FICT-DUP | ambiguous |
| 910008 | null | FICT-OLD | manager_not_found (obsolete code) |
| 910009 | 930009 | null | self_reference |
| 910010 | null | null | missing_link |

Manager employee 920001 has null user ID, code FICT-MGR, `active=false`, and work email `manager@example.test`. Employees 920002 and 920003
have null user IDs and the same code FICT-DUP. Employee 920004 has two rows with
strictly increasing zoned timestamps: old code FICT-OLD, newer code FICT-NEW.
All manager rows carry 64 valid keys so direct employee-ID reads can be checked.
Employee 929999 and user 910099 must be absent. Tests never provision, seed,
modify or clean this fixture; the existing zero-target write probes remain test-only.

## Context projection reader

`read_context(user_id, fields, manager=False)` is internal and accepts a tuple of
registered field names. It validates identity/projection before connection, reads
only selected and representative-ranking fields, and resolves an optional one-hop
manager in the same REPEATABLE READ readonly transaction. It returns immutable
selected values plus observation/source timestamps, without exposing identity
columns. Selection and full-profile APIs remain available for internal callers.
The HTTP context planner selects display/calculation fields from source-authored
requirements; see [unified context contract](contextual-answers.md).
