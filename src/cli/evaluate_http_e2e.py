"""Build inventory or explicitly prepare/run stateless HTTP E2E against a manual bot."""
import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inventory','prepare','run'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--profile', choices=('full', 'smoke'), default='full')
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--env-file', type=Path, help='Explicit config file; process values take precedence. Never loads the app environment.')
    parser.add_argument('--binding', type=Path)
    parser.add_argument('--binding-sha256', help='Explicitly reviewed canonical binding fingerprint from prepare.')
    args = parser.parse_args(argv)
    try:
        from src.paths import REPOSITORY_ROOT
        from src.evaluation.http_e2e.config import configuration, private_path, write_private, digest, EvaluationError
        from src.evaluation.http_e2e.inventory import load_inventory, public_inventory, select_profile
        output = private_path(REPOSITORY_ROOT, args.output)
        if output.exists():
            raise EvaluationError('new_output_required')
        scenarios, inputs, catalog = load_inventory(REPOSITORY_ROOT)
        scenarios = select_profile(scenarios, args.profile)
        if args.command == 'inventory':
            if args.env_file or args.binding or args.binding_sha256:
                raise EvaluationError('inventory_is_local_only')
            result = public_inventory(scenarios, inputs, profile=args.profile)
            exit_code = 0
        else:
            if args.command == 'run' and (not args.binding or not args.binding_sha256):
                raise EvaluationError('reviewed_binding_required')
            if args.command == 'prepare' and (args.binding or args.binding_sha256):
                raise EvaluationError('prepare_does_not_read_binding')
            config = configuration(args.base_url, args.env_file)
            from src.database.runtime import run_async
            from src.evaluation.http_e2e.runner import prepare, run
            if args.command == 'prepare':
                result = run_async(prepare(config, scenarios, inputs, catalog, REPOSITORY_ROOT, profile=args.profile))
                exit_code = 2 if any(b['state'] == 'blocked' for b in result['bindings'].values()) else 0
            else:
                from src.artifact_authoring.sources import decode
                binding = decode(private_path(REPOSITORY_ROOT, args.binding).read_bytes())
                result = run_async(run(config, scenarios, inputs, catalog, REPOSITORY_ROOT,
                                       binding, args.binding_sha256, profile=args.profile))
                exit_code = result['exit_code']
        write_private(REPOSITORY_ROOT, output, result)
        summary = {'mode': result['mode'], 'profile': result['profile'], 'exit_code': exit_code}
        if args.command == 'prepare':
            summary['binding_sha256'] = digest(result)
            summary['counts'] = {state: sum(b['state'] == state for b in result['bindings'].values())
                                 for state in ('ready','blocked')}
        else:
            summary['counts'] = result['counts']
        print(json.dumps(summary))
        return exit_code
    except Exception:
        # argparse and drivers never get to print credentials/personnel/response bodies.
        print(json.dumps({'status': 'failed', 'code': 'http_e2e_unavailable'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
