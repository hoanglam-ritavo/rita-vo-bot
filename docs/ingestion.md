# Legacy local PDF ingestion

This guide documents the legacy `ingest review/publish` commands. For the new
editable JSON approval and multi-format batch workflow, see [offline indexing](offline-indexing.md).

Legacy ingestion prepares Type A data; it does not implement retrieval, a chatbot, or live
Type B information. One invocation processes one PDF. Source files and catalog
decisions are never edited. Word, Excel, batch processing and job resumption are
not supported.

## Setup and commands

Install the database dependencies and optional `requirements-example.txt` in the
project environment. Prepare local Tesseract language files as described in the
[OCR setup below](#pdfocr-review). Apply migrations explicitly, then validate
and import the catalog using the [database guide](catalog-database.md).

```powershell
python main.py                    # Existing database check
python main.py check-db
python main.py ingest review ./app/assets/synthetic.pdf --skip-embeddings
python -m app.cli.ingest review ./app/assets/synthetic.pdf --help
```

The synthetic path is illustrative, not a repository asset. Review is database-free
and supports selected pages, forced OCR, crop, output path,
language/DPI, skipped embeddings and optional review artifacts.
Keep generated files under `indexing_workspace/review` and review
directories under `indexing_workspace/reports`; use fresh names for experiments. Existing review
output replacement remains atomic. The default output is anchored to the repository
root; explicit relative output/review/tessdata paths use the current working directory.
Review JSON is not automatically approved, and publish does not consume edited JSON.

After an operator has reviewed a PDF and set its catalog decision to `include`,
use its real revision UUID and a full 40-hex-character model commit:

```powershell
python main.py ingest publish --revision-id <revision-uuid> --model-revision <model-commit> --tessdata indexing_workspace/cache/tessdata
```

Replace the angle-bracket placeholders before execution. The model is fixed to
`intfloat/multilingual-e5-small`; a branch name such as `main` is rejected. The first
model load may download public weights, cached under `indexing_workspace/cache/huggingface`.
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
  `indexing_workspace/published_artifacts/<document_id>/<revision_id>/<run_id>/`. The copied source must
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

## PDF/OCR review

For Type A metadata storage, see the [catalog database guide](catalog-database.md).
After explicit migrations, preview with `python -m app.cli.import_catalog --dry-run`,
then synchronize with `python -m app.cli.import_catalog`. Both require the configured
database; neither creates chunks or embeddings or changes the local catalog.

Install example dependencies separately from the database dependencies:

```powershell
python -m pip install -r requirements-example.txt
New-Item -ItemType Directory -Force indexing_workspace/cache/tessdata | Out-Null
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/vie.traineddata -OutFile indexing_workspace/cache/tessdata/vie.traineddata
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/eng.traineddata -OutFile indexing_workspace/cache/tessdata/eng.traineddata
```

These are official Tesseract language files. PyMuPDF uses integrated OCR support directly, without `pytesseract` or Poppler. Processing runs locally; the PDF is not uploaded.

First inspect extraction without loading an embedding model:

```powershell
python main.py ingest review ./app/assets/noi-quy-qui-dinh/noi-quy-lao-dong-quy-dinh-quy-trinh-hien-hanh/1-noi-quy-lao-dong-tap-doan-rita-vo.pdf --tessdata indexing_workspace/cache/tessdata --skip-embeddings --output indexing_workspace/review/rita-vo-ocr-chunks.jsonl
```

Remove `--skip-embeddings` to generate normalized vectors with `intfloat/multilingual-e5-small`. The first run may download public model weights from Hugging Face. Review and publish both explicitly use `indexing_workspace/cache/huggingface` for model weights.

Review defaults to `--ocr-mode auto`: native text first, then full-page OCR only for pages without text. Use `--ocr-mode always` to OCR even an existing incomplete/corrupt text layer. Page rotation metadata is normalized in memory before OCR so text sorting uses upright coordinates; the source PDF is never saved or rewritten. Defaults remain `--ocr-language vie+eng`, `--ocr-dpi 300`, and `--output indexing_workspace/review/chunks.jsonl`. Language data resolution uses `--tessdata`, then `TESSDATA_PREFIX`, then PyMuPDF discovery. All requested language files must exist when OCR is needed. Missing data or a page OCR error produces a nonzero exit without replacing output. Successfully OCRed but empty pages are reported and skipped; an entirely empty result exits nonzero. JSONL is staged in the output directory and atomically replaced after successful processing; serialization and replacement failures preserve the existing file.

Review JSONL records contain `chunk_index`, original one-based `page`, `content`, `source` (relative to the working directory), and `extraction_method` (`native` or `ocr`), plus `embedding` unless skipped. Text is normalized to Unicode NFC with horizontal whitespace collapsed within each line; line and paragraph breaks are retained. Chunks contain at most 500 characters, prefer paragraph/line/word boundaries, and overlap by at most 100 characters using complete words. Oversized individual tokens require hard splitting. Chunks never cross pages. This is not automatic clause recognition or correction of missing words, diacritics, or reading order. Tables are not reconstructed. Do not commit generated/private content.

For a reproducible review without embeddings, choose a new review directory and output filename:

```powershell
python main.py ingest review ./app/assets/noi-quy-qui-dinh/noi-quy-lao-dong-quy-dinh-quy-trinh-hien-hanh/1-noi-quy-lao-dong-tap-doan-rita-vo.pdf --pages 1-3,8,15,26,29 --ocr-mode always --tessdata indexing_workspace/cache/tessdata --skip-embeddings --output indexing_workspace/review/ocr-fast-review.jsonl --review-dir indexing_workspace/reports/ocr-review/fast-run
```

`--pages` uses one-based numbers and inclusive ranges, removes duplicates and processes pages in document order. All ranges are validated before OCR. `--review-dir` must be new or empty, and the JSONL output must be outside it. Each selected page produces `.raw.txt` and `.normalized.txt` files; OCR pages additionally produce a PNG rendered with the same settings as the raster passed to integrated OCR. Native pages have no OCR input image. The manifest records the source SHA-256, PyMuPDF version, selected pages, language-file paths and hashes, mode, region, DPI, timings and chunk count. `status: extraction_complete` describes extraction, not approval or successful JSONL publication; check the command exit status. Failed runs may leave partial review artifacts; use a fresh directory when retrying. Console messages do not include extracted document content.

To isolate a region, add `--ocr-region LEFT TOP RIGHT BOTTOM` together with `--ocr-mode always`. Coordinates are fractions in `[0, 1]` of the upright page, with origin at the top left, and must describe a positive-area rectangle. For example, `--ocr-region 0.1 0.1 0.9 0.9` selects the central 80%; it is an illustration, **not** a validated crop for this document. Inspect the saved raster for clipped text. The region is rendered at the chosen DPI into a temporary in-memory PDF at its physical size and OCRed. All selected pages share that region; run cover, body and signing pages separately if they need different bounds. Cropping is explicit: no automatic border/stamp removal, deskew, layout detection or handwriting recognition is added.

For an optional model comparison, manually download the official best weights separately; the script never downloads OCR models:

```powershell
New-Item -ItemType Directory -Force indexing_workspace/cache/tessdata_best | Out-Null
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/vie.traineddata -OutFile indexing_workspace/cache/tessdata_best/vie.traineddata
Invoke-WebRequest https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/eng.traineddata -OutFile indexing_workspace/cache/tessdata_best/eng.traineddata
```

Compare the same pages with `fast + vie+eng`, `best + vie+eng`, then `best + vie`, all at 300 DPI, using distinct output/review paths. Compare inspected body regions separately; test 400 DPI on pages 15 and 26 only after choosing a candidate. Keep human-verified reference transcriptions under `indexing_workspace/reports/ocr-review/`, outside run directories. Evaluate before chunking, normalize Unicode and whitespace identically, and compare exactly the same source region. Measure character errors alongside missing lines, numbers, negations and clause order. Better-looking formatting or passing mock tests is not evidence of improved recognition. No winning configuration or policy-grade accuracy is established without that comparison; defaults remain unchanged. Handwritten dates and stamp/signature regions still require manual verification.

An exploratory local comparison found that `tessdata_best` with `vie` separated an interleaved passage better than the mixed-language runs. That observation is limited to an agent-transcribed excerpt awaiting human verification; neither cropping nor 400 DPI improved that excerpt further. It is a candidate for evaluation, not a validated winning configuration. Historical local comparison artifacts, when present, are under `indexing_workspace/archive/pre-reorganization/reports/ocr-review/` and are not committed.

The synthetic PDF tests mock OCR and embeddings and skip if PyMuPDF is not installed. They also exercise real in-memory PDF rotation/crop geometry without running recognition. Passing them does not establish real-document OCR quality.


## Code organization

CLI parsing lives in `app/cli/ingest.py` and `app/cli/review.py`. The ingestion
package separates PDF extraction/OCR (`pdf`), page-local chunks (`chunking`), local
model execution (`embeddings`), atomic JSONL output (`artifacts`), database-free
review orchestration (`review`) and transactional publication (`publish`).
Model dependencies load only when embeddings are requested; database-only commands
do not import the PDF processing stack. Output formats are unchanged; default paths now use the indexing workspace.
