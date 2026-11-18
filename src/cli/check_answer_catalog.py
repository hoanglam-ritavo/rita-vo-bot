"""Read-only source/lexicon-bound catalog validation; never business acceptance."""
import argparse
from collections import Counter
import json
from pathlib import Path
from src.answering.catalog import load_catalog
from src.paths import REPOSITORY_ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog', type=Path, default=REPOSITORY_ROOT/'artifacts/answers/catalog.json')
    parser.add_argument('--lexicon', type=Path, default=REPOSITORY_ROOT/'artifacts/semantics/lexicon.json')
    args = parser.parse_args(argv)
    try:
        catalog, lexicon = load_catalog(args.catalog, args.lexicon)
        print(json.dumps({'status':'valid', 'documents':len({c.document_id for c in catalog.coverage}),
            'chunks':len(catalog.coverage), 'cards':len(catalog.cards), 'concepts':len(lexicon['concepts']),
            'coverage':dict(Counter(c.disposition for c in catalog.coverage)),
            'notice':'Structure/lexicon pairing only; use impact and target DB checks for source compatibility.'}))
        return 0
    except Exception:
        print(json.dumps({'status':'invalid','code':'answer_catalog_invalid_or_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
