"""Prepare private, unranked scoring review or export reviewed development rows."""
import argparse
import json

from src.retrieval.scoring_review import export_development, private_path, worksheet


def _write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('worksheet', 'export'):
        child = commands.add_parser(name)
        child.add_argument('--dataset', required=True)
        child.add_argument('--collection', required=True)
        if name == 'worksheet':
            child.add_argument('--output-dir', required=True)
        else:
            child.add_argument('--review', required=True)
            child.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'worksheet':
            output = private_path(args.output_dir)
            if output.exists():
                raise ValueError('scoring_review_output_exists')
            data, review, page = worksheet(args.dataset, args.collection)
            output.mkdir(parents=True, exist_ok=False)
            _write(output / 'source-review.json', data)
            _write(output / 'human-review.json', review)
            with (output / 'index.html').open('x', encoding='utf-8') as stream:
                stream.write(page)
            result = {'status': 'unapproved_source_review', 'cases': len(data['cases'])}
        else:
            output = private_path(args.output)
            if output.exists():
                raise ValueError('scoring_review_output_exists')
            data = export_development(args.dataset, args.collection, args.review)
            if output in {private_path(args.dataset), private_path(args.collection), private_path(args.review)}:
                raise ValueError('scoring_review_output_overwrites_input')
            output.parent.mkdir(parents=True, exist_ok=True)
            _write(output, data)
            result = {'status': 'reviewed_development_exported', 'rows': len(data['rows'])}
        print(json.dumps(result | {'accepted_profile': False}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({'status': 'rejected', 'code': 'scoring_review_failed', 'accepted_profile': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
