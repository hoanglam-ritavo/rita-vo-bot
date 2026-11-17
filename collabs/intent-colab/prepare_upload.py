"""Package a fixed file allowlist into repository .tmp; never scans runtime data."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import uuid
import zipfile

from bundle_utils import (HERE, ROOT, SNAPSHOT_ALGORITHM, json_text,
                          private_output, verification_identity)
from build_bundle import render
from author_dataset import data as expected_dataset, review as expected_review

UPLOAD_FILES = {
    'train_intent_standalone.py': HERE / 'train_intent_standalone.py',
    'draft-cases.json': HERE / 'datasets/draft-cases.json',
    'dataset-review.json': HERE / 'datasets/dataset-review.json',
    'requirements-colab.txt': HERE / 'requirements-colab.txt',
    'README.md': HERE / 'README.md',
    'notebooks/train_intent.ipynb': HERE / 'notebooks/train_intent.ipynb',
}


def package(output_dir, verification_report=None):
    output_dir = private_output(output_dir)
    if (HERE / 'train_intent_standalone.py').read_text(encoding='utf-8') != render():
        raise ValueError('standalone_build_drift')
    data = json.loads(UPLOAD_FILES['draft-cases.json'].read_text(encoding='utf-8'))
    review = json.loads(UPLOAD_FILES['dataset-review.json'].read_text(encoding='utf-8'))
    if data != expected_dataset or review != expected_review:
        raise ValueError('sample_source_drift')
    spec = importlib.util.spec_from_file_location('standalone_upload', UPLOAD_FILES['train_intent_standalone.py'])
    trainer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trainer)
    validation = trainer.inspect_dataset(data)
    if validation['errors'] or data['reviewed'] is not False:
        raise ValueError('invalid_unreviewed_sample')
    if trainer.dataset_hash(data) != review['dataset_sha256']:
        raise ValueError('sample_review_hash_mismatch')
    # Materialize a fixed snapshot before writing, not arbitrary workspace paths.
    payloads = {name: path.read_bytes() for name, path in UPLOAD_FILES.items()}
    verification = None
    if verification_report is not None:
        report_path = private_output(verification_report)
        verification = json.loads(report_path.read_text(encoding='utf-8'))
        if (not isinstance(verification, dict) or verification.get('successful') is not True
                or verification.get('source_hash_algorithm') != SNAPSHOT_ALGORITHM
                or verification.get('source_sha256') != verification_identity()):
            raise ValueError('verification_failed_or_stale')
        counts = [verification.get(key) for key in ('tests_run', 'passed', 'failures', 'errors')]
        skipped = verification.get('skipped')
        if (any(type(n) is not int or n < 0 for n in counts)
                or not isinstance(skipped, list)
                or any(not isinstance(row, dict) or not isinstance(row.get('test'), str)
                       or not isinstance(row.get('reason'), str) for row in skipped)
                or verification.get('source_unchanged') is not True
                or counts[0] <= 0 or counts[2:] != [0, 0]
                or counts[0] != counts[1] + len(skipped)):
            raise ValueError('invalid_verification_counts')
        payloads['verification.json'] = json_text(verification).encode('utf-8')
    manifest = {
        'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
        'files_sha256': {name: hashlib.sha256(value).hexdigest() for name, value in payloads.items()},
        'dataset_reviewed': False, 'verification': {
            'status': 'not_supplied' if verification is None else 'local_checks_only',
            'tests_run': None if verification is None else verification['tests_run'],
            'passed': None if verification is None else verification['passed'],
            'skipped': [] if verification is None else verification['skipped']},
        'runtime_accepted': False,
        'limitations': ['No real training, CUDA, live Colab, database benchmark or model acceptance is implied.',
                        'Skipped checks remain unverified. Source identity is sha256-utf8-lf-v1; archive payload hashes are raw bytes.'],
    }
    manifest_bytes = json_text(manifest).encode('utf-8')
    output_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    archive = output_dir / 'colab-upload.zip'
    temporary = output_dir / ('.colab-upload-' + token + '.zip')
    manifest_path = output_dir / 'bundle-manifest.json'
    manifest_tmp = output_dir / ('.bundle-manifest-' + token + '.json')
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as stream:
            for name, value in payloads.items():
                stream.writestr(name, value)
            stream.writestr('bundle-manifest.json', manifest_bytes)
        with zipfile.ZipFile(temporary) as stream:
            if stream.testzip() is not None or set(stream.namelist()) != set(payloads) | {'bundle-manifest.json'}:
                raise ValueError('archive_integrity_failure')
            for name, expected in manifest['files_sha256'].items():
                if hashlib.sha256(stream.read(name)).hexdigest() != expected:
                    raise ValueError('archive_hash_failure')
        manifest_tmp.write_bytes(manifest_bytes)
        os.replace(manifest_tmp, manifest_path)
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)
    return {'archive': str(archive), 'files': len(payloads) + 1,
            'dataset_cases': len(data['cases']), 'human_review_pending': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / '.tmp/intent-colab/dist')
    parser.add_argument('--verification-report', type=Path)
    args = parser.parse_args(argv)
    try:
        result = package(args.output_dir, args.verification_report)
    except (ValueError, OSError):
        print(json.dumps({'status': 'failed', 'code': 'package_inputs_invalid_or_unavailable'}))
        return 2
    print(json.dumps({'status': 'packaged', **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
