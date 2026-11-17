"""Package the independent V3 kit, export reviewed rows, or import candidates."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / 'collabs' / 'relevance-scoring-v3'


def runtime():
    name = 'relevance_kit'
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, KIT / name / '__init__.py',
            submodule_search_locations=[str(KIT / name)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    from relevance_kit import workflow
    workflow.kit_identity()
    manifest = workflow.decode((KIT / 'kit-manifest.json').read_bytes())
    for source, expected in manifest['upstream_files_sha256'].items():
        if workflow.sha((ROOT / source).read_text(encoding='utf-8').encode('utf-8')) != expected:
            raise ValueError('kit_upstream_drift_rebuild_and_review_required')
    return workflow


def private(value):
    path = Path(value)
    path = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    base = (ROOT / '.tmp').resolve()
    if path == base or not path.is_relative_to(base):
        raise ValueError('colab_exchange_requires_private_tmp_path')
    return path


def package(output):
    flow = runtime()
    manifest_raw = (KIT / 'kit-manifest.json').read_bytes()
    manifest = flow.decode(manifest_raw)
    contents = {name: (KIT / name).read_bytes() for name in manifest['files_sha256']}
    if any(flow.sha(raw) != manifest['files_sha256'][name] for name, raw in contents.items()):
        raise ValueError('kit_inputs_changed')
    flow.write_archive(private(output), contents | {'kit-manifest.json': manifest_raw})
    return {'status': 'standalone_kit_packaged', 'accepted_profile': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    pack = commands.add_parser('package')
    pack.add_argument('--output', required=True)
    export = commands.add_parser('export')
    export.add_argument('--dataset', required=True)
    export.add_argument('--output', required=True)
    ingest = commands.add_parser('import')
    ingest.add_argument('--bundle', required=True)
    ingest.add_argument('--result', required=True)
    ingest.add_argument('--output-dir', required=True)
    args = parser.parse_args(argv)
    try:
        flow = runtime()
        if args.command == 'package':
            result = package(args.output)
        elif args.command == 'export':
            result = flow.export_input(private(args.dataset), private(args.output))
        else:
            result = flow.import_result(private(args.bundle), private(args.result), private(args.output_dir))
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, zipfile.BadZipFile):
        print(json.dumps({'status': 'rejected', 'code': 'relevance_colab_exchange_failed',
                          'accepted_profile': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
