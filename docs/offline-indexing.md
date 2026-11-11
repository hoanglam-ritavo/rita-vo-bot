# Offline document preparation and publication

Two module entrypoints implement the reviewed-JSON workflow. They do not implement
retrieval, a chatbot, operational tools or automatic agent review. All processing
is local. Source files in `app/assets/` are never rewritten.

## Installation and prerequisites

Use the repository Python 3.14 environment and install `requirements-dev.txt` and
`requirements-example.txt`. The latter supplies optional PDF/OCR, embedding and
XLSX dependencies, including `openpyxl==3.1.5`. CSV, Markdown and TXT use the Python
standard library. OCR language data must be prepared as described in the
[PDF guide](ingestion.md#pdfocr-review).

Maintain the catalog with the existing catalog commands. Resolve source drift
before extraction; extraction uses catalog identities and never invents or edits
metadata. A `hold`/`pending` record may be extracted for review. Approval and
publication require `include`, `reviewed`, `active`, current effective dates and
the catalog's required ownership, applicability and access metadata.

## Workflow

Run from the repository root; replace placeholders with real local IDs/paths:

```powershell
python -m app.cli.prepare_documents extract --revision-id <revision-uuid>
python -m app.cli.prepare_documents extract --all
python -m app.cli.prepare_documents validate --review <review-directory>
python -m app.cli.prepare_documents approve --review <review-directory> --reviewer "Local reviewer"

# Explicit metadata synchronization; never performed automatically by either CLI.
python -m app.cli.import_catalog --dry-run
python -m app.cli.import_catalog

python -m app.cli.publish_documents --approved <approval-directory> --model-revision <40-hex-model-commit> --dry-run
python -m app.cli.publish_documents --approved <approval-directory> --model-revision <40-hex-model-commit>
python -m app.cli.publish_documents --all --model-revision <40-hex-model-commit>
```

Edit **only `draft.json`** between extraction and validation/approval. The report
from each command identifies newly created package directories. No `--all` is
assumed when selection is omitted. `--approved` can be repeated for an explicit
batch; `--all` rejects revisions with multiple approval directories instead of
guessing which one to publish. Select one package explicitly in that case.

Commands keep the existing repository-root workspace regardless of working
directory. User-supplied relative paths (review, approval, config and tessdata)
are relative to the current working directory. Each extraction/approval creates
a new directory; failures may retain `.extracting-*`/`.staging-*` directories,
which are not valid packages. Windows package IO supports extended-length paths.

## Formats and extraction configuration

Supported inputs are PDF, XLSX, CSV, `.md` and TXT. DOC/DOCX are recognized by the
catalog but unsupported here. Batch extraction reports and skips unsupported,
excluded and retired records. Explicit selection of an ineligible record rejects
it. Other failures are isolated to the selected document where possible; a broken
catalog or source inventory prevents the batch from starting.

Pass `extract --config <json-file>` for settings. Unknown options are rejected.
The settings apply to all selected documents; use individual invocations where
documents need different settings. Defaults:

```json
{
  "encoding": "utf-8-sig",
  "delimiter": ",",
  "tables": [],
  "ocr_mode": "auto",
  "ocr_language": "vie+eng",
  "ocr_dpi": 300,
  "tessdata": null
}
```

- PDF uses native text with OCR fallback, or forced OCR. It retains raw page text,
  page numbers and OCR provenance. Empty pages require explicit review decisions;
  OCR failures cannot be approved. PDF tables are not reconstructed.
- TXT retains paragraphs and source line ranges. Markdown additionally records
  ATX heading context and fenced code blocks; code indentation is preserved and
  no code or embedded instructions are executed. Unclosed fences are flagged.
- CSV retains record number, physical line range, ordered headers and exact parsed
  cell strings, including leading zeroes. Duplicate/empty headers use positional
  column identifiers. `tables: [{"header_row": 2}]` selects an explicit header;
  the default is the first nonempty record. Unequal column counts require review.
- XLSX retains sheet, row/cell coordinates, data types, number formats, merged
  ranges, hidden state, formulas and cached values. All sheets are processed by
  default. Hidden content is retained and flagged. Formula results are never
  calculated or refreshed; cached results require review, and missing results
  appear as unavailable, not as zero or an inferred value. The parser uses the
  documented [openpyxl loading flags](https://openpyxl.readthedocs.io/en/stable/tutorial.html#loading-from-a-file)
  without saving the workbook or following external links.
- For selected XLSX tables use, for example,
  `tables: [{"sheet": "Policy", "range": "A2:D20", "header_row": 2}]`.
  Multiple regions are allowed. Explicit region selection requires a review
  decision because it can omit source content. Preamble rows within the region
  are retained. An empty selected sheet/range is flagged.

Text is normalized to NFC and LF. Horizontal whitespace cleanup applies only to
prose; cell strings and code retain structure. Prose chunks use the existing
500-character / up-to-100-character-overlap boundaries. Code uses exact spans of
up to 500 characters. Tables split row text along line/word boundaries; each
piece repeats sheet/row/header context, so rendered table chunks may exceed 500
characters. Full structure remains in extraction JSON. No exact table-query or
aggregation service is implemented.

## Review package and editing contract (version 1)

`review/<revision_id>/<extraction_id>/` contains `source.<extension>`,
`extraction.json`, `draft.initial.json`, `draft.json` and `manifest.json`.
The manifest hashes the source, extraction and initial draft. `draft.json` is
intentionally editable. Keep the other files unchanged.

Extraction includes identity, source fingerprint, parser/configuration provenance,
segments and warnings. Each segment has raw/normalized text, source location,
method (`native` or `ocr`) and format-specific details. Character offsets refer
to its **normalized `text`**, not the raw source bytes.

The draft has exactly these fields: `schema_version: 1`, `identity`, `chunks`,
`excluded`, and `warning_decisions`. Identity must match extraction. Each chunk
has a UUID `chunk_id`, nonempty `content`, `source_refs` and string `review_notes`.
A reference has `segment_id`, zero-based inclusive `start`, and exclusive `end`.
For example, this is one chunk entry, not a complete package:

```json
{
  "chunk_id": "11111111-1111-4111-8111-111111111111",
  "content": "Synthetic corrected policy.",
  "source_refs": [{"segment_id": "s1", "start": 0, "end": 26}],
  "review_notes": "Compared with the original source."
}
```

Use actual generated IDs/offsets, not the illustrative values. Keep IDs for text
edits; allocate a new UUID for a new/split chunk. Merge by combining references;
array order determines publication order. References cannot cross revisions.
To omit content, add an `excluded` entry with `source_ref` and nonempty `reason`.
Every non-whitespace source character must be covered by a chunk reference or
explicit exclusion; overlapping references are allowed. A coverage check proves
accounting only, not semantic correctness of edited text.

`warning_decisions` maps every generated warning ID to a nonempty explanation.
For example, acknowledge a genuinely blank PDF page after checking the original.
Do not approve missing OCR text or unknown formula values as if they were correct.
Validation rejects unresolved warnings, broken references, duplicate chunk IDs,
empty content, mismatched identities and changed package fingerprints.

Approval copies the checked package to `approved/<revision_id>/<approval_id>/`,
adds derived `payload.json` and an approval manifest with reviewer, UTC time,
content hash and hashes of all package files. Source locations are derived from
extraction, not arbitrary draft fields. All content needed to reproduce this
publication is retained in the approval snapshot. Subsequent draft edits do not
alter it; create another approval. Hashes detect changes, but this local operator
workflow is not a signature, identity provider or adversarial trust boundary.

## Publication and failure behavior

Publication accepts only valid approval packages inside `approved/`. It checks
source/catalog eligibility and database metadata, access rules and relations.
`--dry-run` performs read-only database checks and local validation; it does not
load an embedding model or create a database run. It writes only its local report.
It cannot establish tokenizer compatibility without loading the chosen model.

The real run uses pinned `intfloat/multilingual-e5-small`, 384 dimensions,
`passage: ` prefix and normalized vectors. Weights may be downloaded from the
public model repository on first use; documents are not uploaded. Model cache is
`cache/huggingface`. Preload the same commit in an approved environment before
offline use. Token lengths include prefix and special tokens: oversized chunks
are rejected with `chunk_exceeds_model_limit_reapprove`, never silently truncated.

The publisher embeds the verified in-memory snapshot exactly; it does not call
OCR, extraction, chunking or text normalization. Embeddings must be finite and
have the expected count and dimension. Shared publication transactions insert all
chunks and activate a run atomically under the catalog/import lock. OCR/model
work never occupies that transaction. A competing activation, metadata change or
source change rejects publication and preserves the old active run.

Identical ordered content/provenance, source fingerprint and embedding configuration
skip an already active run; `--force` creates another. Approval timestamps and
reviewer names alone do not cause re-embedding. A revised source or content does.
Chunks get per-run database identities; review IDs and detailed source spans are
retained in JSONB. A merged chunk is marked `ocr` if any contributing span is OCR;
individual span methods remain available.

The published artifacts retain the exact package used, embeddings, approval
identity and processing configuration. Artifact presence or `extraction_complete`
does not prove database success. Failure attempts to mark the new run failed;
database outages or process termination may leave a running row/staging artifacts.
There is no automatic resume or cleanup. Retry as a new run, preserving evidence.

Batch processing is sequential and commits each revision independently. Reports
under `reports/` distinguish processed/skipped/rejected/failed with safe codes and
IDs; neither logs nor report summaries include document text or vectors. Exit 0
means selected work succeeded or was skipped; 1 means rejected input; 2 means
operational/syntax failure. Operational failures take precedence in mixed batches.

## Compatibility and validation

`ingest review` and `ingest publish` remain the legacy single-PDF workflow.
Legacy publish rereads the PDF and **does not use edited review JSON**. Use the
new publisher for approved content. Historical archive JSON is not auto-approved.
No database migration is needed: existing six tables and JSONB provenance suffice.

Run `python -m pytest -m 'not integration'` first. Unit tests use synthetic inputs
and mocked model/database calls. Integration tests require a dedicated disposable
PostgreSQL database through `TEST_DATABASE_URL`; without it they are skipped.
Never use the application database. Real OCR accuracy and model retrieval quality
require separate evaluation; synthetic tests do not establish either.
