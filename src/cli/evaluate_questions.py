"""Run source-fixture HTTP/reducer regressions; reports stay under .tmp."""
import argparse
import asyncio
import json
from pathlib import Path
from src.paths import REPOSITORY_ROOT
from src.artifact_authoring.sources import checked


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--questions-dir', type=Path, help='Private indexed question set below repository .tmp.')
    parser.add_argument('--readonly', action='store_true', help='Use real Type A SELECT-only paths; defer personnel/source-failure fixtures.')
    parser.add_argument('--retrieval-limit', type=int, default=40, choices=range(0,501))
    args = parser.parse_args(argv)
    try:
        path = args.output.absolute()
        if '..' in path.parts or not path.is_relative_to(REPOSITORY_ROOT / '.tmp'):
            raise ValueError('new_private_report_required')
        path = checked(path)
        if path.exists():
            raise ValueError('new_private_report_required')
        if args.readonly:
            from src.evaluation.readonly import run_readonly
            from src.database.runtime import run_async
            report = run_async(run_readonly(REPOSITORY_ROOT, retrieval_limit=args.retrieval_limit,
                                            questions_dir=args.questions_dir))
        else:
            from src.evaluation.questions import run_fixture
            report = asyncio.run(run_fixture(questions_dir=args.questions_dir))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(json.dumps({key: report[key] for key in ('mode', 'cases', 'turns', 'passed', 'failed', 'deferred', 'database_verified') if key in report}))
        return 1 if report['failed'] else 0
    except Exception as error:
        # No question, answer, personnel or driver exception content on stdout.
        print(json.dumps({'status': 'failed', 'code': 'question_evaluation_failed', 'exception_type': type(error).__name__}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
