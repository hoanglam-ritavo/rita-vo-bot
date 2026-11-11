"""Review PDFs locally or publish one catalog revision to PostgreSQL."""
import argparse
from pathlib import Path
import re
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]


def commit(value):
    if not re.fullmatch(r'[0-9a-fA-F]{40}', value):
        raise argparse.ArgumentTypeError('Use a full 40-character model commit hash.')
    return value.lower()


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('Must be positive.')
    return number


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'review':
        try:
            from app.cli.review import create_parser
            from app.ingestion.review import run
            args = create_parser().parse_args(argv[1:])
            return run(args)
        except ValueError:
            print('Review input rejected; check PDF, pages, region and output paths.', file=sys.stderr)
            return 1
        except Exception:
            print('Review failed; check dependencies, OCR data, model and output access.', file=sys.stderr)
            return 2
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('review', help='Local PDF review; use review --help for options.')
    publish = commands.add_parser('publish')
    publish.add_argument('--revision-id', required=True, type=UUID)
    publish.add_argument('--model-revision', required=True, type=commit)
    publish.add_argument('--force', action='store_true')
    publish.add_argument('--ocr-language', default='vie+eng')
    publish.add_argument('--ocr-dpi', type=positive_int, default=300)
    publish.add_argument('--tessdata', type=Path)
    publish.add_argument('--ocr-mode', choices=('auto', 'always'), default='auto')
    args = parser.parse_args(argv)
    from app.db.runtime import run_async
    return run_async(run_publish(args))


async def run_publish(args):
    from app.db import close_database
    from app.ingestion.publish import publish, PublishRejected
    try:
        result = await publish(ROOT, args)
        print(f"{result['status']}: run={result['run_id']} chunks={result['chunk_count']}")
        return 0
    except PublishRejected as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print('Publish failed; check dependencies, source access, database and explicit migrations.', file=sys.stderr)
        return 2
    finally:
        await close_database()


if __name__ == '__main__':
    raise SystemExit(main())
