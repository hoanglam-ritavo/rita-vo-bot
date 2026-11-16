"""Prepare baseline rerank manifests and reviewed snapshots without model/DB IO."""
import argparse
import json

from src.retrieval.rerank_preparation import (
    PreparationError, freeze_workspace, init_workspace, load_workspace, validate_benchmark_arguments,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'validate', 'freeze'):
        child = commands.add_parser(name)
        child.add_argument('--manifest', required=True)
        child.add_argument('--json', action='store_true')
        if name != 'validate':
            child.add_argument('--output-dir', required=True)
        if name == 'validate':
            child.add_argument('--stage', choices=('collection', 'calibration'), default='collection')
        if name == 'freeze':
            child.add_argument('--collection', required=True)
            child.add_argument('--review', required=True)
        if name == 'init':
            child.add_argument('--model-path', default='models/embeddings/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3')
            child.add_argument('--model-revision', default='614241f622f53c4eeff9890bdc4f31cfecc418b3')
            child.add_argument('--rerank-model-path', default='models')
            child.add_argument('--rerank-batch-size', type=int, default=4)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            arguments = ['--model-path', args.model_path, '--model-revision', args.model_revision,
                         '--rerank-model-path', args.rerank_model_path,
                         '--rerank-model-file', 'model_quint8_avx2.onnx', '--rerank-candidates', '30',
                         '--rerank-batch-size', str(args.rerank_batch_size), '--requests', '100', '--rerank']
            validate_benchmark_arguments(arguments)
            result = init_workspace(args.manifest, args.output_dir, arguments)
        elif args.command == 'freeze':
            result = freeze_workspace(args.manifest, args.collection, args.review, args.output_dir)
        else:
            _, items, entries, _ = load_workspace(args.manifest, stage=args.stage)
            result = {'status': 'valid', 'stage': args.stage, 'packages': len(items), 'cases': len(entries),
                      'review_verified': False, 'model_database_verified': False}
        code = 0
    except PreparationError as exc:
        code = 2 if exc.detail['code'] == 'input_unavailable' else 1
        result = {'status': 'error' if code == 2 else 'rejected', 'error': exc.detail}
    except (OSError, ValueError, TypeError, KeyError):
        result, code = {'status': 'error', 'error': {'code': 'preparation_failed'}}, 2
    print(json.dumps(result, ensure_ascii=True, allow_nan=False) if args.json else
          'Rerank preparation: ' + json.dumps(result, ensure_ascii=True, allow_nan=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
