# Local PDF ingestion

Ingestion prepares Type A data; it does not implement retrieval, a chatbot, or live
Type B information. One invocation processes one PDF. Source files and catalog
decisions are never edited. Word, Excel, batch processing and job resumption are
not supported.

## Setup and commands

Install the database dependencies and optional `requirements-example.txt` in the
project environment. Prepare local Tesseract language files as described in the
[README](../README.md#pdfocr-review). Apply migrations explicitly, then validate
and import the catalog using the [database guide](catalog-database.md).

```powershell
python main.py                    # Existing database check
python main.py check-db
python main.py ingest review ./assets/synthetic.pdf --skip-embeddings
python -m app.cli.ingest review ./assets/synthetic.pdf --help
```

The synthetic path is illustrative, not a repository asset. Review is database-free
and supports selected pages, forced OCR, crop, output path,
language/DPI, skipped embeddings and optional review artifacts.
Keep generated files under `data/extracted` and review
directories under `data/reports`; use fresh names for experiments. Existing review
output replacement remains atomic.

After an operator has reviewed a PDF and set its catalog decision to `include`,
use its real revision UUID and a full 40-hex-character model commit:

```powershell
python main.py ingest publish --revision-id <revision-uuid> --model-revision <model-commit> --tessdata data/cache/tessdata
```

Replace the angle-bracket placeholders before execution. The model is fixed to
`intfloat/multilingual-e5-small`; a branch name such as `main` is rejected. The first
model load may download public weights, cached under `data/cache/huggingface`.
Documents are processed locally and are not uploaded. Pre-cache weights when
running offline. Database-only commands do not import OCR/model dependencies.

Publish accepts `--ocr-mode auto|always`, `--ocr-language`, `--ocr-dpi`, `--tessdata`
and `--force`. Defaults remain auto, vie+eng, 300 DPI, 500-character chunks and up
to 100-character overlap. Native-only PDFs do not require OCR language files.
Relative tessdata arguments follow the current working directory; catalog, assets,
publish artifacts and model cache are resolved from the repository root.

Publish always processes the entire PDF and generates embeddings; it rejects
review-only page/crop/output/skip-embedding flags. Empty extracted pages reject
publication, including genuinely blank pages: inspect them in review before
deciding how to curate a new source revision. No hidden blank-page override exists.

Ingestion exits with 0 on success or identical active-run skip, 1 on rejected
input/eligibility and 2 on operational failure or CLI syntax errors. Database
checks retain their existing 0/1 behavior. Console output avoids document content,
vectors and database credentials.

## Publication contract

- The entire local catalog/source inventory must pass the existing import
  preflight. The selected PDF must be reviewed, active, included and within its
  effective dates, with owner, applicability, access groups and evidence notes.
- The database revision must be current and match catalog metadata, access rules
  and relations. Missing/stale metadata requires explicit synchronization first.
  Catalog/source checks are point-in-time checks: do not edit these during a run.
- A short transaction records a running attempt and the initial active run.
  OCR and embedding run synchronously outside database transactions. This is a
  sequential CLI, not an async request handler or worker service.
- Each run keeps `source.pdf`, `chunks.jsonl` and `manifest.json` under
  `data/extracted/<document_id>/<revision_id>/<run_id>/`. The copied source must
  match the catalog fingerprint. Chunks identify original one-based pages and
  the catalog source path; directory names never replace document identity.
- Configuration records pipeline/library versions, OCR language hashes, chunk
  parameters, model commit, dimensions, normalization and passage prefix. Bump
  the pipeline version when changing processing semantics. Equal vector size
  alone does not establish compatible embedding spaces.
- An identical active run is skipped unless `--force` is supplied. OCR/model
  dependencies and required language files must be installed even for this check.
- Artifacts are completed first. A final transaction rechecks eligibility,
  inserts all chunks and switches active runs. It uses the same advisory lock
  as metadata import. If another run has changed the active result, publication
  is rejected rather than replacing that result. No lock spans OCR/model work.
- Errors preserve the old active run and attempt to mark the new run failed.
  A database outage can prevent that status update. Killing the process may leave
  a running row and partial artifacts; neither implies success nor blocks retry.
  There is no automatic timeout, resume or artifact cleanup. A manifest's
  `extraction_complete` status does not prove database publication.

Only finite 384-dimensional vectors are accepted. The database is the authority
for successful publication. A successful active run is not a grant of access or
proof of OCR accuracy: future retrieval must still filter permissions, review,
applicability, effective dates and compatible embedding settings.

## Validation

Run `python -m pytest -m 'not integration'` first. Synthetic tests cover shared
review behavior, CLI isolation, provenance, rejection, failure and rerun paths.
Integration tests require an empty, dedicated disposable PostgreSQL database via
`TEST_DATABASE_URL`; they exercise publication, rollback, concurrent activation
and catalog-import coordination. Without that setting they skip, not pass.

Before a real publish, review representative pages against their originals,
including numbers, negations, missing lines and clause order. Mock OCR/embedding
tests do not establish recognition quality. Do not publish to an application
database merely to validate implementation.
