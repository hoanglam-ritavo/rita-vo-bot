"""Publish a complete PDF revision, preserving the previous active run on failure."""
from datetime import datetime, timezone
from importlib.metadata import version
import json
import math
from pathlib import Path
import re
import shutil
import sys
import time
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.catalog.inventory import CatalogError, file_fingerprint, reject_links
from app.catalog.importer import IMPORT_LOCK, ImportRejected, prepare_catalog, revision_values
from app.db import session_scope
from app.db.models import DocumentRevision, DocumentAccessRule, DocumentRelation, IngestionRun, DocumentChunk


class PublishRejected(ValueError):
    """Only fixed, credential/content-safe messages may be raised here."""


def selected_record(root, revision_id):
    try:
        catalog, _ = prepare_catalog(root)
    except (ImportRejected, CatalogError):
        raise PublishRejected('Catalog/source validation failed; reconcile and import metadata first.') from None
    record = next((r for r in catalog['documents'] if UUID(r['revision_id']) == revision_id), None)
    if record is None:
        raise PublishRejected('Revision is absent from the current catalog.')
    if (record['extension'] != '.pdf' or record['indexing_decision'] != 'include'
            or record['review_status'] != 'reviewed' or record['lifecycle_status'] != 'active'):
        raise PublishRejected('Publish requires an included, reviewed, active PDF revision.')
    source = root / 'assets' / record['source_path']
    reject_links(source)
    if not source.resolve().is_relative_to((root / 'assets').resolve()):
        raise PublishRejected('Source must be inside assets.')
    return record, source


async def lock(db):
    await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': IMPORT_LOCK})


async def check_revision(db, record):
    revision_id = UUID(record['revision_id'])
    revision = await db.get(DocumentRevision, revision_id)
    if revision is None or any(getattr(revision, k) != v for k, v in revision_values(record).items()):
        raise PublishRejected('Database revision differs from catalog; import metadata first.')
    groups = set(await db.scalars(select(DocumentAccessRule.group_name).where(
        DocumentAccessRule.revision_id == revision_id)))
    relations = set((await db.execute(select(DocumentRelation.target_revision_id, DocumentRelation.relation_type)
                                     .where(DocumentRelation.source_revision_id == revision_id))).all())
    if (groups != set(record['access_groups']) or relations != {
            (UUID(r['revision_id']), r['type']) for r in record['relations']}):
        raise PublishRejected('Database access rules or relations differ from catalog; import metadata first.')


async def active_run(db, revision_id):
    return await db.scalar(select(IngestionRun).where(
        IngestionRun.revision_id == revision_id, IngestionRun.is_active.is_(True)))


def processing_configuration(source, args):
    from app.ingestion import pdf
    if not re.fullmatch(r'[0-9a-f]{40}', args.model_revision):
        raise PublishRejected('Model revision must be a full lowercase commit hash.')
    pdf.validate_ocr_options(args.ocr_mode, None)
    if args.ocr_dpi <= 0:
        raise PublishRejected('OCR DPI must be positive.')
    with pdf.pymupdf.open(source) as document:
        needs_ocr = args.ocr_mode == 'always' or any(
            not page.get_text('text', sort=True).strip() for page in document)
    tessdata = None
    languages = {}
    if needs_ocr:
        tessdata = pdf.resolve_tessdata(args.tessdata, args.ocr_language)
        languages = {lang: pdf.sha256_file(Path(tessdata) / f'{lang}.traineddata')
                     for lang in args.ocr_language.split('+')}
    config = {
        'pipeline_version': 1, 'pymupdf_version': pdf.pymupdf.VersionBind,
        'libraries': {name: version(name) for name in ('sentence-transformers', 'transformers', 'torch', 'numpy', 'tokenizers')},
        'ocr_mode': args.ocr_mode, 'ocr_language': args.ocr_language, 'ocr_dpi': args.ocr_dpi,
        'language_sha256': languages, 'chunk_size': pdf.CHUNK_SIZE, 'chunk_overlap': pdf.CHUNK_OVERLAP,
        'model_name': pdf.MODEL_NAME, 'model_revision': args.model_revision,
        'embedding_dimensions': 384, 'normalize_embeddings': True, 'prefix': 'passage: ',
        'batch_size': 16,
    }
    return config, tessdata


def validate_chunks(chunks):
    if not chunks:
        raise PublishRejected('No chunks extracted; review the PDF first.')
    for index, chunk in enumerate(chunks):
        vector = chunk.get('embedding')
        if (chunk['chunk_index'] != index or not chunk['content'].strip()
                or not isinstance(chunk['page'], int) or chunk['page'] < 1
                or chunk['extraction_method'] not in ('native', 'ocr')):
            raise PublishRejected('Invalid chunk metadata.')
        if (not isinstance(vector, list) or len(vector) != 384
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector)):
            raise RuntimeError('Invalid embedding vectors.')


def process_snapshot(root, snapshot, record, args, configuration, tessdata, directory):
    from app.ingestion import pdf
    started = time.perf_counter()
    manifest = {'status': 'extraction_complete', 'source_sha256': record['sha256'],
                'source': record['source_path'], 'configuration': configuration}
    pages = pdf.load_pdf(snapshot, ocr_language=args.ocr_language, ocr_dpi=args.ocr_dpi,
                         tessdata=Path(tessdata) if tessdata else None,
                         ocr_mode=args.ocr_mode, manifest=manifest)
    empty = [p['page'] for p in manifest['pages'] if p['empty']]
    if empty:
        raise PublishRejected('Empty extracted pages; review first: ' + ','.join(map(str, empty)))
    chunks = pdf.build_chunks(pages)
    if not chunks:
        raise PublishRejected('No chunks extracted; review the PDF first.')
    for chunk in chunks:
        chunk['source'] = record['source_path']
    chunks = pdf.generate_embeddings(chunks, model_revision=args.model_revision,
                                     cache_folder=root / 'data/cache/huggingface')
    validate_chunks(chunks)
    actual_languages = {key: item['sha256'] for key, item in manifest['language_files'].items()}
    if actual_languages != configuration['language_sha256']:
        raise PublishRejected('OCR language data changed during processing.')
    # Hash again after recognition, so modified language data cannot be published.
    if tessdata and any(pdf.sha256_file(Path(tessdata) / f'{lang}.traineddata') != digest
                        for lang, digest in actual_languages.items()):
        raise PublishRejected('OCR language data changed during processing.')
    manifest.update(chunk_count=len(chunks), document_id=record['document_id'],
                    revision_id=record['revision_id'], run_id=directory.name,
                    elapsed_seconds=time.perf_counter() - started)
    pdf.save_jsonl(chunks, directory / 'chunks.jsonl')
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return chunks


async def start_run(record, configuration, force):
    revision_id = UUID(record['revision_id'])
    async with session_scope() as db:
        await lock(db)
        await check_revision(db, record)
        active = await active_run(db, revision_id)
        if (not force and active is not None and active.source_sha256 == record['sha256']
                and active.configuration == configuration):
            return None, {'status': 'skipped', 'run_id': str(active.id), 'chunk_count': active.chunk_count}
        expected_active = active.id if active else None
        run_id = uuid4()
        db.add(IngestionRun(id=run_id, revision_id=revision_id, status='running',
                            started_at=datetime.now(timezone.utc), source_sha256=record['sha256'],
                            configuration=configuration, model_name=configuration['model_name'],
                            model_revision=configuration['model_revision'], embedding_dimensions=384))
    return run_id, expected_active


async def finish_run(root, record, run_id, expected_active, chunks):
    # Validate file state before opening the short publication transaction.
    current, _ = selected_record(root, UUID(record['revision_id']))
    if current != record:
        raise PublishRejected('Catalog changed during processing; retry after synchronization.')
    validate_chunks(chunks)
    async with session_scope() as db:
        await lock(db)
        await check_revision(db, record)
        active = await active_run(db, UUID(record['revision_id']))
        if (active.id if active else None) != expected_active:
            raise PublishRejected('Another run was activated during processing; existing result preserved.')
        run = await db.get(IngestionRun, run_id)
        if run is None or run.status != 'running' or run.revision_id != UUID(record['revision_id']):
            raise PublishRejected('Run is no longer eligible for publication.')
        db.add_all([DocumentChunk(run_id=run_id, chunk_index=c['chunk_index'], content=c['content'],
                                  source_location={'page_start': c['page'], 'page_end': c['page']},
                                  extraction_method=c['extraction_method'], embedding=c['embedding']) for c in chunks])
        if active:
            active.is_active = False
        await db.flush()  # Release the partial unique index before selecting the new run.
        run.status = 'succeeded'
        run.is_active = True
        run.chunk_count = len(chunks)
        run.finished_at = datetime.now(timezone.utc)


async def fail_run(run_id, code):
    async with session_scope() as db:
        await lock(db)
        run = await db.get(IngestionRun, run_id)
        # A lost commit acknowledgement must not demote an already successful run.
        if run and run.status == 'running':
            run.status = 'failed'
            run.error_code = code
            run.finished_at = datetime.now(timezone.utc)


async def publish(root, args):
    root = Path(root).resolve()
    record, source = selected_record(root, args.revision_id)
    configuration, tessdata = processing_configuration(source, args)
    current, _ = selected_record(root, args.revision_id)
    if current != record:
        raise PublishRejected('Catalog changed during preflight; retry after synchronization.')
    run_id, previous = await start_run(record, configuration, args.force)
    if run_id is None:
        return previous
    print(f'Processing: run={run_id}')
    try:
        directory = root / 'data/extracted' / record['document_id'] / record['revision_id'] / str(run_id)
        reject_links(directory)
        directory.mkdir(parents=True, exist_ok=False)
        snapshot = directory / 'source.pdf'
        reject_links(source)
        with source.open('rb') as incoming, snapshot.open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        fingerprint = file_fingerprint(snapshot)
        if fingerprint['sha256'] != record['sha256'] or fingerprint['size_bytes'] != record['size_bytes']:
            raise PublishRejected('Source changed before processing; reconcile catalog first.')
        chunks = process_snapshot(root, snapshot, record, args, configuration, tessdata, directory)
        if file_fingerprint(snapshot) != fingerprint:
            raise PublishRejected('Source snapshot changed during processing.')
        await finish_run(root, record, run_id, previous, chunks)
        return {'status': 'published', 'run_id': str(run_id), 'chunk_count': len(chunks)}
    except BaseException as exc:
        try:
            await fail_run(run_id, 'rejected' if isinstance(exc, PublishRejected) else 'processing_failed')
        except Exception:
            print(f'Run status update failed: run={run_id}; inspect database before retrying.', file=sys.stderr)
        raise
