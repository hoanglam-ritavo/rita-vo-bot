"""Retired entrypoint; retained only to explain the replacement workflow."""
def main(argv=None):
    print('This workflow is retired. Use python -m app.cli.prepare_documents init --all; use an agent to create v2 chunk packages following docs/offline-indexing.md, then validate --package and publish_documents --package.')
    return 2

if __name__ == '__main__':
    raise SystemExit(main())
