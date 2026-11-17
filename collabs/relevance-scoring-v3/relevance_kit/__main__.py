"""Run with python -m relevance_kit from the extracted kit root."""
import argparse
import json
from pathlib import Path
import zipfile

from .workflow import encode, example, export_input, fit_bundle, kit_identity


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('check')
    demo = commands.add_parser('example')
    demo.add_argument('--output-dir', required=True)
    fit = commands.add_parser('fit')
    fit.add_argument('--bundle', required=True)
    fit.add_argument('--output', required=True)
    fit.add_argument('--gpu', action='store_true')
    args = parser.parse_args(argv)
    try:
        identity = kit_identity()
        if args.command == 'check':
            result = {'status': 'kit_integrity_passed', 'kit_identity': identity}
        elif args.command == 'example':
            output = Path(args.output_dir)
            output.mkdir(parents=True, exist_ok=False)
            dataset = output / 'fictional-development.json'
            dataset.write_bytes(encode(example()))
            result = export_input(dataset, output / 'input.zip', synthetic=True)
        else:
            result = fit_bundle(args.bundle, args.output, gpu=args.gpu)
        print(json.dumps(result | {'accepted_profile': False}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, zipfile.BadZipFile):
        print(json.dumps({'status': 'rejected', 'code': 'development_kit_failed', 'accepted_profile': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
