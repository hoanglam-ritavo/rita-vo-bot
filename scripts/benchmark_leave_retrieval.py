"""Readonly before/after leave ordering; private cases/reports, no acceptance."""
import argparse
import asyncio
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import time

from src.paths import REPOSITORY_ROOT


def private_path(value):
    path = Path(value).resolve()
    temporary = (REPOSITORY_ROOT / '.tmp').resolve()
    if path == temporary or not path.is_relative_to(temporary):
        raise ValueError('private_path_required')
    return path


def load_cases(path):
    data = json.loads(private_path(path).read_bytes())
    if not isinstance(data,list) or not 1 <= len(data) <= 100:
        raise ValueError('invalid_leave_cases')
    ids = set()
    for row in data:
        if (not isinstance(row,dict) or set(row)-{'id','text','expected_role','expected_system'}
                or not isinstance(row.get('id'),str) or not row['id'] or row['id'] in ids
                or not isinstance(row.get('text'),str) or not row['text'].strip() or len(row['text'])>8000
                or row.get('expected_role') not in {'general_procedure','policy','system_procedure','session_case','special_case'}
                or row.get('expected_system') not in {None,'workit','eleave'}):
            raise ValueError('invalid_leave_case')
        ids.add(row['id'])
    return data


def percentile95(values):
    return sorted(values)[math.ceil(len(values)*.95)-1]


async def collect(settings,cases,top_k):
    from src.answering.pipeline import matches_reference
    from src.retrieval import RetrievalService
    from src.database import close_database
    service = RetrievalService(model_path=settings.model_path,model_revision=settings.model_revision,
                               device='cpu',rerank=settings.rerank,semantic=settings.semantic)
    report = {'quality_acceptance':False,'database_writes':False,'rows':[]}
    captures=[]
    try:
        started=time.perf_counter()
        await service.start()
        report['startup_seconds']=time.perf_counter()-started
        if service.policy.catalog is None:
            raise ValueError('retrieval_policy_unavailable')
        reorder=service.policy.reorder

        def metadata(match):
            b=service.policy._bindings.get((match.get('document_id'),match.get('revision_id'),match.get('chunk_index')))
            if isinstance(b,list):
                b=next((binding for binding in b if matches_reference(binding.ref,match)),None)
            valid=b is not None and matches_reference(b.ref,match)
            return {'chunk_id':match['chunk_id'],'binding_id':b.id if valid else None,
                    'role':b.role if valid else None,'system':b.system if valid else None}

        def capture(question,matches):
            # Save an internal snapshot before public finalization strips hashes.
            before=[metadata(m) for m in matches]
            started=time.perf_counter()
            ordered=reorder(question,matches)
            elapsed=time.perf_counter()-started
            captures.append((before,[metadata(m) for m in ordered],elapsed))
            return ordered

        service.policy.reorder=capture
        for case in cases:
            started=time.perf_counter()
            async with asyncio.timeout(120):
                result=await service.retrieve(case['text'],top_k)
            before,after,elapsed=captures[-1]
            expected=lambda rows: bool(rows and rows[0]['role']==case['expected_role']
                                      and (case.get('expected_system') is None or rows[0]['system']==case['expected_system']))
            report['rows'].append({'id':case['id'],'before':before[:top_k],'after':after[:top_k],
                'before_expected_first':expected(before),'after_expected_first':expected(after),
                'ordering_ms':elapsed*1000,'retrieval_seconds':time.perf_counter()-started,
                'corpus_fingerprint':result.get('corpus_fingerprint')})
        # Isolated warm ordering overhead, using real candidates but no extra SQL
        # or inference. The query and source excerpts never enter this report.
        from src.retrieval.storage import search
        prepared=await service.prepare_query(cases[0]['text'],top_k)
        candidates=(await search(list(prepared.vector),service.model_revision,service._fingerprint,
                                 30 if settings.rerank.enabled else top_k))['matches']
        durations=[]
        for n in range(110):
            started=time.perf_counter();reorder(cases[0]['text'],candidates)
            if n>=10:durations.append((time.perf_counter()-started)*1000)
        report.update(ordering_p95_ms=percentile95(durations),ordering_median_ms=statistics.median(durations),
                      ordering_under_50ms=percentile95(durations)<50,
                      policy_sha256=service.policy.fingerprint,
                      before_expected_first=sum(r['before_expected_first'] for r in report['rows']),
                      after_expected_first=sum(r['after_expected_first'] for r in report['rows']),
                      top_k=top_k,model_revision=service.model_revision,model_fingerprint=service._fingerprint,
                      reranker_fingerprint=service._reranker.fingerprint if service._reranker else None,
                      lexicon_sha256=service.policy.catalog.lexicon_sha256)
        # Keep attribution to the exact local artifacts across the run.
        if hashlib.sha256(service.policy.path.read_bytes()).hexdigest()!=report['policy_sha256']:
            raise ValueError('retrieval_policy_changed')
        if hashlib.sha256(service.policy.lexicon_path.read_bytes()).hexdigest()!=report['lexicon_sha256']:
            raise ValueError('lexicon_changed')
        return report
    finally:
        try:
            await service.close()
        finally:
            await close_database()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--top-k',type=int,choices=range(1,21),default=5)
    args=parser.parse_args(argv)
    try:
        case_path=private_path(args.cases)
        case_hash=hashlib.sha256(case_path.read_bytes()).hexdigest()
        cases=load_cases(case_path);output=private_path(args.output)
        if output.exists():
            raise ValueError('new_output_required')
        from src.api.config import load_settings
        from src.database.runtime import run_async
        settings=load_settings()
        with open(os.devnull,'w') as sink,redirect_stdout(sink),redirect_stderr(sink):
            report=run_async(collect(settings,cases,args.top_k))
        if hashlib.sha256(case_path.read_bytes()).hexdigest()!=case_hash:
            raise ValueError('cases_changed')
        report['cases_sha256']=case_hash
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8',newline='\n') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
        print(json.dumps({'status':'recorded','cases':len(cases),'before':report['before_expected_first'],
                          'after':report['after_expected_first'],'ordering_p95_ms':report['ordering_p95_ms'],
                          'quality_acceptance':False}))
        return 0
    except Exception:
        print(json.dumps({'status':'failed','code':'leave_benchmark_unavailable_or_invalid'}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
