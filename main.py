"""Wrapper entrypoint for the database check command."""

from app.cli.check_db import main
from app.db.runtime import run_async


if __name__ == '__main__':
    raise SystemExit(run_async(main()))
