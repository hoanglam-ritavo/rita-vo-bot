# Structured evidence v3

This extension retains the four-file package and v2 readers/publication. All three JSON files must use the same integer schema version. Metadata and extraction fields remain unchanged. Source revisions and snapshots are unchanged by chunk conversion. Validation checks structure/provenance, not whether an author's interpretation is correct.

## Authoring and conversion

Preview one selected package, then apply explicitly:

```powershell
python -m scripts.convert_structured_package --package chunks/<document_id>
python -m scripts.convert_structured_package --package chunks/<document_id> --apply
```

Conversion writes a scaffold with UUID chunk author IDs, default `detail` roles and empty structures. It stages under `.tmp/`, retains backups there and checks for concurrent edits. An agent must author roles, scopes, memberships and values against the source before claiming structured coverage. Keep IDs stable on subsequent edits. No batch discovery, automatic semantic inference or publication occurs.

V3 `chunks.json` retains `chunks`, `excluded`, `warning_decisions` and adds `scopes`, `items`, `values`. Undefined fields are rejected. Each chunk adds `id` and `role` to the v2 content/source_refs/optional notes. Roles: `definition`, `rule`, `procedure`, `inventory`, `detail`, `table`, `administrative`. Author IDs use 1–100 ASCII letters, digits, dots, underscores or hyphens. Database row UUIDs remain distinct from author IDs.

| Collection | Required fields |
| --- | --- |
| scopes | `id`, `name`, `description`, `item_ids`, `chunk_ids`, `coverage`, `metric`, `operations`, `comparison`, `source_refs` |
| items | `id`, `label`, `name`, `parent_id`, `order`, `chunk_ids`, `source_refs` |
| values | `id`, `item_id`, `name`, `type`, `value`, `unit`, `basis`, `original`, `conditions`, `ambiguous`, `source_refs` |

Coverage is `complete`, `partial` or `unknown`. Membership and chunk links must exist and be unique. A scope cannot contain both an ancestor and its descendant: author separate scopes for counting main items and subitems. Item order is a nonnegative integer; parents must form an acyclic graph. `metric` is a nullable exact value-name selector. Scope `operations` explicitly permits `list`, `count`, `procedure`, `compare`, `min`, `max`; an inventory cannot be used as a procedure merely because its items are ordered. Link required eligibility/rule chunks through the relevant item's `chunk_ids`.

Value types are `text`, `boolean`, `decimal`, `date`. Decimal values are finite plain decimal strings, not floats or exponent notation; dates are ISO dates. Numeric values require both unit and basis. Preserve original source text and conditions. Do not normalize uncertain conditions into invented thresholds. `ambiguous` prevents a numeric conclusion when uncertainty affects the numeric measure. An uncertain age eligibility boundary can coexist with an unambiguous printed award amount, but cannot justify an individual entitlement.

`comparison: "same_basis"` requires identical unit/basis pairs. An explicitly authored `nominal_amount` scope compares printed amounts in one currency while preserving and displaying each original basis (person, case, child, occasion). It does not sum awards, convert units or equate entitlement bases. Use it only for a clearly described face-value comparison; cash and in-kind values must have different metrics and scopes. For other incompatible units/bases, request clarification.

Every structured object has nonempty source refs with real segment IDs and Python character offsets, exclusive end. Ensure overview chunks support scope claims, and item chunks include all relevant values and conditions. `complete` is an author's source-backed assertion, never an inference from chunk count or a validator guarantee.

## Storage and deployment

Run `python -m alembic upgrade head` explicitly before using the new code. Migration `0005_structured_evidence` adds chunk author ID/role and `evidence_scopes`, `evidence_items`, `evidence_values`. Keys include `ingestion_run`; typed decimal values also use PostgreSQL Numeric. JSONB retains original representation, links, conditions and provenance. V2 chunk fields remain null.

Validate → preflight with `--check-embeddings` → publication dry-run → publication using the existing CLIs. Structure participates in the publication fingerprint. Chunks, vectors and structure commit together; failure leaves the previous active run intact. No `--force` is needed merely for a structure change. Downgrade removes only the new structure/fields, preserving chunks, vectors and the shared vector extension. It requires subsequent v2-compatible code and cannot preserve v3 aggregate functionality.

## Online behavior and limits

`POST /api/v1/retrieve` accepts `text` and optional `top_k`, and returns ranked source chunks with provenance and timings. It searches vector candidates across all eligible chunk roles, optionally reranks them on CPU, and returns only passages accepted by a matching relevance profile. Missing assessment withholds candidates; see [filtering and fallback](online-reranking.md#retrieval-and-response-semantics). There is no LLM planner, scope selection, item expansion, generated prose, verification verdict or aggregate answer. Authored coverage is stored but is not a completeness claim made by the API.

The structured storage helpers remain available internally and covered by storage tests; they are not exposed as API operations. Stored `operations`, `comparison` and `ambiguous` fields describe source-backed semantics for possible future consumers. They do not cause the retrieval endpoint to count, compare or evaluate conditions. Top-K matches cannot establish a complete inventory or individual entitlement.

`scripts/try_retrieval.py` uses the public filtered contract and renders accepted matches with source locations, without starting the API or writing the database. Its default vector-only configuration cannot accept candidates and returns `relevance_unavailable` when candidates exist. Its compact display currently uses a generic no-match message rather than displaying the fallback reason; use the retrieval CLI JSON or HTTP response to inspect that reason. Use separate case files under `.tmp/` for evaluation rather than changing its user-owned `QUESTION`. See [retrieval evaluation](retrieval-evaluation.md).

## Verification

Run `python -m pytest -m 'not integration'`. Integration tests require a separate disposable PostgreSQL database, cover additive migrations, v2 publication, v3 atomic rollback and public visibility, and must never target the application database. Real-data validation/preflight/publication, storage verification and real-model retrieval quality are separate results. Synthetic multi-format/role tests do not establish real-document quality for unreviewed internal sources.
