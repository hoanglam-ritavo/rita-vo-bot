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

Provision the `bce4184a7f51` baseline and additive `c7d92e6a104b` guards explicitly; see [database initialization and boundaries](database-design.md#initialization-and-boundaries) for the vector prerequisite, fresh provisioning and data-preserving guard upgrade. The retired `0006_publication_baseline` is not available, and no automatic conversion of an older corpus is supplied. V2 package fields remain supported without fabricated author IDs/roles.

Scopes/items/values retain authored JSON. Ordered scope-item, scope-chunk and item-chunk links have same-run composite FKs; item parents also have same-run FKs. Decimal projections use PostgreSQL Numeric and must match authored values. Shared readers verify projections and ordered memberships before skip, vector reuse or structured expansion. Semantic cycles, source fidelity and coverage remain package-validator/author responsibilities.

Validate, preflight with `--check-embeddings`, publication dry-run, then publication using the existing CLIs. Chunks, vectors, evidence links, metadata audit and the attempt receipt commit together; failure preserves the prior active publication. Structure participates in fingerprints. No `--force` is needed merely for a structure change. Neither schema provisioning nor successful publication establishes aggregate correctness or calibration acceptance.


## Online behavior and limits

The separate `/api/v1/answer` route now uses a source-bound catalog and deterministic
templates; it does not activate the stored scope/item expansion helpers described
here. No package/schema/publication change is required. See the
[current answer contract](answer-evaluation.md#semantic-template-answer-api).

`POST /api/v1/retrieve` accepts `text`, a trusted Backend-derived `hrm_user_id` and
optional `top_k`. It requires [HTTP service authentication](http-service-auth.md)
and returns ranked source chunks with provenance and timings. Vector retrieval
searches across eligible chunk roles, enforces access and optionally reranks on
CPU. Matches are `unassessed`. Empty document outcomes distinguish `no_candidates`
and `no_eligible_documents`. Relevance profiles and their filter settings are
retired. Authored coverage is stored for internal readers. See
[retrieval semantics](online-reranking.md#retrieval-and-response-semantics).

The structured storage helpers remain available internally and covered by storage tests; they are not exposed as API operations. Stored `operations`, `comparison` and `ambiguous` fields describe source-backed semantics for possible future consumers. They do not cause the retrieval endpoint to count, compare or evaluate conditions. Top-K matches cannot establish a complete inventory or individual entitlement.

`scripts/try_retrieval.py` displays unassessed candidates with source locations
through the local Python retrieval service. It runs without an HTTP server or
database writes. Use retrieval CLI JSON or HTTP to inspect the exact empty
fallback reason. Keep evaluation cases in separate files under `.tmp/`. See
[retrieval evaluation](retrieval-evaluation.md).

## Verification

Run `python -m pytest -m 'not integration'`. Integration tests require a separate disposable PostgreSQL database, cover additive migrations, v2 publication, v3 atomic rollback and public visibility, and must never target the application database. Real-data validation/preflight/publication, storage verification and real-model retrieval quality are separate results. Synthetic multi-format/role tests do not establish real-document quality for unreviewed internal sources.
