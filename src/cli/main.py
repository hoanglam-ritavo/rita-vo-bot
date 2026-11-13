"""Application commands, preserving the default database check."""
import argparse
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == 'ingest':
        from src.cli.ingest import main as ingest
        return ingest(args[1:])
    parser = argparse.ArgumentParser(description='Database checks; use src.cli.prepare_documents and src.cli.publish_documents for ingestion.')
    parser.add_argument('command', nargs='?', choices=['check-db', 'ingest'], default='check-db')
    parser.parse_args(args)
    from src.cli.check_db import main as check
    from src.database.runtime import run_async
    return run_async(check())
