"""Retired entrypoint; retained only to explain the replacement workflow."""
def main(argv=None):
    print('This workflow is retired. Use python -m app.cli.prepare_documents init --all, then extract, validate, approve and app.cli.publish_documents.')
    return 2

if __name__ == '__main__':
    raise SystemExit(main())
