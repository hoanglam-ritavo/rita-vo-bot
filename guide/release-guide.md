# Rita VO Bot production release guide

Use this guide to deploy Rita VO Bot with a fresh PostgreSQL database, then
verify chat through Rita ID Backend and the Web Portal. Rehearse with the same
release on a dedicated disposable PostgreSQL environment first. For an existing
bot database, follow the [database upgrade guide](../src/database/README.md#existing-database-upgrades).

### Before you start

- Get the release tag or commit for the bot and the compatible Backend and
  Portal builds. Confirm who owns the bot, database, Backend and Portal steps.
- Confirm the bot host, working directory and shell. Run bot terminal commands
  from the selected checkout. Open **Windows · PowerShell** or **Linux · Bash**
  where shown. Use your selected shell throughout the package steps.
- Get the bot PostgreSQL host and database name, application login and
  migration login if separate. Identify the DBA who can enable pgvector. Also
  get the selected package list and E5 model directory and revision.
- Confirm the Backend and Portal URLs, where to obtain the private shared
  service key, and which employee account and source citation to use for smoke
  testing. If the Backend internal database is also new, ask its DBA to
  provision the reviewed base schema before applying the chat migration. The
  chat migration needs the existing `res_users` table.

Replace each `<placeholder>` before running a command. Work through one block
at a time and check its result before going on. Keep credentials, test inputs,
backups and release reports in private storage. Use `.tmp/` for local reports.

## 1. Prepare the release and its configuration

### Linux release commands

From the selected release checkout, use
`python3.14 release/linux-first-release.py` for a fresh bot database or
`python3.14 release/linux-release.py` for an initialized bot database.
Append `--apply` to the selected command to install requirements, fetch
Git LFS assets, check the database, and apply schema and corpus changes.
Preview checks checkout inputs and lists operations. Apply performs the
database checks. Use one release operator and keep the checkout stable
throughout the run.

For a first release, have the DBA enable `vector` before apply. For updates,
stop the bot and other publishers before apply and make `pg_dump` available.
Update mode makes a custom-format backup before schema or corpus writes.
Use direct PostgreSQL host, port and DB configuration for the target check.
The private backup and step/publication reports are under the printed
`.tmp/linux-release-*` directory. Retain the backup in protected storage.

After a successful handoff, start the bot through the existing service
manager and run the smoke checks in step 4. For a failed publication, inspect
the report and settle any uncertain attempt before retrying. If a first
release already applied migrations, verify schema before continuing with
update mode.

The release operator runs this step in the bot checkout on the bot host.

Use a separate checkout of an approved tag or commit. In a fresh clone, select
it with `git switch --detach <release-ref>`, then record `git rev-parse HEAD`
and confirm `git status --short` is empty. Use the model files, `chunks/`,
catalogs and manifests from that same release. Install Git LFS, Python 3.14 and
the PostgreSQL client tools for the target environment.

<details>
<summary>Windows · PowerShell</summary>

```powershell
py -3.14 -m venv env
.\env\Scripts\Activate.ps1
```

</details>

<details>
<summary>Linux · Bash</summary>

```bash
python3.14 -m venv env
source env/bin/activate
```

</details>

Run these commands in order in either shell. Use `requirements-dev.txt` when
setting up the disposable test environment for pytest.

```text
python -m pip install -r requirements.txt -r requirements-example.txt
git lfs pull
python -m src.cli.build_artifacts check
python -m src.cli.check_answer_catalog
python -m scripts.verify_runtime_assets
```

Expect `current` with zero changed files, `valid` for the catalog, and an empty
asset `errors` list. If any check fails, resolve missing LFS objects or obtain
the correct release bytes before proceeding. See
[runtime artifact operations](../docs/runtime-artifacts.md#git-lfs-checkout-and-verification).

Use the committed [environment example](../.env.example) to configure private
values through the deployment environment or a protected checkout-local `.env`.
Fill in `CHAT_BOT_SERVICE_KEY` or omit the blank assignment when the launcher
provides it. Keep `TEST_*` variables in the dedicated
[pytest process environment](../README.md#testing) using
[`.env.test.example`](../.env.test.example) as a reference.

| Before | Configuration | Check |
| --- | --- | --- |
| Migration and publication | `DATABASE_URL` for the bot database. Use `MIGRATION_DATABASE_URL` for a separate DDL role. | Both accounts target the same PostgreSQL database. Use `postgresql+psycopg` URLs and URL-encode credential characters. |
| Model preflight and publication | Use the selected E5 directory and full 40-hex revision in `--model-path` and `--model-revision`. Set the matching `E5_MODEL_PATH` and `E5_MODEL_REVISION` for HTTP startup. | Publisher and bot use byte-identical model files. |
| HTTP startup | `API_HOST=127.0.0.1`, the chosen `API_PORT` (default `8000`), `CHAT_BOT_SERVICE_KEY` and the capability settings in step 4. | Backend and the bot use the same private key. Keep it out of browser configuration, logs and shell transcripts. |

Standalone DB commands give process variables precedence. HTTP startup gives
the repository `.env` precedence, including for `DATABASE_URL`. Keep one
effective configuration for each setting and verify that both entrypoints point
to the same intended target before either writes or serves requests.

**Continue when** the checkout is pinned, all three checks pass, and the
operator and DBA agree on the target database and model identity. **Stop if**
the release bytes or effective configuration are unclear.

## 2. Provision PostgreSQL and apply migrations

The DBA prepares the database. The release operator applies migrations from
the bot checkout. Migrations create the tables and other schema objects. The
next step writes documents and their embeddings into those tables.

### DBA · Confirm the database and enable pgvector

Create the empty bot database and install pgvector on its PostgreSQL server.
Connect with the provisioning role in `psql` or the DBA's SQL client.

```sql
SELECT current_database();
```

Confirm the database name and server against the release inputs. Then enable
the extension in that session. The baseline creates an embedding column of
type `Vector(384)` before the later guard migration runs.

```sql
CREATE EXTENSION IF NOT EXISTS vector;
SELECT extversion FROM pg_extension WHERE extname = 'vector';
```

The query must return one version before the release operator runs Alembic.

### Release operator · Inspect, migrate and verify

`heads` reads the release files. `current`, `upgrade` and `check` use the
migration connection. It defaults to `DATABASE_URL` if `MIGRATION_DATABASE_URL`
is absent or empty. Inspect the target before writing to it.

```text
python -m alembic heads
python -m alembic current
```

Expect one release head and no applied revision for a fresh database. If the
database already has application tables or a recorded revision, stop and use
the [database upgrade guide](../src/database/README.md#existing-database-upgrades).

When the DBA confirms the DB and extension, apply the migrations. This command
writes to PostgreSQL.

```text
python -m alembic upgrade head
```

```text
python -m alembic current
python -m alembic check
python -m src.cli.check_db
```

`check_db` uses `DATABASE_URL`. For this check, use the eventual bot service
login if publication uses a separate writer. **Continue when** `current`
matches `heads`, `check` passes and `check_db` reports the pgvector version.
**Stop if** the DB or revision differs. For a missing vector type, recheck the
extension on the exact target. For a permissions error, ask the DBA to confirm
the role for the failed operation.

## 3. Publish and verify the document corpus

The release operator runs these commands from the bot checkout with the
approved model and the bot database writer in `DATABASE_URL`. A package is a
folder under `chunks/` containing one document's prepared content and metadata.

### Confirm the release manifest

Use the tracked [corpus manifest](../chunks/manifest.json) from the selected
release. It identifies each document, revision, package fingerprint and
expected chunk count. Keep it with the exact packages and model used for
release. The release preparer uses `python -m src.cli.publish_corpus freeze
--all` when intentionally creating the manifest for every package, or repeats
`--package chunks/<document-id>` for a selected set. To replace an existing
manifest, add `--replace` and review the manifest diff with the package
changes. Operators use the manifest shipped with the release.

### Preview the release

Use the explicit model directory and full revision from step 1. Run this
optional preview in either PowerShell or Bash from the bot checkout.

```text
python -m src.cli.publish_corpus plan --model-path "<local-e5-directory>" --model-revision "<40-hex-e5-revision>"
```

Expect `status=planned` and check the package IDs, counts and dependency waves
against the release. The command validates package bytes and token lengths
using the real tokenizer. Its `database_checks` is `not_run`. PostgreSQL
checks occur during apply.

### Publish to PostgreSQL

After confirming `DATABASE_URL` and reviewing the intended manifest, run the
following from the same checkout. This command writes to PostgreSQL.

```text
python -m src.cli.publish_corpus apply --model-path "<local-e5-directory>" --model-revision "<40-hex-e5-revision>"
```

Apply checks every selected package and model input before writing. It
dry-runs each dependency group against PostgreSQL before publishing that
group. This gives later groups access to earlier committed relation targets.
A fresh database may report absent historical predecessors as
`missing_supersedes`. Missing required relation targets and cycles block
publication. Each package commits independently.

Keep the JSON report in private storage. Read the results with
`phase=publish` for confirmed writes or skips.

| Result | Action |
| --- | --- |
| `status=completed` | Confirm that all expected packages have a publish result and that `unattempted` is empty. |
| `published` or `skipped` in the publish phase | Retain the run ID and attempt ID. A skipped package matched its active publication. |
| `rejected` or `failed` | Review the phase, result code and `unattempted` list. Earlier package commits remain in the DB. Resolve the failure before running again. |
| `publication_outcome_unknown` | Inspect the attempt with `python -m src.cli.publication_attempt --id <uuid>`. Reconcile a matching durable receipt after the publisher stops before deciding whether to rerun. |

See the [CLI reference](../src/cli/README.md#manifest-based-corpus-release)
and [publication recovery guide](../docs/corpus-publication.md#verification-and-failure-handling).

### Compare the stored corpus with the release inventory

Run this read-only check against the bot database. Compare the exact document and
revision IDs and counts with the release inventory. Each selected revision must
have one active successful run with the expected model revision and model file
fingerprint from preflight. `recorded_chunks`, `stored_chunks` and
`embedded_chunks` must all match the expected count. Keep this metadata privately
for the repeat check.

```sql
BEGIN READ ONLY;
SELECT r.document_id, r.id AS revision_id, i.id AS run_id, i.model_revision,
       i.configuration->>'model_files_sha256' AS model_files_sha256,
       i.chunk_count AS recorded_chunks, count(c.id) AS stored_chunks,
       count(c.id) FILTER (WHERE vector_dims(c.embedding) = 384) AS embedded_chunks,
       array_agg(c.id ORDER BY c.chunk_index) FILTER (WHERE c.id IS NOT NULL) AS chunk_ids
FROM document_revisions AS r
LEFT JOIN ingestion_runs AS i
  ON i.revision_id = r.id AND i.is_active AND i.status = 'succeeded'
LEFT JOIN document_chunks AS c ON c.run_id = i.id
WHERE r.is_catalog_current
GROUP BY r.document_id, r.id, i.id, i.model_revision,
         i.configuration->>'model_files_sha256', i.chunk_count
ORDER BY r.document_id, r.id;
COMMIT;
```

Run `apply` again with the same manifest and model and keep a new report.
Expect every publish result to be `skipped`. Rerun the query and compare
the run IDs, ordered chunk IDs and counts with the original results. Attempt
receipts may increase during a repeat. Resolve differences before starting the
service.

**Continue when** the DB matches the inventory and each unchanged package
returns `skipped` on repeat. **Stop if** a package is missing, a run or vector
differs from the expected results, or a publication outcome is unresolved.

## 4. Start the bot and verify before opening traffic

The release operator starts the bot and runs the local checks on the bot host.
The Backend and Portal operators verify their components in that order. Keep
employee traffic closed until the final checklist passes.

### Start the bot

Choose the capabilities for the rollout. HTTP authentication is required for
every configuration. Set `CHAT_BOT_SERVICE_KEY` privately before startup.
For a Portal chat release, enable both template answers and HRM conversations.

| Capability | Settings and prerequisites |
| --- | --- |
| Retrieval only | Start with `ANSWER_ENABLED=false`, `CONVERSATION_ENABLED=false`, `TYPE_B_ENABLED=false` and `SEMANTIC_MODE=off`. |
| Template answers | Set `ANSWER_ENABLED=true`, `SEMANTIC_MODE=expand` and `RERANK_ENABLED=true`. Set `SEMANTIC_ARTIFACT_PATH=artifacts/semantics/lexicon.json` and use the matching `ANSWER_CATALOG_PATH=artifacts/answers/catalog.json`. Semantic expansion requires the pinned AVX2 CPU reranker. |
| HRM conversations | Enable template answers and set `CONVERSATION_ENABLED=true`. Provision the compatible Backend chat schema and configure Backend's `CHAT_BOT_BASE_URL` and the matching `CHAT_BOT_SERVICE_KEY`. |
| HRM personnel evidence | With the HRM SELECT-only connection configured in `HRM_DATABASE_URL` and `HRM_DATABASE_SCHEMA` as applicable, run `python -m src.cli.check_employee_source`. After a successful preflight, set `TYPE_B_ENABLED=true` if this rollout includes personnel evidence. |

See [model and reranker requirements](../docs/online-reranking.md#local-model-and-configuration)
and [HRM source provisioning](../docs/employee-source.md#readonly-boundary-and-provisioning).
From the selected checkout, use this entrypoint as the service command. It binds
to loopback and starts one worker.

```text
python -m src.cli.serve_api
```

### Local HTTP smoke tests

Use a second shell. Supply the same key through that shell's private environment
without printing it. Set `BOT_SMOKE_HRM_USER_ID` to a designated authorized HRM
user ID. Set `BOT_SMOKE_QUERY` to a private source-grounded question and record
its expected citation before testing. Change the port below if configured.
Uncomment the answer probe before running the block if answers are enabled.

<details>
<summary>Windows · PowerShell</summary>

```powershell
$base = 'http://127.0.0.1:8000'
if ([string]::IsNullOrWhiteSpace($env:CHAT_BOT_SERVICE_KEY)) { throw 'Set the probe service key privately' }
$headers = @{ 'X-Rita-Service-Key' = $env:CHAT_BOT_SERVICE_KEY }
Invoke-RestMethod -Uri "$base/health/live" -TimeoutSec 5 -ErrorAction Stop

$status = try {
    (Invoke-WebRequest -UseBasicParsing -Uri "$base/health/ready" -TimeoutSec 20 -ErrorAction Stop).StatusCode
} catch {
    if ($null -eq $_.Exception.Response) { throw }
    [int]$_.Exception.Response.StatusCode
}
if ($status -ne 401) { throw "Expected HTTP 401 without a key, got $status" }
Invoke-RestMethod -Uri "$base/health/ready" -Headers $headers -TimeoutSec 20 -ErrorAction Stop
# If answers are enabled, also run:
# Invoke-RestMethod -Uri "$base/health/answer" -Headers $headers -TimeoutSec 20 -ErrorAction Stop

$payload = @{
    text = $env:BOT_SMOKE_QUERY
    hrm_user_id = [long]$env:BOT_SMOKE_HRM_USER_ID
    top_k = 5
} | ConvertTo-Json -Compress
$body = [Text.Encoding]::UTF8.GetBytes($payload)
Invoke-RestMethod -Method Post -Uri "$base/api/v1/retrieve" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $body -TimeoutSec 60 -ErrorAction Stop
```

</details>

<details>
<summary>Linux · Bash</summary>

```bash
(
    set -euo pipefail
    base='http://127.0.0.1:8000'
    : "${CHAT_BOT_SERVICE_KEY:?Set the probe service key privately}"
    probe() {
        curl --fail --silent --show-error --write-out '\nHTTP %{http_code}\n' \
            --header @<(printf 'X-Rita-Service-Key: %s\n' "$CHAT_BOT_SERVICE_KEY") "$@"
    }

    curl --fail --silent --show-error --max-time 5 --write-out '\nHTTP %{http_code}\n' "$base/health/live"
    status=$(curl --silent --show-error --max-time 20 --output /dev/null --write-out '%{http_code}' "$base/health/ready")
    if [[ "$status" != 401 ]]; then
        printf 'Expected HTTP 401 without a key, got %s\n' "$status" >&2
        exit 1
    fi
    probe --max-time 20 "$base/health/ready"
    # If answers are enabled, also run:
    # probe --max-time 20 "$base/health/answer"

    python - <<'PY' | probe --max-time 60 --header 'Content-Type: application/json' --data-binary @- "$base/api/v1/retrieve"
import json
import os

print(json.dumps({
    "text": os.environ["BOT_SMOKE_QUERY"],
    "hrm_user_id": int(os.environ["BOT_SMOKE_HRM_USER_ID"]),
    "top_k": 5,
}))
PY
)
```

</details>

Require HTTP 200 from each applicable authenticated probe. Compare the retrieval
match to the expected source and keep the response private. Rerun the request
with an outside-corpus question. Retrieval candidates may still be returned and
remain `unassessed`. If answers are enabled, repeat the two requests against
`/api/v1/answer`. Check schema version 2, the expected citation for the supported
question and an explicit fallback or unsupported outcome for the other question.
See the [version 2 evidence contract](../docs/contextual-answers.md#version-2-evidence-contract).

### Connect Backend

The Backend operator confirms that the reviewed internal schema and chat
migration are in place. Configure `CHAT_BOT_BASE_URL` with the bot origin and
`CHAT_BOT_SERVICE_KEY` with the same private key as the bot. For a remote
Backend, use the bot's HTTPS proxy with certificate verification. A Backend
in the same network namespace can use `http://127.0.0.1:8000` with the chosen
bot port. See [bot HTTP authentication](../docs/http-service-auth.md) and
the Backend repository's `docs/chat-bot-https.md` for proxy and rollout details.

For a new Backend build, run these commands from the matching Backend checkout
with its production configuration. Use the last command as its service entrypoint.

```text
npm ci
npm run build
npm run start:prod
```

With the designated employee's authenticated session, send a `POST` to
`/chat-bot/retrieve` relative to the Backend base URL. For the same-domain
proxy layout, the public path is `/api/chat-bot/retrieve`. Send a JSON body
with the `text` field set to the selected smoke question. Expect HTTP 200 and
the matching source evidence from the bot. Backend resolves the employee's HRM
identity from the session and keeps the service key server-side.

### Connect and verify the Web Portal

The Portal operator sets `ENV_MODE` and `NEXT_PUBLIC_ENV_MODE` to `production`
and sets `BACKEND_API_URL` to the public HTTPS Backend base URL **before** a
build. For the same-domain layout, use a URL like `https://portal.example.com/api`
and have the proxy strip `/api` before forwarding to Backend. See **Local and
release routing** in the Portal README for the deployment settings.

For a new Portal build, run these commands from the matching Portal checkout.
Use the last command as its service entrypoint.

```text
npm ci
npm run build
npm run start
```

Sign in with the designated employee account, open `/hrm`, create a room, send
the known question and check its source citation. Reopen the room to verify
history. Delete the test room and check that Backend completes the purge.
See [conversation verification](../docs/conversations.md#deletion-and-verification).

**Stop if** the bot probe passes but Backend cannot call it, the Portal
cannot establish the employee session, or the conversation or citation differs
from the expected result. Check the matching URL, TLS, shared key and service
versions at the failed boundary before opening traffic.

### Release checks and recovery

- On the disposable rehearsal target, follow the
  [PostgreSQL test configuration](../README.md#testing) for migrations,
  publication and any conversation/HRM suites in scope. Use database fixtures
  dedicated to testing. For answer releases, follow the
  [holdout and Type A procedure](../docs/business-topics.md#template-coverage-and-holdout)
  using the commands below. Run the second with an authorized SELECT-only
  Type A source.

  ```text
  python -m src.cli.evaluate_questions --questions-dir .tmp/<private-set> --output .tmp/<fixture-report>.json
  python -m src.cli.evaluate_questions --questions-dir .tmp/<private-set> --output .tmp/<readonly-report>.json --readonly
  ```
- Record the release SHA, migration head and private report locations. Before
  cutover, keep a protected PostgreSQL backup using `pg_dump --format=custom`
  and verify it with `pg_restore` on a separate disposable database.
- **Open traffic when** the schema and corpus checks pass, the bot and Backend
  smoke checks pass, and the applicable Portal and quality checks pass. After
  cutover, watch latency and the rate of `429`, `503` and timeout responses.
- **If rollback is needed**, close new chat requests, drain the worker and use
  a compatible bot, Backend, Portal and artifact set. Retain chat rows,
  tombstones and pending purge jobs, and keep the purge path operational.
  Treat database recovery as a separate operation using the tested backup.
