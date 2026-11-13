# Agent chunk preparation and publication

The workflow is **agent-authored package -> automatic validation -> local embedding -> PostgreSQL**. There is no approval command, reviewer identity or review/approved distinction in the new contract. Validation checks consistency, not transcription accuracy, completeness of the original reading or business correctness.

## Workspace and identity

Original documents remain in `assets/`. Each source path has one current package, tracked in Git:

```text
chunks/<document_id>/
    metadata.json
    extraction.json
    chunks.json
    source.<extension>
```

The snapshot is named `source.pdf`, `source.txt`, etc. A complete package has exactly these four files. Initialization creates metadata and snapshot; missing extraction/chunks means incomplete input, not a review state. No local catalog, manifest, initial draft, payload, approval copy, cache or report is maintained. Source identity appears only in metadata; the directory name must match document_id.

`package_paths` scans only this layout. Old `review/`, `approved/` and hidden staging directories are excluded. Historical `indexing_chunks/` and `indexing_workspace/` are never scanned, merged or removed. Initialization refuses to create duplicate identities for sources represented only in the current legacy review/approved workspace; explicitly convert a selected legacy package first.

Edit extraction/chunks and non-identity metadata in place for the same source revision. Do not replace snapshot bytes or change identity/fingerprint fields to disguise source changes. Use `revise` for changed bytes; it requires `--supersedes-current`, creates a new revision of the same document at the same package path, and resets lifecycle/indexing eligibility and evidence notes. Old extraction/chunks are removed from the current package. It records the previous revision and its declared supersedes ancestors, dropping other relations. Source renaming is unsupported. Git retains committed authoring history; the database retains previous indexed chunks. Local displaced packages remain in ignored `.tmp/package-backups/` for recovery, not as publication candidates.

## Commands

From the repository root, with the virtual environment active:

```powershell
python -m src.cli.prepare_documents init --source policies/example.txt
# Or initialize all current sources:
python -m src.cli.prepare_documents init --all
# Have an agent author extraction.json/chunks.json and complete metadata.
python -m src.cli.prepare_documents validate --package chunks/<document_id>
python -m src.cli.publish_documents --package chunks/<document_id> --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --dry-run
python -m src.cli.publish_documents --package chunks/<document_id> --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit>
# Only when the source bytes change:
python -m src.cli.prepare_documents revise --package chunks/<document_id> --source policies/example.txt --supersedes-current
```

Replace placeholders before execution; the source path is relative to `assets/`. Package/model paths use the caller's working directory. Defaults remain repository anchored. `--package` can repeat for publication; `--all` selects current-layout packages only. Workspace identity/layout errors reject the scan; incomplete extraction/chunks are reported per package. It does not select a latest revision by filename or time. Relations order batches; cycles and duplicate revision selections are rejected. Each revision is published independently, not in a batch-wide transaction. An independent publication failure does not prevent other eligible independent packages from being processed. If the remaining dependency graph contains a cycle, the cyclic remainder is rejected but independent packages already ordered by the batch still run. A nonzero batch exit code therefore does not imply that nothing was published. Dry-run checks against the current database; a relation target selected in that same dry-run is still missing if it has never been published.

Commands support `--json` for structured stdout. Exit codes: 0 success/skips, 1 rejected input, 2 operational failure or retired command. Diagnostics never print source text or credentials. Initialization, revision creation and validation need no model/database. Publication including dry-run reads PostgreSQL. Dry-run checks model files but does not load the tokenizer, generate vectors or prove chunk token limits.

`approve`, `extract`, `--review`, `--reviewer`, `--content-only` and publication `--approved` return retirement guidance without filesystem/model/database work. Other historical ingestion/catalog/PDF commands and destructive workspace migration are retired too. Python extraction/chunking runtimes remain retired. Supported complete packages are PDF, XLSX, CSV, MD and TXT; DOC/DOCX may be initialized with metadata/snapshot but cannot be published.

## Technical preflight

Use preflight after agent authoring and before publication to check the actual local tokenizer/model. Unlike publication, it does not connect to PostgreSQL or require include/active metadata. Package schema, source snapshot, references, coverage and warning decisions must still be valid. Existing metadata schema rules still apply: for example, an include record with missing access groups is invalid, even for preflight.

```powershell
python -m pip install -r requirements-example.txt
python -m src.cli.preflight_documents --package chunks/<document_id> --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --json
# Optional: also compute all vectors in memory; never write them to the database:
python -m src.cli.preflight_documents --package chunks/<document_id> --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --check-embeddings --json
```

Replace placeholders before execution. One package is accepted per invocation. The default loads the supplied local model but does not call encode. Counts include `passage: ` and special tokens, without truncation, using the minimum of the model and tokenizer limits. Every chunk is reported with its zero-based `chunk_index`, `token_count` and `passed`/`exceeded` status. All oversized chunks are reported; any oversized chunk prevents optional embedding. No automatic splitting or content rewriting occurs.

With `--check-embeddings`, preflight generates all vectors using batch size 16 and normalized embeddings, checks count, 384 dimensions and finite values, then discards the vectors. The report includes document/revision IDs, model name and declared revision, package/model fingerprints, token limit, per-chunk results and `embedding_check` (`not_run`, `skipped`, `passed`, or `failed`). Metadata blockers are reported separately; `database_checked` is always false. Neither mode resolves database conflicts or relation targets. A valid hold/unknown package can pass technical checks while remaining ineligible for publication.

Package reads may create OS lock files under ignored `.tmp/package-locks/`, requiring a writable `.tmp/` directory even for validation and preflight. Reports go only to stdout (structured JSON with `--json`); no package files or reports are written. Runtime diagnostics are suppressed in favor of safe error codes. Exit codes are 0 for successful requested technical checks, 1 for rejected package/token/vector data or detected input changes, and 2 for operational failures such as a missing model/runtime. Package/model fingerprints are rechecked after token/embedding checks; changed inputs invalidate the result. No model download or hosted service is used. The supplied model commit and file hashes do not authenticate model identity.

Workflow: author package, run preflight, establish publication eligibility from verified facts, then publish. Preflight is not approval, transcription verification or a retrieval evaluation. Publication repeats its checks even after a successful preflight. Publication `--dry-run` remains a separate database-aware check that does not load the tokenizer or create embeddings. Synthetic tests exercise these contracts; testing a real package/model requires explicitly supplied local model files and is reported separately.

## Strict schema v2

All three JSON files have integer `schema_version: 2`. Old schemas and undefined fields are rejected; arbitrary dictionaries are permitted only for configuration, parser metadata, segment details and warning locations. Use `src.ingestion.document_packages` and `src.catalog.validation` as schema authorities.

### Metadata

Python initialization supplies all fields, IDs, source hash/size and an unchanged snapshot. Preserve its identity fields: `document_id`, `revision_id`, `source_path`, `sha256`, `size_bytes`, `extension`.

Other fields are `title`, `category`, `document_type`, `owner`, `applicability`, `version_label`, `effective_from`, `effective_to`, `lifecycle_status`, `access_groups`, `indexing_decision`, `evidence_notes`, `relations`. Text/date fields may be null. Notes and access groups are string arrays. Relations contain exactly `type` and target `revision_id`; accepted types are `duplicate_of`, `supplements`, `supersedes`, `form_for`. Targets are checked in PostgreSQL at publication.

Defaults are hold/unknown, empty groups/notes and nullable business metadata. Publication needs `indexing_decision: "include"`, `lifecycle_status: "active"`, nonempty applicability/access groups, and dates that include today when provided. Date ranges must be ordered. Owner and evidence notes are optional. Never invent metadata to pass validation. Review status and reviewer fields do not exist. Access groups describe intended rights; future backend authorization remains a separate requirement.

### Extraction

```json
{
  "schema_version": 2,
  "status": "complete",
  "configuration": {},
  "parser_metadata": {"method": "synthetic-agent-transcription"},
  "segments": [{
    "segment_id": "s1",
    "raw": "Employees submit the form before leave.",
    "text": "Employees submit the form before leave.",
    "location": {"kind": "text", "line_start": 1, "line_end": 1},
    "extraction_method": "native",
    "details": {}
  }],
  "warnings": []
}
```

Segment IDs are nonempty unique strings. Retain raw transcription and the text used for reference offsets. Locations are one-based: PDF uses `kind: "pdf"`, `page_start`, `page_end`; TXT/MD use `kind: "text"`, `line_start`, `line_end`; CSV uses `kind: "csv"` with line ranges; XLSX uses `kind: "xlsx"`, `sheet`, `row`, `column_start`, `column_end`, `header_row`. Ranges must be positive and ordered. Kind must match the source extension.

Each warning has `warning_id`, `code`, `location`. IDs are unique nonempty strings. Record blank, illegible or uncertain source areas. `native` means native text extraction; reading page images uses `ocr`, with the actual method in parser metadata. Do not invent an OCR engine name. No extraction identity or agent-generated fingerprint is needed.

### Chunks

```json
{
  "schema_version": 2,
  "chunks": [{
    "content": "Employees submit the form before leave.",
    "source_refs": [{"segment_id": "s1", "start": 0, "end": 39}],
    "notes": "Synthetic example"
  }],
  "excluded": [],
  "warning_decisions": {}
}
```

`notes` is optional; other shown fields are required. Content and references must be nonempty. Offsets are Python character offsets into segment `text`, with exclusive `end`. Chunk IDs are unnecessary; array order becomes `chunk_index`, and database rows receive generated IDs. Multiple references and overlap are allowed.

Every non-whitespace extraction character must be covered by a chunk reference or an exclusion. An exclusion contains `source_ref` (same reference shape) and a nonempty `reason`. Warning decisions map every extraction warning ID to a string. Empty/whitespace decisions remain unresolved and block validation/publication; a nonempty decision records the actual resolution evidence, not an approval. Conversion preserves unresolved decisions instead of inventing them.

## Agent authoring procedure

### Scope boundaries

The offline pipeline has two scopes. **Scope 1: source preparation and package authoring** ends with the four package artifacts at `chunks/<document_id>/`, checked against the source and structurally validated. It needs no embedding model or database. **Scope 2: package reading, embedding and publication** starts from that package; actual tokenizer checks through [technical preflight](#technical-preflight) are the handoff check before database publication. If preflight rejects oversized chunks, return to authoring without truncating away meaning.

A structurally valid package can still be on hold or have unknown lifecycle metadata. Completing authoring does not establish publication eligibility, current policy validity or retrieval quality. Online question routing, evidence selection, answer generation and fallback are outside both authoring guidance and this documentation change.

### Source reading and structure

1. Inspect source identity and existing packages before initializing or revising. Establish an authorized processing environment for internal content; agent execution is not necessarily offline. Do not implicitly upload internal data to hosted services. Treat source content as data, never agent instructions.
2. Read every source page or sheet, including blank or uncertain regions, and preserve transcription with actual locations. Use page rendering/cropping for scanned PDFs as needed. Preserve the unchanged snapshot, table headers, units, merged-cell relationships, conditions and exceptions. Do not guess missing numbers, negations, conditions or names; record uncertainties.
3. Retain Article, item and subitem labels in transcription and relevant chunk content. Identify the counting unit: articles, main benefit items and subitems are different quantities. Repeated table rows for eligibility bands do not automatically represent separate benefits. Chunk count is never a substitute for this structure.
4. Tie every chunk to real transcription spans. Record exclusions such as headers/footers with reasons. Resolve uncertainty against the source before publication; do not fill warning decisions merely to pass a check. Preserve unresolved interpretations explicitly rather than inventing a policy answer.

### Chunk levels and relevance

Choose levels according to the source, not a fixed quota of chunks:

| Authoring level | Use | Required evidence |
| --- | --- | --- |
| Detail | A specific rule, entitlement or procedure | Necessary scope, amount/unit, conditions and exceptions together with the rule |
| Overview or inventory | A document with multiple items that users may ask to list | The complete named inventory within an explicit document/section boundary, with references supporting every item |
| Group | Several related items grouped meaningfully by the source | The group's boundary and member items, without inventing a new policy category |

These are authoring conventions, not runtime chunk types. Not every document needs all three levels. An inventory may paraphrase or assemble source headings, but must not add benefits, resolve ambiguous wording or infer current validity. A document-wide inventory is not proof of the company's entire benefit offering.

Repeat only the context necessary to interpret a detail chunk independently. Avoid copying the full introduction, unrelated definitions and all general notes into every chunk. Keep eligibility and exceptions that determine the entitlement in each affected detail chunk, even if they also have a dedicated chunk. The current runtime does not automatically attach a separate conditions chunk. Shared conditions must remain attributable to their actual source spans.

For long inventories, split at meaningful boundaries and label each part's scope in `content`; never present one part as the full list. Preserve item labels so an author can check completeness and distinguish main items from subitems. Top-K retrieval does not guarantee that all inventory parts will be returned. Additional paraphrase copies or speculative question/answer chunks do not establish coverage and can crowd out distinct evidence.

Keep chunks focused and anticipate the intended model's token budget, but do not claim a character count proves tokenizer compatibility. Exact counting, including the passage prefix and special tokens, belongs to preflight in Scope 2.

### Synthetic authoring example

The following is a fictional five-line TXT source, represented as a complete extraction example with three segments. Credits are synthetic units. Metadata and the unchanged TXT snapshot are still required for a complete package.

```json
{
  "schema_version": 2,
  "status": "complete",
  "configuration": {},
  "parser_metadata": {"method": "synthetic-agent-transcription"},
  "segments": [
    {
      "segment_id": "scope",
      "raw": "Article 1. Benefits apply to permanent employees.",
      "text": "Article 1. Benefits apply to permanent employees.",
      "location": {"kind": "text", "line_start": 1, "line_end": 1},
      "extraction_method": "native",
      "details": {}
    },
    {
      "segment_id": "learning",
      "raw": "1. Learning support\n1.1 Books: 20 credits per year.\n1.2 Courses: 40 credits per year.",
      "text": "1. Learning support\n1.1 Books: 20 credits per year.\n1.2 Courses: 40 credits per year.",
      "location": {"kind": "text", "line_start": 2, "line_end": 4},
      "extraction_method": "native",
      "details": {}
    },
    {
      "segment_id": "travel",
      "raw": "2. Travel support: 10 credits per month.",
      "text": "2. Travel support: 10 credits per month.",
      "location": {"kind": "text", "line_start": 5, "line_end": 5},
      "extraction_method": "native",
      "details": {}
    }
  ],
  "warnings": []
}
```

The corresponding `chunks.json` contains an inventory, a source-defined learning group retaining its subitems, and a travel detail. All offsets are exclusive-end Python character offsets into the segment `text`.

```json
{
  "schema_version": 2,
  "chunks": [
    {
      "content": "Article 1 benefit inventory for permanent employees. The complete list of main benefit items in this source is: 1. Learning support (1.1 Books; 1.2 Courses); 2. Travel support.",
      "source_refs": [
        {"segment_id": "scope", "start": 0, "end": 49},
        {"segment_id": "learning", "start": 0, "end": 85},
        {"segment_id": "travel", "start": 0, "end": 40}
      ],
      "notes": "Inventory for this synthetic source only; main items and subitems remain distinct."
    },
    {
      "content": "Article 1. Benefits apply to permanent employees.\n1. Learning support\n1.1 Books: 20 credits per year.\n1.2 Courses: 40 credits per year.",
      "source_refs": [
        {"segment_id": "scope", "start": 0, "end": 49},
        {"segment_id": "learning", "start": 0, "end": 85}
      ],
      "notes": "Source-defined group with the eligibility shared by both subitems."
    },
    {
      "content": "Article 1. Benefits apply to permanent employees.\n2. Travel support: 10 credits per month.",
      "source_refs": [
        {"segment_id": "scope", "start": 0, "end": 49},
        {"segment_id": "travel", "start": 0, "end": 40}
      ],
      "notes": "Detail with necessary eligibility and payment unit."
    }
  ],
  "excluded": [],
  "warning_decisions": {}
}
```

Manual checks for this example: a travel question has an amount, unit and eligibility; a list question has both main items; a count question distinguishes two main items from the two subitems under learning and from the three chunks. Both learning subitems retain the shared permanent-employee condition. There are no warnings in this fictional source. If an actual source instead says an ambiguous phrase such as "young children", preserve that wording and record the uncertainty; do not invent an age boundary. If it remains unresolved, record that honestly and keep the package from publication rather than fabricate a warning resolution.

### Authoring completion checklist

- **Manual source fidelity:** compare transcription and chunks with every source page/sheet; check numbers, units, negations, table relationships, applicability and exceptions.
- **Manual coverage:** reconcile the inventory against source items and subitems. Confirm every summary claim has supporting spans and every detail keeps necessary context. References covering all characters do not prove that `content` represents all their meaning.
- **Manual relevance:** remove unnecessary repetition and unrelated context without removing conditions needed to understand a rule. Do not omit a substantive rule by calling it an exclusion merely to make validation pass.
- **Structural validation:** run the existing package validator to check schema, identity/snapshot consistency, valid reference ranges, character coverage through references/exclusions and warning decisions. It does not verify transcription accuracy, semantic completeness, count correctness or business approval.
- **Handoff:** retain factual metadata, exclusions and evidence notes in the existing fields; leave unresolved publication blockers explicit. Hand the four artifacts to Scope 2 for real-model preflight and eligibility/publication checks. No approval step or extra package report is introduced.

### Future extensions — not implemented

Schema v2 accepts only `content`, `source_refs` and optional `notes` on each chunk. `notes` can explain authoring intent to a reader; it is not embedded, persisted as a chunk field or used for routing by the current pipeline. Labels and hierarchy written in `content` are text, not machine-enforced relationships. `chunk_index` is array position, not a stable semantic item identifier.

Future work may introduce explicit chunk types, stable source-item identifiers, parent/child relationships and machine-readable coverage information. This requires a separate schema/runtime compatibility design; no field names, new schema version, migration or database representation are defined here. Do not add `chunk_type`, `parent_id` or custom item/link fields to v2 JSON, or repurpose document-level `metadata.relations` as chunk links.

Better source preparation alone does not establish accurate retrieval, exhaustive counting or production-ready RAG. Online retrieval still needs evidence selection and coverage checks, and real-model evaluation remains separate from authoring and structural validation.

## Scope 2: package reading, embedding and publication

Scope 2 consumes the four-file package produced by [Scope 1](#agent-authoring-procedure) and ends with a successful PostgreSQL publication or an explicit skip/rejection/failure result. It does not parse the original document again, rewrite chunks, infer missing metadata or implement Online answering. A successful publication establishes storage consistency, not semantic correctness or sufficient evidence for a question.

### Operational sequence and check boundaries

Use this recommended sequence: receive the package, validate it, run local-model preflight, establish publication eligibility from verified facts, perform database-aware dry-run, publish explicitly, then verify the result. Preflight is an independent diagnostic; publication neither requires nor stores a prior preflight-passed state and repeats its own checks. Dry-run is not a reservation of database state for a later write.

| Operation | Model work | Database work | What it establishes |
| --- | --- | --- | --- |
| Package validation | None | None | Package structure, snapshot identity and reference consistency |
| Default preflight | Load model/tokenizer and count tokens | None | Actual token compatibility; metadata blockers reported separately |
| Preflight with `--check-embeddings` | Also encode and check vectors in memory | None | Technical embedding checks; vectors are discarded |
| Publication `--dry-run` | Fingerprint supplied model files; no tokenizer or encode | Read eligibility-related database state, metadata conflicts and relation targets | Whether the selected package can proceed against the observed state, not token compatibility |
| Publication | Load/tokenize/encode unless the identical active configuration is skipped | Read state, then atomically write one revision's publication | Stored metadata, chunks, vectors and active-run state are consistent for that transaction |

Example sequence from the repository root, using an already prepared package and supplied local model:

```powershell
python -m src.cli.prepare_documents validate --package chunks/<document_id>
python -m src.cli.preflight_documents --package chunks/<document_id> --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --json
python -m src.cli.publish_documents --package chunks/<document_id> --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --dry-run --json
python -m src.cli.publish_documents --package chunks/<document_id> --model-path C:/models/multilingual-e5-small --model-revision <40-hex-commit> --json
```

Replace all placeholders. Review each result before continuing; these separate commands do not form an automatic stop-on-error workflow. Optional preflight embedding does not cache vectors for publication: publication encodes again. Model files stay local and externally supplied. See [technical preflight](#technical-preflight) and [commands](#commands) for flags, safe diagnostics and exit codes. CLI exit 0 includes skips, 1 indicates rejected input, and 2 indicates operational failure; inspect per-package results in a batch rather than interpreting the exit code as all-or-nothing success.

### Embedding contract and persisted data

The current embedding input is exactly `passage: ` followed by the entire `chunk.content`. There is no separate search-text field, automatic summary or heading injection. The supplied local model is configured as `intfloat/multilingual-e5-small`, with 384-dimensional output, batch size 16 and `normalize_embeddings=True`. Token counts include the prefix and special tokens, using the smaller of model/tokenizer limits. Oversized input is rejected without automatic splitting or truncation.

The runtime checks vector count, sequential chunk indices, 384 dimensions and finite numeric values. Normalization is requested from the encoder; the vector validator does not independently check unit norm or semantic quality. Publication also verifies that encoding did not change the validated chunk data other than adding embeddings.

| Package input | Runtime or PostgreSQL representation |
| --- | --- |
| Order in `chunks` | Zero-based `chunk_index`; each stored chunk receives a generated row ID and an ingestion `run_id` |
| `content` | Stored unchanged as chunk content; the passage prefix is added only for embedding |
| `source_refs` and referenced extraction segments | `source_location.spans` containing segment IDs, offsets, locations and extraction methods; chunk extraction method is `ocr` if any referenced span is OCR, otherwise `native` |
| Metadata identity and policy fields | Document/revision rows, access rules and resolved revision relations |
| Package/model bytes and embedding configuration | Fingerprints and parameters in ingestion run configuration; hashes detect changes, not authenticity |
| Optional chunk `notes` | Remain in package JSON; not embedded or stored as a chunk field, but changes affect the package fingerprint |
| Raw extraction, source snapshot, exclusions and warning decisions | Remain package evidence; the database does not store the complete package in every chunk. Exclusions/warning decisions also contribute to the content fingerprint |

Inventory/detail/group labels authored in Scope 1 remain plain content. Neither the database mapping nor embedding turns them into runtime types, stable source-item IDs or parent/child links. See [database metadata](catalog-database.md) for revision state and [future extensions](#future-extensions--not-implemented) for the current schema boundary.

### Publication outcomes and recovery decisions

Use the existing [consistency rules](#publication-and-consistency); no approval state or automatic retry is added.

| Situation | Current behavior and operator action |
| --- | --- |
| Identical active configuration | Returns `skipped` / `identical_active_run`; `--force` requests another run but bypasses no validation |
| Existing metadata differs | Rejects unless `--update-metadata` is intentionally selected; immutable identity checks still apply |
| Source bytes changed | Return to Scope 1 and explicit `revise`; do not modify identity fields to reuse the old revision |
| Chunk exceeds token limit | Return to authoring, preserve meaning and references, then repeat preflight |
| Package/model drift or concurrent database changes | Rejects; inspect the result and stabilize/recheck inputs before a new explicit attempt |
| Database transaction fails | Transaction rollback preserves the prior active state; pre-transaction failures do not create persisted failed-run records |
| Some packages in a batch fail | Independent revisions can still publish; inspect each result because there is no batch-wide rollback |
| A supersedes target is absent | Report `missing_supersedes` without placeholder rows; other missing relation targets reject publication |

A successful write returns `processed` / `published`; dry-run returns `processed` / `dry_run` unless the identical-run skip applies. Active runs belong to individual revisions. A superseded revision can retain an active historical run; current revision, lifecycle, effective dates, access and model compatibility still govern Online retrieval. Successful publication alone does not guarantee that the current public retrieval service will return the document.

### Post-publication verification and future work

When publication is performed as a separately authorized operation, verify its run/revision identity, stored chunk count, content, source spans and model configuration against the selected package. Distinguish publication invariants from database constraints: other writers are not guaranteed to enforce the same checks. Do not print private source text or vectors merely to record a verification result. This checklist adds no mandatory report file or new verification command.

Evaluate retrieval separately with specific questions, inventories, counts, multi-part questions and unsupported requests. Use the [package-pinned evaluator](../README.md#local-question-retrieval) with explicit `--package` and `--cases`. It checks the selected active run against package/model fingerprints and stored content, provenance and vectors before scoring. Expected evidence uses groups of exact package chunk indices; segment overlap alone is insufficient because an inventory may reference every detail without stating its amounts. Agent review still checks semantic sufficiency and unsupported cases. Reports distinguish detail/condition targets from aggregate coverage; neither a nonempty top-K response nor successful publication establishes answer correctness. The evaluator reports model-load and overall per-question times, not separate query embedding/database latency. Keep real-document cases and reports under `.tmp/`, preserve baseline results, and re-pin cases after package edits rather than evaluating stale runs.

Potential future Scope 2 changes include a separate embedding-text representation, persistence of explicit chunk types/source-item identities and parent/child or coverage data. These require their own schema, provenance and compatibility design. They are not implemented by this guidance, and no migration, new model, Redis layer or automatic rechunking is introduced. Successful publication and improved authoring do not establish exhaustive answers, fallback behavior or production-ready RAG.

## Publication and consistency

Publication fingerprints all package bytes and embeds a validated in-memory snapshot. Source drift in `assets/` does not invalidate the stored revision snapshot. Package files are rechecked after embedding and under the database publication lock; changes reject the run. Stop editing while publication runs. Hashes detect differences, not authenticity, and do not prevent edits that are reverted between checks.

Provide an existing local multilingual-e5-small model directory and declared 40-hex revision. Downloads and remote code are disabled. Local model files are fingerprinted; the commit string alone does not authenticate them. Actual embedding counts prefixed text plus special tokens using the model tokenizer and rejects oversize input instead of truncating. Vectors must be finite and 384-dimensional. Install `requirements-example.txt` only for this local embedding runtime.

Run configuration records source/content/metadata/package/model fingerprints and embedding parameters. Identical active configuration skips unless `--force`; metadata/notes/formatting changes to package bytes also change the package fingerprint. Existing database metadata conflicts require explicit `--update-metadata`. For an existing revision, publication rejects changes to `document_id`, source `sha256`, `size_bytes` or `extension` even with `--update-metadata`. The directory name must match document_id in metadata. The runtime currently treats `source_path` as updatable metadata: it can change with `--update-metadata` when no current-document/path collision exists. This only changes stored metadata; it does not rename files. Agents should still preserve identity fields during authoring and use `revise` when source bytes change. Model/package changes and concurrent publication/metadata changes are rejected. Metadata, access rules, relations, chunks and active-run activation commit atomically per revision; the prior active run survives failure. No pre-transaction failed-run log is persisted.

A replacement current revision needs an explicit `supersedes` relation to the prior current revision of the same document. Missing sources do not retire database records. Missing non-supersedes relation targets, incompatible duplicate hashes, source-path collisions and supersedes cycles are rejected. A missing supersedes target is retained as declared provenance but does not create a placeholder revision or relation row. Dry-run, successful publication and identical-run skips report `missing_supersedes`. Successful run configuration records `declared_supersedes` and `missing_supersedes` when supersedes is declared. Present targets must belong to the same document. Resolved target state is included in concurrency checks and rechecked under the publication lock. Metadata comparison uses only relations whose targets exist in that database.

## Legacy conversion and rollout

Stop ingestion before upgrading publication code and run migrations explicitly:

```powershell
python -m alembic upgrade head
python scripts/convert_chunk_package.py --package chunks/approved/<revision_id>/<approval_id>
python scripts/convert_chunk_package.py --package chunks/approved/<revision_id>/<approval_id> --apply
```

Preview does not modify package content (reads may create `.tmp/` lock files). Choose one current-layout review pending, review ready or approval package explicitly. The converter validates legacy schemas, identity and available hashes, preserves source/transcription/chunk bytes as content, references, exclusions, warning decisions and evidence notes, and writes the new layout through staging. Source snapshots remain byte-identical. Pending packages copy a matching original source if no snapshot exists; a missing original leaves metadata-only incomplete output. It never invents missing metadata or publishes content-only legacy packages without the new eligibility checks.

Conversion is not guaranteed for every package accepted by the legacy schema. The converter additionally applies v2 extraction/chunk validation, including required location fields and rejection of undefined fields. For example, a legacy XLSX location without `sheet`, or a location with an extra legacy field, is rejected with `segment_location_fields` during preview and apply; no target is finalized. The converter never guesses a sheet name or drops unsupported location information. If a package cannot be converted, author a v2 package separately using verified source facts and the intended document/revision identity; leave the legacy input intact. A converted content-only approval can remain inspectable but ineligible for publication until its new-layout metadata satisfies the normal include/active/applicability/access requirements.

Source packages are never edited/deleted. The destination is skipped only when structurally equivalent JSON and identical source bytes already exist; conflicting destinations stop conversion. Multiple legacy versions of the same revision are not automatically ranked or merged. Changed inputs before finalization reject conversion. Legacy schemas are accepted only by this converter, not normal validation/publication. Historical `indexing_chunks/` and sidecar-based older workspace migrations are unsupported and left untouched.

Migration `0003_agent_packages` removes `review_status` and its constraint, renames `review_notes` to `evidence_notes`, and preserves notes, vectors, revisions and ingestion run history. Historical approval fields in old run configuration are retained. Downgrade restores all review statuses as pending; previous review values cannot be recovered. The shared vector extension is unaffected.

After explicit conversion, complete any missing extraction or metadata, validate, dry-run, then publish selected packages. Nothing automatically re-embeds existing database content. Integration checks require a disposable PostgreSQL database configured through `TEST_DATABASE_URL`; skipped integration tests are not evidence of database correctness.

## Verification

The package, converter and batch tests use synthetic documents. Multi-format fixtures exercise schema/provenance contracts and byte preservation, not actual PDF/XLSX parsing or the quality of agent transcription. Publication unit tests isolate model/database boundaries; PostgreSQL integration tests use deterministic synthetic embeddings to exercise revision rules, dry-run dependencies and rollback after writes. A passing unit run does not establish those database guarantees or validate a real embedding model.

Integration tests require a dedicated disposable PostgreSQL database and can upgrade/downgrade schemas, write application tables and delete application-table rows during cleanup. URL guards run before migration; the publication/migration empty-table check runs after migration. Follow the [full test setup](../README.md#testing), and report skipped or unexecuted checks explicitly. The standalone historical PowerShell rename tests are outside pytest and are not part of chunk ingestion.

## Layout migration and deployment

### Repository directory rename

The Python package is now `src`, with source documents in root-level `assets/` and packages in root-level `chunks/`. Run commands from the repository root using `python -m src.cli.<command>`; the old `app.*` commands have no compatibility alias. This is a package rename, not an installable `src/app` layout. No editable installation or new dependency is required.

Before updating an existing environment, stop ingestion, publication, retrieval and gRPC processes. Preserve local changes and inventory all files under the old `app/assets/` and `app/chunks/`, including ignored files, recording relative paths, sizes and SHA-256 values under `.tmp/`. Check for pending package recovery before moving directories. Resolve any interrupted transaction using the old code first.

The new checkout supplies tracked files at their new locations. Transfer remaining local files from `app/assets/` to `assets/` and from `app/chunks/` to `chunks/`, preserving relative paths and bytes. If a destination already exists, compare fingerprints: retain identical files and resolve differing files explicitly before continuing. Do not blindly merge, overwrite or regenerate packages. Move residual old Python caches into `.tmp/` rather than retaining an importable `app/` tree. Compare the complete before/after inventory, then validate each current package with `python -m src.cli.prepare_documents validate --package chunks/<document_id> --json`.

Update service launch commands, IDE settings and external scripts to `src.*` and the new data paths. `--source` and metadata `source_path` remain relative to the assets directory; package IDs, revision IDs and all package bytes remain unchanged. There is no automatic fallback to the old data roots, database migration, re-embedding or republication required by this directory rename.

Legacy review/approved folders, if present in the old chunk workspace, retain their relative layout under `chunks/`; conversion remains an explicit separate operation. Historical `indexing_chunks/`, rename manifests, journals and `rename-history/` remain untouched. Historical source-renaming utilities intentionally retain their old data-path contracts and must not be run against the new source tree.

To roll back, stop all processes, preserve a fresh inventory and return assets/packages to the old locations before starting the old code. Handle occupied destinations by comparing fingerprints, preserve local files, and verify the restored inventory. Do not use `git clean` or a destructive reset to relocate data. No database rollback is needed for this rename.

### Package revision layout

Packages use `chunks/<document_id>/`; `revision_id` remains in metadata. The scanner rejects nested v2 revision layouts with `legacy_layout_convert_explicitly`, and duplicate case-insensitive source paths with `duplicate_source_package`. Two different source paths remain separate documents even when their bytes match. Historical review/approved inputs are excluded from normal scans and still require the explicit legacy converter above.

For nested v2 packages, preview the selected document before applying:

```powershell
python scripts/migrate_chunk_layout.py --document chunks/<document_id> --json
python scripts/migrate_chunk_layout.py --document chunks/<document_id> --revision-id <revision-to-keep> --apply --json
```

A document with multiple revisions requires `--revision-id`; there is no latest-by-time selection. A single revision may omit this flag. Migration preserves the selected package bytes and all IDs, and moves the complete old document directory into ignored `.tmp/package-backups/`. It verifies the selected bytes after replacement and does not infer relations from discarded candidates. Preview changes no package content. Source assets, historical indexing directories and unrelated hidden directories are untouched. The legacy review/approved converter likewise writes to the stable document directory, preserves its inputs and rejects conflicting destinations.

Replacement uses staging plus an OS lock per document; initialization/revision commands also serialize workspace identity changes. Normal replacement failures restore the old package. A process interruption leaves a transaction marker that blocks reads (`package_recovery_required`). After the interrupted command has stopped, roll back that transaction explicitly:

```powershell
python -m src.cli.prepare_documents recover --package chunks/<document_id> --json
```

Recovery restores the previous directory when one was displaced and archives remaining transaction files under `.tmp/package-backups/`; it does not discard either version. `package_busy` means another command holds the lock: retry after it completes. OS locks are released on process exit. An unresolved replacement blocks workspace scans and initialization as well, even if its current directory is temporarily absent. Recovery of an interrupted layout migration restores the old layout; run the migration again afterward. Do not edit package files manually while commands run. Backups are local recovery material, not a replacement for Git history, and are not automatically pruned.

Development-to-environment workflow:

1. Initialize or explicitly revise a source, author the current extraction/chunks and factual metadata, then validate and run technical preflight with the intended local model.
2. Review the four package artifacts and commit them together with related source changes. Package snapshots and extracted text become repository history; the repository must be authorized to hold that content. Only named current-package artifacts are allowed by `.gitignore`; old layouts, staging and auxiliary files stay ignored.
3. Checkout the same commit at the destination. `.gitattributes` disables text/filter/ident/encoding transformations for packages to preserve fingerprints. Configure that environment's database and supply the local model; models and vectors are not distributed with packages.
4. Run preflight, publication dry-run, then explicit publication. A fresh database can accept the latest revision without importing missing supersedes ancestors. Other relation dependencies still need to be present. A database currently on A can upgrade directly to C only when C explicitly declares A as superseded. Checking out an older Git commit does not authorize replacing a newer database revision.

No automatic deployment, Git LFS setup, database schema changes or automatic retirement of removed documents are part of this workflow.
