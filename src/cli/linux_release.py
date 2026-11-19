"""Linux release entrypoints. A release never starts or stops the bot process."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

from src.paths import REPOSITORY_ROOT


INPUTS = ('requirements.txt', 'requirements-example.txt', 'chunks/manifest.json', 'alembic.ini')


def launch(*, first):
    parser = argparse.ArgumentParser(description='Prepare Rita VO Bot for release on Linux.')
    parser.add_argument('--apply', action='store_true', help='Install dependencies and write to the release database.')
    args = parser.parse_args()
    root = REPOSITORY_ROOT
    missing = [name for name in INPUTS if not (root / name).is_file()]
    if missing:
        print('Missing release inputs: ' + ', '.join(missing), file=sys.stderr)
        return 1
    mode = 'first' if first else 'update'
    if not args.apply:
        operations = ('verify an empty bot database' if first else
                      'verify migration history and back up the database before writing')
        print(f'Preview ({mode}): prepare Python and Git LFS, {operations}, '
              'apply migrations and corpus, then hand off to the service operator. '
              'Database checks run with --apply.')
        return 0
    if sys.platform != 'linux' or sys.version_info[:2] != (3, 14):
        print('Apply requires Linux and Python 3.14.', file=sys.stderr)
        return 1
    python = root / 'env/bin/python'
    temporary = root / '.tmp'
    try:
        temporary.mkdir(exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix='linux-release-', dir=temporary))
    except OSError:
        print('Could not create the private release report directory.', file=sys.stderr)
        return 1
    print(f'Private release reports: {output.relative_to(root)}', flush=True)
    stage = 'virtualenv'
    try:
        if not python.is_file():
            venv.EnvBuilder(with_pip=True).create(root / 'env')
        version_check = 'import sys; sys.exit(sys.version_info[:2] != (3, 14) or sys.prefix == sys.base_prefix)'
        subprocess.run([str(python), '-c', version_check], cwd=root, check=True)
        stage = 'requirements'
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', 'requirements.txt',
                        '-r', 'requirements-example.txt'], cwd=root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stage = 'git_lfs'
        subprocess.run(['git', 'lfs', 'pull'], cwd=root, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stage = 'release'
        return subprocess.run([str(python), '-m', 'src.cli.linux_release', mode, str(output)],
                              cwd=root, check=False).returncode
    except (OSError, subprocess.SubprocessError):
        print(f'Release stopped during {stage}.', file=sys.stderr)
        return 1


def main():
    from src.release_workflow import apply_release

    parser = argparse.ArgumentParser(description='Run the prepared Linux release steps.')
    parser.add_argument('mode', choices=('first', 'update'))
    parser.add_argument('report_directory', type=Path)
    args = parser.parse_args()
    return apply_release(REPOSITORY_ROOT, args.report_directory, first=args.mode == 'first')


if __name__ == '__main__':
    raise SystemExit(main())
