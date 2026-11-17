"""Run the canonical src/tests kit suite; keep receipts and fixtures in .tmp."""
from datetime import datetime, timezone
import importlib.util
import json
import unittest

from bundle_utils import ROOT, SNAPSHOT_ALGORITHM, json_text, private_output, verification_identity


def main():
    before = verification_identity()
    path = ROOT / 'src/tests/test_intent_colab.py'
    spec = importlib.util.spec_from_file_location('intent_colab_checks', path)
    tests = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tests)
    suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    after = verification_identity()
    summary = {
        'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'source_hash_algorithm': SNAPSHOT_ALGORITHM, 'source_sha256': before,
        'source_unchanged': before == after, 'tests_run': result.testsRun,
        'passed': result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        'failures': len(result.failures), 'errors': len(result.errors),
        'skipped': [{'test': test.id(), 'reason': reason} for test, reason in result.skipped],
        'successful': result.wasSuccessful() and before == after,
        'scope': 'synthetic_local_not_real_colab_or_training',
    }
    output = private_output(ROOT / '.tmp/intent-colab/verification.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json_text(summary), encoding='utf-8', newline='\n')
    print(json.dumps({'report': str(output), 'passed': summary['passed'],
                      'skipped': len(summary['skipped']), 'successful': summary['successful']}))
    return 0 if summary['successful'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
