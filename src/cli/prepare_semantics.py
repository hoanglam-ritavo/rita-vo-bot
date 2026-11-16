"""Offline semantic authoring entrypoint; no implicit model/database work."""
from src.semantics.preparation import main


if __name__ == '__main__':
    raise SystemExit(main())
