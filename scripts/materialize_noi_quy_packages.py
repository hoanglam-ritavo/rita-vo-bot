"""Serialize explicitly agent-authored source segments and chunk selections.

This task utility does not read PDFs, run OCR, choose boundaries or infer policy.
Authoring inputs and evaluation artifacts belong under ignored .tmp/.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def materialize(source):
    from src.ingestion import document_packages as packages

    data = json.loads(source.read_text(encoding='utf-8'))
    inventory = json.loads((ROOT / '.tmp/noi-quy-indexing/inventory.json').read_text())
    record = next(row for row in inventory if row['number'] == data['number'])
    directory = ROOT / record['package']
    metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
    assert metadata['sha256'] == record['sha256']
    for key, value in data['metadata'].items():
        assert key not in packages.IDENTITY_FIELDS
        metadata[key] = value
    segments = []
    for item in data['segments']:
        page = item['page']
        segments.append(dict(segment_id=item['id'], raw=item['text'], text=item['text'],
                             location=dict(kind='pdf', page_start=page, page_end=item.get('page_end', page)),
                             extraction_method='ocr', details=item.get('details', {})))
    by_id = {s['segment_id']: s for s in segments}
    assert len(by_id) == len(segments)
    def ref(key):
        return dict(segment_id=key, start=0, end=len(by_id[key]['text']))
    chunks, cases, coverage = [], [], []
    for item in data['chunks']:
        # The author explicitly supplies the references, content and question.
        index = len(chunks)
        content = item.get('content')
        if content is None:
            content = data['title'] + '\n' + '\n'.join(by_id[k]['text'] for k in item['refs'])
        chunks.append(dict(content=content, source_refs=[ref(k) for k in item['refs']]))
        for q in item.get('questions', []):
            question = q if isinstance(q, str) else q['question']
            category = 'detail' if isinstance(q, str) else q.get('category', 'detail')
            case_id = f'q{len(cases)+1:03d}'
            cases.append(dict(id=case_id, question=question, category=category,
                              critical=category == 'condition', required_groups=[[index]]))
            coverage.append(dict(case_id=case_id, chunk_index=index, segment_ids=item['refs']))
    warnings = data.get('warnings', [])
    extraction = dict(schema_version=2, status='complete', configuration=dict(language='vi',page_count=record['pages'],
        text_normalization='Agent transcription; normalized line wrapping; table rows retain explicit headers and merged-cell context.'),
        parser_metadata=dict(method='agent-visual-transcription-of-local-pymupdf-renders',renderer='PyMuPDF',render_scale=1.8,
                             external_ocr_service=False,inspected_pages=data['inspected_pages']),
        segments=segments, warnings=[dict(warning_id=w['id'],code=w['code'],location=w['location']) for w in warnings])
    draft = dict(schema_version=2,chunks=chunks,excluded=[dict(source_ref=ref(e['id']),reason=e['reason']) for e in data.get('excluded',[])],
                 warning_decisions={w['id']: w['decision'] for w in warnings})
    packages.validate_extraction(extraction)
    packages.validate_chunks(extraction, draft)
    # Validate the full package using the normal CLI after serialization.
    write(directory/'metadata.json', metadata)
    write(directory/'extraction.json', extraction)
    write(directory/'chunks.json', draft)
    _, payload, _ = packages.load_package(ROOT,directory)
    cases.extend(data.get('extra_cases', []))
    write(ROOT/f'.tmp/noi-quy-indexing/cases/{metadata["document_id"]}.json',dict(revision_id=metadata['revision_id'],
        chunks_sha256=packages.canonical_hash(payload['chunks']),cases=cases))
    write(ROOT/f'.tmp/noi-quy-indexing/coverage/{metadata["document_id"]}.json',dict(source_path=metadata['source_path'],rows=coverage))
    print(json.dumps(dict(package=record['package'],chunks=len(chunks),cases=len(cases))))


if __name__ == '__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    materialize(parser.parse_args().source)
