<!-- Current answer contract: docs/contextual-answers.md -->
# Durable structured conversations

Opt-in conversations extend the existing template answer engine. No generative
model, broker, new port, hosted service, automatic aggregates or personal
eligibility decisions are added. Backend owns rooms and employee authorization;
the bot owns turns, messages, checkpoints and deletion tombstones. The portal uses stored private rooms and the unified answer/evidence view.

## Enablement and storage

Provision the additive Alembic revision `d8e031c72b19` after `c7d92e6a104b`
explicitly, with the old bot stopped. It adds `chat_conversations`, `chat_turns`
and `chat_messages` to the existing application database, not the readonly HRM
source. The total application schema now has 17 tables. No migration runs at
startup. Downgrade destroys chat storage and deletion receipts and is not an
application rollback procedure.

`CONVERSATION_ENABLED=false` by default. `CHAT_BOT_SERVICE_KEY` is always required
for HTTP startup and must match Backend. Keep it private.
Configure `ANSWER_ENABLED`, semantics and the source-bound catalog as documented
for answers; Type B retains its existing opt-in source configuration. The new
service credential authenticates all HTTP business operations, including stateless
Type A retrieval/answer and history. TYPE_B_SERVICE_KEY is retired. Backend resolves
numeric HRM references from its authenticated internal user; no lookup by name
or email is performed. If Type B is disabled the reference is not used; its
facts are not fabricated or restored from cache.

See [shared authentication and remote HTTPS](http-service-auth.md). Python still
binds loopback; Backend can connect through a separate HTTPS reverse proxy.

## Internal wire contract

All conversation routes require `X-Rita-Service-Key` and return no-store responses.
The ordinary stateless `/api/v1/answer` body/response remains available.
Conversation answer requests add all four identity/version fields together:

```json
{
  "user_id": "11111111-1111-4111-8111-111111111111",
  "room_id": "22222222-2222-4222-8222-222222222222",
  "message_id": "33333333-3333-4333-8333-333333333333",
  "expected_context_version": 0,
  "text": "Fictional policy question",
  "hrm_user_id": 123,
  "top_k": 5
}
```

Text is trimmed, 1–2,000 characters; identities are UUIDs. Partial conversation
identity and extra fields are rejected. HTTP 200 returns a terminal turn; 202
means that same message is processing. The envelope contains `room_id`,
`message_id`, `sequence`, `state`, `context_version`, `attempt`, nullable `result`
and nullable controlled `error_code`. COMPLETED has an answer result; other
states have no result. Answer outcomes remain distinct from transport state.

A task clarification can include optional `result.clarification_options` with
one to five ordered `{id, label}` entries. An explicit selection adds
`choice: {prompt_message_id, option_id}` and sends the displayed label as
`text`. The bot validates the prompt, option, label and context version before
reserving a new turn. Stale choices return 409, and history hides actionable options from a
replaced catalog. Numbered and textual replies remain supported. Text-only requests keep their original replay fingerprint.

| Route | Input / purpose |
| --- | --- |
| `POST /api/v1/answer` | Complete conversation request above, or stateless body with required hrm_user_id |
| `GET /api/v1/conversations/{room_id}/turns/{message_id}` | `user_id` query; inspect without inference |
| `POST /api/v1/conversations/{room_id}/turns/{message_id}/retry` | `{user_id, hrm_user_id}`; explicit retry |
| `GET /api/v1/conversations/{room_id}/messages` | `user_id`, `limit` (20, max 50), `order=asc` (default) with exclusive `after` (default 0), or `order=desc` with exclusive `before` (omit for newest). |
| `POST /api/v1/conversations/{room_id}/purge` | `{user_id}`; idempotent permanent purge |

Message pages are turn records with the original `text`, stored result and turn
state, plus `next_cursor` and `context_version`. They do not regenerate answers.
Page serialization is bounded below 2 MiB. Follow the returned cursor in the
same direction even when the page is shorter than `limit`. The latest page and
its context version are read under the same room lock. Empty rooms are
materialized on the bot only after an authorized history/answer request.

## Processing and recovery

Room transactions take a PostgreSQL advisory transaction lock, including when
the room does not yet exist. Each task owns a separate SQLAlchemy session. User
message and processing reservation commit before inference; no long database
transaction spans inference. Answer, assistant message, context delta/version
and any checkpoint commit atomically. Cache writes happen after that commit.

States are PROCESSING, COMPLETED, FAILED, CANCELLED and INTERRUPTED. Same ID and
payload replay the saved outcome; changed payload returns 409. Context version
conflicts also return 409. Only the latest failed/cancelled/interrupted turn can
be retried, with unchanged identity/payload and a new attempt number. Inference
uses one single-process gate for conversations and standalone requests.
A busy rejection returns 429 before a new turn is stored. Stored duplicate
results and turn/history reads and purge remain available during inference.

Disconnects cancel and drain owned work. A timeout or lost HTTP response does
not establish whether a transaction committed: inspect the turn before retry.
On startup, enabled conversation storage marks old PROCESSING rows INTERRUPTED,
without rerunning them. Stop/drain the old process before starting exactly one
replacement; concurrent bot instances are not a supported deployment.

## Context and evidence

`src/conversation` uses deterministic rules and keeps one active request per
room. Aspect follow-ups replace the requested aspects unless the user asks to
add them. Platform, leave type and duration use the routing registry. An explicit
correction replaces its value and rechecks the route. Changing between days and
sessions clears the other unit. Competing values prompt for a choice without
applying the update. The bot checks requested aspects before offering document
or platform choices, then reads the declared references for up to five candidate
scopes in one eligible snapshot. One viable scope gets a labeled answer with the
remaining limitations. Zero viable scopes get an evidence limitation. With
several, the prompt lists the viable scopes and marks partial coverage.
A routing-slot prompt still requires the requested input. Leave requests resolve
the subtype before asking which platform the user uses.

An unlabelled `YYYY-MM-DD` or `DD/MM/YYYY` date is accepted when exactly one
date slot is pending. Only the leave dates slot accepts a date range. Other
contextual values use labels, and relative dates require clarification. Inputs
remain user assertions and stay with their task. Changing the route clears a
previous source choice, and changing tasks discards task-bound inputs.
A user can name a related scope to switch while keeping the requested aspects.
Numbered selections apply only to the active task prompt.

Short unrecognized replies and acknowledgements preserve the active request.
Cancellation and self-contained new requests end it. A numbered reference to a
previous answer can select a displayed, uniquely mapped document claim. The
bot reads its declared evidence again and asks for clarification when the
reference is ambiguous. Self-work field follow-ups may
reuse self-reference after an applied or partial employee context. Unsupported
subjects or eligibility decisions do not inherit permission to read HRM data.
Original messages are stored separately. The resulting plan drives contextual
reads, evidence selection and rendering.

Completed turns carry a context delta snapshot. Every eighth completion saves a
checkpoint. A cache miss reads the newest completed snapshot after the
checkpoint or uses the checkpoint if there is none. Failed turns do not advance
context. Reducer schema 9 stores bounded task, related scope, prompt and displayed
document claim references alongside user slot provenance. It reads compatible
schema 7/8 state and expires the numbering of their old task prompts. After a catalog change, active tasks can be reused when their bindings
still match. Numbered choices from an old prompt must be confirmed against a
fresh prompt. Completed messages and answers remain readable through the
schema 2 API, with no table migration.

The app-owned memory helper uses namespace `conversation-context-v2`, owner/room
keys, its existing 300-second TTL and a 48 KiB context payload budget. One DB
transaction checks room ownership and version and uses the matching cache entry
or reads the latest snapshot/checkpoint. Stale/missing entries rebuild; cache
failures do not undo a committed answer. Current document eligibility/revisions
and requested HRM fields are read again for new answers. Historical citations
and user assertions do not authorize present business conclusions.

Structured logs record history direction, first-page status, item count,
item bytes and read latency. Context logs report cache/snapshot/checkpoint
source, read latency, rejected snapshot reasons and the startup count of
interrupted turns.

When a chosen task has no section for the requested aspect, the reply confirms
the topic and returns `answer_section_unavailable`. A declared reference that
cannot support any claim returns `answer_reference_unavailable`. A request for
maternity-benefit paperwork stays in the maternity-policy task. Its catalog
currently supplies an overview, so it cannot list the paperwork. Ambiguous
childbirth paperwork can receive the supported company-benefit answer with an
explicit limit on maternity-insurance paperwork. The release preserves schema 2
and the existing optional clarification fields.

An unmatched single-aspect document question with an authored topic cue can
receive up to three suggested catalog topics. Discovery uses one bounded
retrieval and at most one batch of exact live references. The prompt contains
labels only. A choice binds to the current prompt and context version, and the
bot rechecks citations before rendering general guidance. Explicit document
lookup, including phrasing such as “cho mình xem tài liệu”, uses the excerpt
path. Discovery logs candidate outcomes and elapsed time without question text.

## Deletion and verification

Backend hides a room and durably queues purge before returning 202. Purge removes
turns, messages, checkpoint, owner reference and cached context. Only a room UUID
and empty deleted state remain as a tombstone, without expiry. Pending/late
attempts cannot commit or recreate that room. Backend deletes its room metadata
after acknowledgement and retries purge across restarts. There is no read-only
closed room or automatic age-based retention in this version.

Unit/HTTP tests use fictional sources. The optional PostgreSQL fixture takes its
target from `TEST_CONVERSATION_DATABASE_URL` in the pytest process environment.
It checks [database isolation](../README.md#testing), creates a UUID-named schema,
applies the chat migration and drops its schema in cleanup. The process restart
test starts fresh conversation services against that schema to check snapshot
and checkpoint restoration, a killed inference and explicit retry. Fixture
inference uses fictional data. The suite skips without a test URL.

```powershell
python -m pytest src/tests/test_conversation.py -q
python -m pytest src/tests/test_conversation_integration.py -m integration -q
```

Deploy schema then bot then Backend, initially disabled. Check readiness and a
designated synthetic end-to-end conversation, timeout lookup, context rebuild
and deletion before enabling users. For the choice UI, deploy the compatible
bot, Backend and Portal in that order. After accepting bound choices, keep the
bot and Backend compatible with stored choice payloads if the Portal is rolled
back. Keep the purge worker and credentials operational. See [current verification](../README.md#current-verification-status).

For the natural Vietnamese routing release, deploy the bot and its generated
catalogs together. In an authenticated staging room, send both company-vehicle
question variants and confirm that the reply cites HCNS-QT02 and gives the
contact and approval steps without a clarification menu. In the same room,
ask what paperwork is needed, then ask explicitly about booking a company
vehicle in Workit. Confirm that each reply cites the corresponding source.
Ask about travel-expense settlement with both verified copies published and
confirm that one source-backed answer is returned. These checks exercise the
conversation payload and current published evidence. Roll back the bot and
catalogs as a set.

For seal and topic discovery verification, confirm that the general seal
question cites HCNS-QC01 and that a request for a hanging or spanning seal stays
on the respective procedure. In the same room, ask what paperwork is needed and
then change to an unrelated subject. Submit an unmatched document question about
disposing of old office assets, check that the suggested topic has no answer
claims yet, then confirm it and check its citations. Use a disposable staging
fixture to confirm that withdrawing the selected source prevents an answer.

For the priority HRM topics, use an authenticated staging room with the published
travel policy. Ask broadly about travel expenses and check the grounded topics
for allowances, advances and settlement. Select settlement, ask what supporting
documents are needed, and verify the citation to Article 8.3. Ask about personally
paid client expenses and verify that the bot asks which type of expense applies.
Then switch to company vehicles and missed clock-ins in the same room, and check
that childbirth benefits ask for the applicable situation when it is ambiguous.
In disposable staging data, withdraw a referenced publication and verify that a
previously offered option requires current evidence. After a catalog refresh,
verify that completed history is readable and old prompt IDs cannot activate
outdated choices. Observe response reasons and discovery latency by family.

For labor rules, attendance and overtime, deploy the bot with its matching
answer and retrieval catalogs. In an authenticated staging room, exercise a
general labor rule, a missed clock-in and an exact overtime threshold. Check
that each claim links to the current source. Follow up with a short question
about the paperwork, then change to another topic. Ask about today's personal
timesheet to confirm that the reply directs the employee to their own Workit
record. In disposable staging data, withdraw the source and exercise the same
follow-up again. The reply must reflect source unavailability. Roll back the
bot and its catalogs together.

## Contextual answer revision

All answer/retry bodies require a positive hrm_user_id. Results use the unified
schema 2 evidence graph. Cache namespace v2 stores the derived request described
under [context and evidence](#context-and-evidence). User assertions remain
unverified and personnel reads are fresh. See
[contextual answers and compatible development rollout](contextual-answers.md).
