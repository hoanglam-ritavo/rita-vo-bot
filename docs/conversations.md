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

| Route | Input / purpose |
| --- | --- |
| `POST /api/v1/answer` | Complete conversation request above, or stateless body with required hrm_user_id |
| `GET /api/v1/conversations/{room_id}/turns/{message_id}` | `user_id` query; inspect without inference |
| `POST /api/v1/conversations/{room_id}/turns/{message_id}/retry` | `{user_id, hrm_user_id}`; explicit retry |
| `GET /api/v1/conversations/{room_id}/messages` | `user_id`, `after` sequence (default 0), `limit` (20, max 50) |
| `POST /api/v1/conversations/{room_id}/purge` | `{user_id}`; idempotent permanent purge |

Message pages are turn records with the original `text`, stored result and turn
state, plus `next_cursor` and `context_version`. They do not regenerate answers.
Page serialization is bounded below 2 MiB; use the returned cursor. Empty rooms
are materialized on the bot only after an authorized history/answer request.

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
shares the existing single-process busy gate; other new work receives 429.
Turn/history reads and purge remain available during inference.

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
applying the update. Task selection is limited to the options offered by a task
clarification. A routing-slot prompt still requires the requested slot.

An unlabelled `YYYY-MM-DD` or `DD/MM/YYYY` date is accepted when exactly one
date slot is pending. Only the leave dates slot accepts a date range. Other
contextual values use labels, and relative dates require clarification. Inputs
remain user assertions and stay with their task. Changing the route clears a
previous source choice, and changing tasks discards task-bound inputs.

Unrecognized replies to a pending request keep its clarification. Cancellation
and recognized topic changes end the request. Self-work field follow-ups may
reuse self-reference after an applied or partial employee context. Unsupported
subjects or eligibility decisions do not inherit permission to read HRM data.
Original messages are stored separately. The resulting plan drives contextual
reads, evidence selection and rendering.

Completed turns carry a context delta snapshot. Every eighth completion saves a
checkpoint; a cache miss rebuilds from that checkpoint and newer completed
deltas. Failed turns do not advance context. Context retains up to four recent
user texts, task/candidate/aspect state, reducer/catalog identity and bounded
evidence references and user-text slot origins. It does not store assistant prose
or employee facts as authoritative context. Reducer schema 7 validates the
shape, byte budget and catalog identity before inheritance. Older reducers,
changed fingerprints and invalid payloads require a complete restatement for
dependent replies. Independent questions still proceed. Completed messages and
answers stay readable through the existing schema 2 API, with no table migration.

The app-owned memory helper uses namespace `conversation-context-v2`, owner/room
keys, its existing 300-second TTL and a 48 KiB context payload budget. A small DB
version read validates every cache hit. Stale/missing entries rebuild; cache
failures do not undo a committed answer. Current document eligibility/revisions
and requested HRM fields are read again for new answers. Historical citations
and user assertions do not authorize present business conclusions.

## Deletion and verification

Backend hides a room and durably queues purge before returning 202. Purge removes
turns, messages, checkpoint, owner reference and cached context. Only a room UUID
and empty deleted state remain as a tombstone, without expiry. Pending/late
attempts cannot commit or recreate that room. Backend deletes its room metadata
after acknowledgement and retries purge across restarts. There is no read-only
closed room or automatic age-based retention in this version.

Unit/HTTP tests use fictional sources. The optional PostgreSQL fixture uses only
`TEST_CONVERSATION_DATABASE_URL`, creates a UUID-named schema, applies the chat
migration and drops its schema in cleanup. It never loads application `.env`.
Missing configuration is skipped, not live database verification.

```powershell
python -m pytest src/tests/test_conversation.py -q
python -m pytest src/tests/test_conversation_integration.py -m integration -q
```

Deploy schema then bot then Backend, initially disabled. Check readiness and a
designated synthetic end-to-end conversation, timeout lookup, context rebuild
and deletion before enabling users. Roll back by disabling new conversation
entrypoints while keeping purge credentials and worker operational; do not drop
tables or tombstones. See [current verification](../README.md#current-verification-status).

## Contextual answer revision

All answer/retry bodies require a positive hrm_user_id. Results use the unified
schema 2 evidence graph. Cache namespace v2 stores the derived request described
under [context and evidence](#context-and-evidence). User assertions remain
unverified and personnel reads are fresh. See
[contextual answers and compatible development rollout](contextual-answers.md).
