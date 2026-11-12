"""Read-only manual evaluation of the published benefits package; no answer generation.

Run from the repository root: python -m scripts.evaluate_retrieval --model-path ...
--model-revision ... . Optional --output must stay under the ignored .tmp workspace.
"""
import argparse
import json
import time
from pathlib import Path
from contextlib import redirect_stdout, redirect_stderr
import os

from sqlalchemy import select, func, text
from app.database import session_scope, close_database
from app.database.models import IngestionRun, DocumentChunk
from app.database.runtime import run_async
from app.retrieval import RetrievalService
from app.ingestion.document_packages import snapshot_files, files_hash
from app.paths import REPOSITORY_ROOT

PACKAGE = REPOSITORY_ROOT / 'app/chunks/d1ed5f3f-57ed-465d-a080-97c4a39efd8d/9ccea696-6089-48de-8367-315648f9668b'
CASES = [
    ('Nhân viên kết hôn được hỗ trợ bao nhiêu?', 'p3-marriage-employee'),
    ('Công ty có chế độ gì khi cán bộ nhân viên cưới?', 'p3-marriage-employee'),
    ('Nhân viên nữ sinh con được hưởng phúc lợi gì?', 'p3-birth'),
    ('Sinh em bé cần giấy tờ gì để nhận hỗ trợ của công ty?', 'p3-birth'),
    ('Nằm viện trên ba ngày thì được hỗ trợ thế nào?', 'p3-inpatient'),
    ('Nghỉ ốm điều trị nội trú cần giấy tờ gì để nhận phúc lợi?', 'p3-inpatient'),
    ('Điều trị ngoại trú từ năm ngày được hỗ trợ bao nhiêu?', 'p3-outpatient'),
    ('Không nằm viện mà nghỉ ốm ở nhà thì cần giấy tờ gì để được hỗ trợ?', 'p3-outpatient'),
    ('Ngày quốc tế phụ nữ 8/3 được hưởng mức phúc lợi nào?', 'p3-womens-day'),
    ('Nhân viên nữ có thâm niên trên 48 tháng được bao nhiêu tiền dịp 8 tháng 3?', 'p3-womens-day'),
    ('Quốc tế thiếu nhi con nhân viên được quà bao nhiêu?', 'p3-children-midautumn'),
    ('Tết Trung thu có hỗ trợ cho con nhân viên không và cần giấy tờ gì?', 'p3-children-midautumn'),
    ('Thời tiết ngày mai ở Hà Nội thế nào?', None),
    ('Ai là quản lý trực tiếp hiện tại của tôi?', None),
]


async def counts():
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        return {'runs': await db.scalar(select(func.count()).select_from(IngestionRun)),
                'chunks': await db.scalar(select(func.count()).select_from(DocumentChunk))}


async def evaluate(args):
    service = RetrievalService(model_path=args.model_path, model_revision=args.model_revision)
    try:
        before = await counts()
        package_hash = files_hash(snapshot_files(PACKAGE))
        start = time.perf_counter()
        await service.start()
        report = {'model_load_seconds': time.perf_counter() - start, 'top_k': 5, 'cases': []}
        for question, expected in CASES:
            start = time.perf_counter()
            result = await service.retrieve(question, top_k=5)
            sources = [{'chunk_index': m['chunk_index'], 'revision_id': m['revision_id'],
                        'cosine_distance': m['cosine_distance'],
                        'segments': [span['segment_id'] for span in m['source_spans']]}
                       for m in result['matches']]
            found = any(expected in m['segments'] and m['revision_id'] == PACKAGE.name for m in sources) if expected else None
            report['cases'].append({'question': question, 'expected_segment': expected,
                                    'expected_in_top5': found, 'status': result['status'],
                                    'seconds': time.perf_counter() - start, 'sources': sources})
        after = await counts()
        report.update(passed=sum(c['expected_in_top5'] is True for c in report['cases']), total=12,
                      counts_before=before, counts_after=after,
                      package_unchanged=files_hash(snapshot_files(PACKAGE)) == package_hash,
                      notice='Candidates only; no answer or answerability claim for any question.')
        assert before == after and report['package_unchanged']
        return report
    finally:
        await service.close()
        await close_database()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--model-revision', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output and not args.output.resolve().is_relative_to((REPOSITORY_ROOT / '.tmp').resolve()):
        parser.error('Output must stay under .tmp.')
    try:
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            report = run_async(evaluate(args))
    except Exception:
        print('Read-only retrieval evaluation failed; no diagnostic content emitted.')
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
