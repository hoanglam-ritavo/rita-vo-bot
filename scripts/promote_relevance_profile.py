"""Promote only an emitted, reviewed, passing semantic schema-v3 profile."""
import argparse
import json

from scripts.verify_runtime_assets import verify
from src.paths import REPOSITORY_ROOT
from src.retrieval.rerank_promotion import PromotionError, promote_profile


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('report', 'dataset', 'review', 'profile', 'semantic-artifact', 'training-report', 'version'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--expected-current-sha256',
        help='Required when replacing the current profile; exact SHA-256 of the existing bytes.')
    args = parser.parse_args(argv)
    try:
        receipt = promote_profile(report=args.report, dataset=args.dataset, review=args.review,
            profile=args.profile, semantic_artifact=args.semantic_artifact, training_report=args.training_report,
            version=args.version, expected_current_sha256=args.expected_current_sha256, root=REPOSITORY_ROOT, verify_inventory=verify)
        print(json.dumps(receipt, allow_nan=False))
        return 0
    except PromotionError as exc:
        print(json.dumps({'status': 'rejected', 'code': str(exc)}))
        return 1
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print(json.dumps({'status': 'error', 'code': 'profile_promotion_failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
