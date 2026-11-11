"""Thin command dispatcher; no database or model work at import time."""

from app.cli.main import main


if __name__ == '__main__':
    raise SystemExit(main())
