"""Check package tokens and optionally embeddings without database access or writes."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import os

from app.cli.publish_documents import model_commit
from app.ingestion import document_packages as packages
from app.ingestion.preflight import check
from app.paths import REPOSITORY_ROOT


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--package', type=Path, required=True)
    cli.add_argument('--model-path', type=Path, required=True)
    cli.add_argument('--model-revision', type=model_commit, required=True)
    cli.add_argument('--check-embeddings', action='store_true')
    cli.add_argument('--json', action='store_true')
    args = cli.parse_args(argv)
    # Dependency diagnostics may contain local paths or input text. Only emit our report.
    with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
        result = check(REPOSITORY_ROOT, args.package, args.model_path, args.model_revision,
                       check_embeddings=args.check_embeddings)
    return packages.report(REPOSITORY_ROOT, 'preflight-documents', [result], as_json=args.json)


if __name__ == '__main__':
    raise SystemExit(main())
