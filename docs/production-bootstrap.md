# Bootstrap Rita VO Bot on a fresh production environment

Use this runbook for a new bot deployment with an empty PostgreSQL database.
Rehearse the same release on a dedicated disposable PostgreSQL environment first.
For an existing bot database, follow the [database upgrade guide](../src/database/README.md#existing-database-upgrades).
For a new Rita ID Backend database, provision its reviewed internal schema
separately. The Backend chat migration depends on the existing `res_users`
table. See `src/database/migrations/README.md` and `docs/chat-bot-https.md` in
the Backend repository.

Run the commands from the selected bot checkout. Where PowerShell and Bash
examples differ, use the block for your shell. Replace placeholders before
running commands and stop at any failed check. Keep release reports, protected
backups and test inputs in private storage. Use `.tmp/` for local reports.

## 1. Prepare the release and its configuration

Use a separate checkout of an approved tag or commit. In a fresh clone, select
it with `git switch --detach <release-ref>`, then record `git rev-parse HEAD`
and confirm `git status --short` is empty. Use the model files, `chunks/`,
catalogs and manifests from that same release. Install Git LFS, Python 3.14 and
the PostgreSQL client tools for the target environment.

PowerShell

```powershell
py -3.14 -m venv env
.\env\Scripts\Activate.ps1
```

Bash

```bash
python3.14 -m venv env
source env/bin/activate
```

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
the correct release bytes before proceeding. The checks cover artifact integrity
and pairing. See [runtime artifact operations](runtime-artifacts.md#git-lfs-checkout-and-verification).

Use the committed [environment example](../.env.example) to configure private
values through the deployment environment or a protected checkout-local `.env`.

| Before | Configuration | Check |
| --- | --- | --- |
| Migration and publication | `DATABASE_URL` for the bot database. Use `MIGRATION_DATABASE_URL` for a separate DDL role. | Both accounts target the same PostgreSQL database. Use `postgresql+psycopg` URLs and URL-encode credential characters. |
| Model preflight and publication | Use the selected E5 directory and full 40-hex revision in `--model-path` and `--model-revision`. Set the matching `E5_MODEL_PATH` and `E5_MODEL_REVISION` for HTTP startup. | Publisher and bot use byte-identical model files. |
| HTTP startup | `API_HOST=127.0.0.1`, the chosen `API_PORT` (default `8000`), `CHAT_BOT_SERVICE_KEY` and the capability settings in step 4. | Backend and the bot use the same private key. Keep it out of browser configuration, logs and shell transcripts. |

Standalone DB commands give process variables precedence. HTTP startup gives
the repository `.env` precedence, including for `DATABASE_URL`. Keep one
effective configuration for each setting and verify that both entrypoints point
to the same intended target before either writes or serves requests.

## 2. Provision PostgreSQL and apply migrations

1. Create the empty bot database and install pgvector on its PostgreSQL server.
   Connect with the provisioning role using `psql` or the DBA's SQL client.
   Run `SELECT current_database();` and confirm that it is the intended target.
2. In that session, enable and check the extension before invoking Alembic.

   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   SELECT extversion FROM pg_extension WHERE extname = 'vector';
   ```

   The second statement must return one version. The baseline creates
   `Vector(384)` columns before the later guard migration runs.
3. Run the Alembic commands below. `heads` reads the release files. `current`,
   `upgrade` and `check` use the migration connection, which defaults to
   `DATABASE_URL` if `MIGRATION_DATABASE_URL` is absent or empty. Run the final
   command with the service account in `DATABASE_URL` if publication uses a
   separate writer.

   ```text
   python -m alembic heads
   python -m alembic current
   python -m alembic upgrade head
   python -m alembic current
   python -m alembic check
   python -m src.cli.check_db
   ```

The first `current` should show no applied revision for a fresh database. After
upgrade, `current` must match `heads`, `check` must succeed, and `check_db` must
report the pgvector version. Only `upgrade` applies migrations in this sequence.
If the target is wrong, stop and verify both effective URLs. For a missing type,
recheck `vector` in that exact database. For permission errors, have the DBA
check the provisioning or migration role for the operation that failed. If the
database already has application tables or a recorded revision, use the
[database guide](../src/database/README.md#apply-existing-migrations) to
identify the appropriate procedure before writing anything.

## 3. Publish and verify the document corpus

### Select and preflight the packages

Use `git ls-files -- 'chunks/*/metadata.json'` to enumerate current tracked
packages. Record the intended packages in a private release inventory with
`document_id`, `revision_id`, target revision IDs from `relations`, and expected
chunk counts from `chunks.json`. Include every eligible package in the intended
corpus. Resolve unexpected invalid, held or missing packages before cutover.
Read [package eligibility and layout](offline-indexing.md#commands) for v2/v3
package requirements.

Fill in the E5 values and list every selected package directory in the shell
example. Choose a new report directory for each run. The examples stop when a
command fails or a preflight report includes metadata blockers. Preflight loads
the real tokenizer and checks token lengths. Its JSON can report blockers even
when the command exits successfully. The database dry-run in the next step
checks planned writes and relations separately.

PowerShell

```powershell
$modelPath = '<local-e5-directory>'
$modelRevision = '<40-hex-e5-revision>'
$reportDir = '.tmp/production-bootstrap-<run-id>'
$packages = @('chunks/<document-id-1>', 'chunks/<document-id-2>')
New-Item -ItemType Directory -Path $reportDir -ErrorAction Stop | Out-Null

foreach ($package in $packages) {
    $id = Split-Path -Leaf $package
    python -m src.cli.prepare_documents validate --package "$package" --json > "$reportDir/$id.validate.json"
    if ($LASTEXITCODE -ne 0) { throw "Validation failed for $id" }

    $preflight = "$reportDir/$id.preflight.json"
    python -m src.cli.preflight_documents --package "$package" --model-path "$modelPath" --model-revision "$modelRevision" --json > $preflight
    if ($LASTEXITCODE -ne 0) { throw "Preflight failed for $id" }
    $result = (Get-Content -LiteralPath $preflight -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop).results[0]
    if ($result.metadata_blockers) { throw "Publication metadata blocked for $id. See $preflight" }
}
```

Bash

```bash
model_path='<local-e5-directory>'
model_revision='<40-hex-e5-revision>'
report_dir='.tmp/production-bootstrap-<run-id>'
packages=('chunks/<document-id-1>' 'chunks/<document-id-2>')

(
    set -e
    mkdir -p .tmp
    mkdir "$report_dir"
    for package in "${packages[@]}"; do
        id=${package##*/}
        python -m src.cli.prepare_documents validate --package "$package" --json > "$report_dir/$id.validate.json"

        preflight="$report_dir/$id.preflight.json"
        python -m src.cli.preflight_documents --package "$package" --model-path "$model_path" --model-revision "$model_revision" --json > "$preflight"
        python - "$preflight" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
result = json.loads(path.read_text(encoding="utf-8"))["results"][0]
if result["metadata_blockers"]:
    raise SystemExit(f"Publication metadata blocked. See {path}")
PY
    done
)
```

### Dry-run and publish one dependency group at a time

Start with packages whose target revisions are already present or that have no
relations. In an empty database, selecting both ends of a relation in one dry-run
can produce `relation_target_missing`: dry-run reads the current DB without
simulating an earlier package's commit. Publish the target group first. Stop if
a dependency is outside the intended corpus or the selected relations form a
cycle. Prefer repeated `--package` arguments so the scope matches the inventory.
`--all` selects every package in the current layout.

In the same shell, select a fresh group name and only the directories for that
group. Run the dry-run and review its JSON before executing the publish command.

PowerShell dry-run

```powershell
$group = 'group-01'
$selection = @('--package', 'chunks/<first-id>', '--package', 'chunks/<second-id>')
python -m src.cli.publish_documents @selection --model-path "$modelPath" --model-revision "$modelRevision" --dry-run --json > "$reportDir/$group.dry-run.json"
if ($LASTEXITCODE -ne 0) { throw "Dry-run failed. See $reportDir/$group.dry-run.json" }
```

PowerShell publication

```powershell
python -m src.cli.publish_documents @selection --model-path "$modelPath" --model-revision "$modelRevision" --json > "$reportDir/$group.publish.json"
if ($LASTEXITCODE -ne 0) { throw "Publication failed. See $reportDir/$group.publish.json" }
```

Bash dry-run

```bash
group='group-01'
selection=(--package 'chunks/<first-id>' --package 'chunks/<second-id>')
python -m src.cli.publish_documents "${selection[@]}" --model-path "$model_path" --model-revision "$model_revision" --dry-run --json > "$report_dir/$group.dry-run.json"
```

Bash publication

```bash
python -m src.cli.publish_documents "${selection[@]}" --model-path "$model_path" --model-revision "$model_revision" --json > "$report_dir/$group.publish.json"
```

| Result | Action |
| --- | --- |
| `processed` with `dry_run` | Compare the planned revision and chunk counts with the inventory, then publish the group. |
| `processed` with `published` | Retain the run and attempt IDs. Continue after all expected packages in the group are accounted for. |
| `skipped` | An unchanged publication was verified. Compare its run ID with the prior receipt if this is a repeat. |
| `rejected` or `failed` | Stop between groups. Each package commits independently, so inspect all results before resuming. Correct the underlying error and use ordinary publication for the intended unchanged inputs. |
| `publication_outcome_unknown` | Inspect `python -m src.cli.publication_attempt --id <uuid>` before retrying. Use `--reconcile` only after confirming the publisher has stopped and a matching commit receipt exists. Escalate an unresolved receipt for investigation. |

Use ordinary publication on bootstrap and repeat runs. Reserve `--force` and
`--update-metadata` for separately reviewed re-encoding or metadata changes.
See [publication verification and recovery](corpus-publication.md#verification-and-failure-handling).

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

Repeat each publication command with the same model and package inputs and write
its output to a new report file. Expect every package to be `skipped`. Rerun the
query and compare the run IDs, ordered chunk IDs and counts with the original
results. Attempt receipts may increase during a repeat. Resolve differences
before starting the service.

## 4. Start the bot and verify before opening traffic

Choose the capabilities for the rollout. HTTP authentication is required for
every configuration. Set `CHAT_BOT_SERVICE_KEY` privately before startup.

| Capability | Settings and prerequisites |
| --- | --- |
| Retrieval only | Start with `ANSWER_ENABLED=false`, `CONVERSATION_ENABLED=false`, `TYPE_B_ENABLED=false` and `SEMANTIC_MODE=off`. |
| Template answers | Set `ANSWER_ENABLED=true`, `SEMANTIC_MODE=expand` and `RERANK_ENABLED=true`. Set `SEMANTIC_ARTIFACT_PATH=artifacts/semantics/lexicon.json` and use the matching `ANSWER_CATALOG_PATH=artifacts/answers/catalog.json`. Semantic expansion requires the pinned AVX2 CPU reranker. |
| HRM conversations | Enable template answers and set `CONVERSATION_ENABLED=true`. Provision the compatible Backend chat schema and configure Backend's `CHAT_BOT_BASE_URL` and the matching `CHAT_BOT_SERVICE_KEY`. |
| HRM personnel evidence | With the HRM SELECT-only connection configured in `HRM_DATABASE_URL` and `HRM_DATABASE_SCHEMA` as applicable, run `python -m src.cli.check_employee_source`. After a successful preflight, set `TYPE_B_ENABLED=true` if this rollout includes personnel evidence. |

See [model and reranker requirements](online-reranking.md#local-model-and-configuration)
and [HRM source provisioning](employee-source.md#readonly-boundary-and-provisioning).
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

PowerShell

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

Bash

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

Require HTTP 200 from each applicable authenticated probe. Compare the retrieval
match to the expected source and keep the response private. Rerun the request
with an outside-corpus question. Retrieval candidates may still be returned and
remain `unassessed`. If answers are enabled, repeat the two requests against
`/api/v1/answer`. Check schema version 2, the expected citation for the supported
question and an explicit fallback or unsupported outcome for the other question.
See the [version 2 evidence contract](contextual-answers.md#version-2-evidence-contract).

### Release checks and recovery

- On the disposable rehearsal target, follow the
  [PostgreSQL test configuration](../README.md#testing) for migrations,
  publication and any conversation/HRM suites in scope. Use database fixtures
  dedicated to testing. For answer releases, follow the
  [holdout and Type A procedure](business-topics.md#template-coverage-and-holdout)
  using the commands below. Run the second with an authorized SELECT-only
  Type A source.

  ```text
  python -m src.cli.evaluate_questions --questions-dir .tmp/<private-set> --output .tmp/<fixture-report>.json
  python -m src.cli.evaluate_questions --questions-dir .tmp/<private-set> --output .tmp/<readonly-report>.json --readonly
  ```
- For a remote Backend, verify the [HTTPS and authentication boundary](http-service-auth.md)
  before opening routing. Forward authenticated business traffic through the
  proxy and keep local probes private. For HRM chat, use a designated test account
  to create a room, send a turn, read history and verify deletion and purge
  through Backend before opening the portal. See the
  [conversation verification guide](conversations.md#deletion-and-verification).
- Open traffic after the schema matches the release head, the corpus and repeat
  checks match the inventory, and the applicable HTTP and quality checks pass.
  Record the release SHA, migration head and private report locations. After
  cutover, watch latency and the rate of `429`, `503` and timeout responses.
- Keep a protected PostgreSQL backup using `pg_dump --format=custom` and verify
  it with `pg_restore` on a separate disposable database before cutover. For
  application rollback, close new entrypoints, drain the worker and restore a
  compatible bot, Backend and artifact set. Retain chat rows, tombstones and
  pending purge jobs, and keep the purge path operational. Treat database
  recovery as a separate operation using the tested backup.
