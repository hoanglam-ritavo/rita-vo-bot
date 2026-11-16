"""Fit development-only local scoring coefficients; no model/DB IO or approval."""
import argparse
import hashlib
import json

from src.retrieval.scoring_training import fit_development
from src.retrieval.semantic_evaluation import private_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', required=True)
    parser.add_argument('--output', required=True, help='New candidate JSON under .tmp.')
    args = parser.parse_args(argv)
    try:
        source, output = private_path(args.dataset), private_path(args.output)
        report_path = output.with_name(output.stem + '.training-report.json')
        if output == report_path or any(p.exists() or p == source for p in (output, report_path)):
            raise ValueError('scoring_output_overwrites_input')
        raw = source.read_bytes()
        data = json.loads(raw)
        scoring, report = fit_development(data)
        if source.read_bytes() != raw:
            raise ValueError('development_inputs_changed')
        report['input_file_sha256'] = hashlib.sha256(raw).hexdigest()
        output.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation keeps existing experiments intact. Partial writes
        # are never acceptance; no runtime profile is emitted by this utility.
        for path, value in ((output, scoring), (report_path, report)):
            with path.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
        print(json.dumps({'status': 'development_candidate_only', 'accepted_profile': False,
            'scoring_digest': scoring['digest'], 'cases': report['cases']}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({'status': 'rejected', 'code': 'scoring_training_failed', 'accepted_profile': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
