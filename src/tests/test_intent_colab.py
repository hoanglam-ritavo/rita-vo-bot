"""Standalone Colab kit boundaries. Synthetic checks are not model acceptance."""
import argparse
import ast
import copy
from contextlib import contextmanager
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
import uuid
import zipfile
from unittest.mock import patch
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / 'collabs/intent-colab'
spec = importlib.util.spec_from_file_location('standalone', HERE / 'train_intent_standalone.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


def functions(path):
    return {n.name: n for n in ast.parse(path.read_text(encoding='utf-8')).body
            if isinstance(n, ast.FunctionDef)}


def upstream_function(relative, name, namespace):
    node = functions(ROOT / relative)[name]
    exec(compile(ast.Module(body=[node], type_ignores=[]), relative, 'exec'), namespace)
    return namespace[name]


def load_tool(name):
    spec = importlib.util.spec_from_file_location('colab_tool_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'path', [str(HERE), *sys.path]):
        spec.loader.exec_module(module)
    return module


class BundleChecks(unittest.TestCase):
    def setUp(self):
        # tempfile's mode-0700 directories are inaccessible in this Windows
        # sandbox. Use ordinary owned directories; retain fictional diagnostics.
        self.workspace = ROOT / '.tmp/intent-colab/verification-work' / uuid.uuid4().hex
        self.workspace.mkdir(parents=True, exist_ok=False)
        self.old_cwd = Path.cwd()
        os.chdir(self.workspace)
        self.private = self.workspace / '.tmp'
        self.private.mkdir()
        self.data = json.loads((HERE / 'datasets/draft-cases.json').read_text(encoding='utf-8'))
        self.dataset = self.private / 'cases.json'
        self.save()

    def tearDown(self):
        os.chdir(self.old_cwd)

    def save(self):
        self.dataset.write_text(json.dumps(self.data, ensure_ascii=False), encoding='utf-8')

    def args(self, command='train'):
        return argparse.Namespace(command=command, dataset=str(self.dataset),
                                  model_path=str(self.workspace / 'model'), model_revision='a' * 40,
                                  device='cuda', batch_size=16, output_root=str(self.private / 'runs'))

    def test_source_snapshot_and_exact_training_functions(self):
        ours = functions(HERE / 'train_intent_standalone.py')
        names = {
            'src/intent/runtime.py': ['digest', 'identity', 'scores', 'classify', 'validate_artifact'],
            'src/intent/dataset.py': ['inspect_dataset', 'validate_dataset', 'dataset_hash'],
            'src/intent/training.py': ['fit_head', 'accepted_mask', 'source_metrics', 'calibrate', 'evaluate', 'train'],
            'src/intent/preparation.py': ['train_loaded'],
        }
        for relative, expected in bundle.CONTRACT_SNAPSHOT.items():
            self.assertEqual(hashlib.sha256((ROOT / relative).read_text(encoding='utf-8').encode()).hexdigest(),
                             expected, relative)
        self.assertEqual(bundle.CONTRACT_SNAPSHOT_ALGORITHM, 'sha256-utf8-lf-v1')
        for relative, selected in names.items():
            original = functions(ROOT / relative)
            for name in selected:
                left, right = copy.deepcopy(ours[name]), copy.deepcopy(original[name])
                # Standalone eliminates only a project-local import inside train_loaded.
                right.body = [n for n in right.body if not isinstance(n, ast.ImportFrom)]
                self.assertEqual(ast.dump(left), ast.dump(right), name)

    def test_no_project_imports_and_no_import_time_ml(self):
        tree = ast.parse((HERE / 'train_intent_standalone.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertFalse((node.module or '').startswith(('src', 'scripts')))
            if isinstance(node, ast.Import):
                self.assertFalse(any(n.name.startswith(('src', 'scripts')) for n in node.names))
        code = ('import runpy,sys; runpy.run_path(sys.argv[1],run_name="isolated_import"); '
                'assert not any(x in sys.modules for x in '
                '("numpy","sklearn","torch","sentence_transformers","sqlalchemy"))')
        result = subprocess.run([sys.executable, '-I', '-c', code,
                                 str(HERE / 'train_intent_standalone.py')],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_uploaded_script_help_and_validation_without_repository(self):
        script = self.workspace / 'train_intent_standalone.py'
        shutil.copyfile(HERE / script.name, script)
        for extra in (['--help'], ['validate', '--dataset', str(self.dataset)]):
            result = subprocess.run([sys.executable, '-I', str(script), *extra],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertTrue(output['validation']['review_required'])

    def test_generators_check_is_readonly_from_another_working_directory(self):
        paths = [HERE / 'train_intent_standalone.py', HERE / 'datasets/draft-cases.json',
                 HERE / 'datasets/dataset-review.json']
        before = [p.read_bytes() for p in paths]
        for name in ('build_bundle.py', 'author_dataset.py'):
            result = subprocess.run([sys.executable, str(HERE / name), '--check'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual([p.read_bytes() for p in paths], before)
        builder = load_tool('build_bundle')
        with patch.object(builder, 'render', return_value='synthetic build drift'):
            self.assertEqual(builder.main(['--check']), 1)
        self.assertEqual([p.read_bytes() for p in paths], before)

    def test_source_hash_normalizes_crlf_but_model_hash_does_not(self):
        utils = load_tool('bundle_utils')
        sample = self.private / 'sample.py'
        sample.write_bytes(b'first\nsecond\n')
        source_lf = utils.text_hash(sample)
        raw_lf = bundle.file_hash(sample)
        sample.write_bytes(b'first\r\nsecond\r\n')
        self.assertEqual(utils.text_hash(sample), source_lf)
        self.assertNotEqual(bundle.file_hash(sample), raw_lf)

    def receipt(self, tool, successful=True):
        report = {'source_hash_algorithm': 'sha256-utf8-lf-v1',
                  'source_sha256': tool.verification_identity(), 'successful': successful,
                  'source_unchanged': True, 'failures': 0, 'errors': 0,
                  'tests_run': 3, 'passed': 2, 'skipped': [{'test': 'fictional', 'reason': 'not available'}]}
        path = self.private / 'verification.json'
        path.write_text(json.dumps(report), encoding='utf-8')
        return path, report

    def test_package_allowlist_and_raw_hashes_exclude_private_work(self):
        tool = load_tool('prepare_upload')
        # Files beside a requested output must never be recursively included.
        (self.private / '.env').write_text('FICTIONAL_PRIVATE_MARKER=must_not_package')
        output = self.private / 'dist'
        report_path, report = self.receipt(tool)
        result = tool.package(output, report_path)
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertEqual(set(archive.namelist()), {
                'train_intent_standalone.py', 'draft-cases.json', 'dataset-review.json',
                'requirements-colab.txt', 'README.md', 'verification.json', 'bundle-manifest.json',
                'notebooks/train_intent.ipynb'})
            manifest = json.loads(archive.read('bundle-manifest.json'))
            self.assertEqual(manifest['verification']['passed'], 2)
            self.assertEqual(manifest['verification']['skipped'], report['skipped'])
            self.assertFalse(manifest['runtime_accepted'])
            self.assertFalse(manifest['dataset_reviewed'])
            for name, sha in manifest['files_sha256'].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), sha)
            self.assertNotIn(b'FICTIONAL_PRIVATE_MARKER', b''.join(archive.read(n) for n in archive.namelist()))

    def test_package_without_report_makes_no_verification_claim(self):
        tool = load_tool('prepare_upload')
        result = tool.package(self.private / 'unverified')
        with zipfile.ZipFile(result['archive']) as archive:
            self.assertNotIn('verification.json', archive.namelist())
            self.assertEqual(json.loads(archive.read('bundle-manifest.json'))['verification']['status'],
                             'not_supplied')

    def test_notebook_is_part_of_source_identity_and_invalidates_receipt(self):
        tool = load_tool('prepare_upload')
        report_path, report = self.receipt(tool)
        notebook_key = 'collabs/intent-colab/notebooks/train_intent.ipynb'
        self.assertIn(notebook_key, report['source_sha256'])
        scope = tool.verification_identity.__globals__
        original_hash = scope['text_hash']
        def changed_hash(path):
            return 'changed-notebook' if path.suffix == '.ipynb' else original_hash(path)
        with self.notebook_package_inputs(tool), patch.dict(scope, {'text_hash': changed_hash}):
            with self.assertRaisesRegex(ValueError, 'verification_failed_or_stale'):
                tool.package(self.private / 'dist', report_path)

    def notebook(self):
        return json.loads((HERE / 'notebooks/train_intent.ipynb').read_text(encoding='utf-8'))

    @contextmanager
    def notebook_package_inputs(self, tool):
        # Notebook workflow tests use canonical synthetic inputs independently
        # of a user's edits to the tracked sample. The separate sample/build/
        # review binding regressions stay enabled and still detect that drift.
        source = self.private / 'notebook-package-inputs'
        source.mkdir(exist_ok=True)
        dataset, notes = source / 'draft.json', source / 'review.json'
        dataset.write_text(json.dumps(tool.expected_dataset), encoding='utf-8')
        notes.write_text(json.dumps(tool.expected_review), encoding='utf-8')
        with patch.dict(tool.UPLOAD_FILES, {'draft-cases.json': dataset, 'dataset-review.json': notes}):
            yield

    def notebook_cell(self, identity):
        return ''.join(next(c['source'] for c in self.notebook()['cells'] if c['id'] == identity))

    def notebook_namespace(self):
        namespace = {}
        exec(compile(self.notebook_cell('configuration'), 'configuration', 'exec'), namespace)
        namespace.update(WORKSPACE=self.workspace, KIT_DIR=self.workspace / 'kit with spaces',
                         KIT_ZIP=self.private / 'upload.zip', MODEL_PATH=self.workspace / 'e5 model',
                         WORK_DIR=self.private / 'notebook', DEVICE='cpu')
        namespace['DATASET_PATH'] = namespace['WORK_DIR'] / 'reviewed-cases.json'
        namespace['RUN_ROOT'] = namespace['WORK_DIR'] / 'runs'
        namespace['WORK_DIR'].mkdir()
        exec(compile(self.notebook_cell('workflow-helpers'), 'workflow-helpers', 'exec'), namespace)
        return namespace

    def notebook_fixture(self, namespace, status='candidate_ready_for_benchmark'):
        self.data.update(reviewed=True, review_notes='Synthetic fixture; not actual human review.')
        namespace['DATASET_PATH'].write_text(json.dumps(self.data), encoding='utf-8')
        namespace['KIT_DIR'].mkdir(exist_ok=True)
        (namespace['KIT_DIR'] / 'train_intent_standalone.py').write_text('# mocked CLI only\n')
        namespace['MODEL_PATH'].mkdir(exist_ok=True)
        run = namespace['RUN_ROOT'] / 'fictional-run'
        run.mkdir(parents=True)
        outputs = {'validation.json': {}, 'preflight.json': {}, 'candidate.json': {'fictional': True},
                   'training-report.json': {'fictional': True}}
        manifest = {'complete': True, 'status': status, 'dataset_sha256': bundle.digest(self.data),
                    'outputs': {name: bundle.digest(outputs[name])
                                for name in ('candidate.json', 'training-report.json')}}
        outputs['manifest.json'] = manifest
        for name, content in outputs.items():
            (run / name).write_text(json.dumps(content), encoding='utf-8')
        return run, manifest

    def test_notebook_format_empty_outputs_and_no_training_duplication(self):
        notebook = self.notebook()
        self.assertEqual((notebook['nbformat'], notebook['nbformat_minor']), (4, 5))
        self.assertEqual(notebook['metadata']['kernelspec']['name'], 'python3')
        ids = [c['id'] for c in notebook['cells']]
        self.assertEqual(len(ids), len(set(ids)))
        for cell in notebook['cells']:
            self.assertRegex(cell['id'], r'^[A-Za-z0-9_-]{1,64}$')
            self.assertIsInstance(cell['source'], list)
            self.assertEqual(cell['metadata'], {})
            if cell['cell_type'] == 'code':
                self.assertEqual(cell['outputs'], [])
                self.assertIsNone(cell['execution_count'])
                tree = ast.parse(''.join(cell['source']))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertFalse((node.module or '').startswith(('src', 'sklearn', 'sentence_transformers')))
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                                self.assertNotEqual(target.slice.value, 'reviewed')

    def test_notebook_extracts_verified_kit_and_rerun_never_overwrites(self):
        namespace = self.notebook_namespace()
        tool = load_tool('prepare_upload')
        with self.notebook_package_inputs(tool):
            packaged = tool.package(self.private / 'dist')
        namespace['KIT_ZIP'] = Path(packaged['archive'])
        namespace['prepare_kit']()
        trainer = namespace['KIT_DIR'] / 'train_intent_standalone.py'
        before = trainer.read_bytes()
        namespace['prepare_kit']()
        self.assertEqual(trainer.read_bytes(), before)
        # Exercise the extracted CLI for real without project imports/model/DB.
        namespace['prepare_dataset']()
        result = namespace['run_cli']('validate')
        self.assertFalse(result['validation']['training_allowed'])
        with self.assertRaisesRegex(RuntimeError, 'Human review required'):
            namespace['train_once']()
        trainer.write_text('# explicit private edit\n')
        with self.assertRaisesRegex(RuntimeError, 'Existing kit differs'):
            namespace['prepare_kit']()
        self.assertEqual(trainer.read_text(), '# explicit private edit\n')

    def test_notebook_rejects_unsafe_archives_and_bad_checksums_before_extract(self):
        namespace = self.notebook_namespace()
        tool = load_tool('prepare_upload')
        with self.notebook_package_inputs(tool):
            packaged = tool.package(self.private / 'dist')
        with zipfile.ZipFile(packaged['archive']) as archive:
            valid = {name: archive.read(name) for name in archive.namelist()}
        variants = [dict(valid, **{'../outside.txt': b'fictional'}),
                    dict(valid, **{'draft-cases.json': b'changed checksum'})]
        for payloads in variants:
            with zipfile.ZipFile(namespace['KIT_ZIP'], 'w') as archive:
                for name, content in payloads.items():
                    archive.writestr(name, content)
            with self.assertRaises(RuntimeError):
                namespace['prepare_kit']()
            self.assertFalse(namespace['KIT_DIR'].exists())
        with zipfile.ZipFile(namespace['KIT_ZIP'], 'w') as archive:
            for name, content in valid.items():
                info = zipfile.ZipInfo(name)
                if name == 'draft-cases.json':
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                archive.writestr(info, content)
        with self.assertRaisesRegex(RuntimeError, 'Unsafe archive entry'):
            namespace['prepare_kit']()

    def test_notebook_retains_dataset_and_requires_explicit_upload(self):
        namespace = self.notebook_namespace()
        namespace['KIT_DIR'].mkdir()
        (namespace['KIT_DIR'] / 'draft-cases.json').write_bytes(self.dataset.read_bytes())
        namespace['prepare_dataset']()
        target = namespace['DATASET_PATH']
        target.write_text('{"private-edit": true}')
        namespace['prepare_dataset']()
        self.assertEqual(target.read_text(), '{"private-edit": true}')
        uploader = Mock()
        namespace['colab_files'] = lambda: types.SimpleNamespace(upload=uploader)
        exec(self.notebook_cell('upload-reviewed'), namespace)
        uploader.assert_not_called()

    def test_notebook_reviewed_upload_validates_before_replacement(self):
        namespace = self.notebook_namespace()
        target = namespace['DATASET_PATH']
        target.write_text('{"keep-private-edit": true}')
        api = types.SimpleNamespace(upload=Mock(return_value={'reviewed.json': b'{"reviewed": true}'}))
        namespace['colab_files'] = lambda: api
        validator = Mock(return_value={'validation': {'training_allowed': False}})
        namespace['run_cli'] = validator
        with self.assertRaisesRegex(RuntimeError, 'not reviewed/valid'):
            namespace['upload_reviewed_dataset']()
        self.assertEqual(target.read_text(), '{"keep-private-edit": true}')
        validator.return_value = {'validation': {'training_allowed': True}}
        namespace['upload_reviewed_dataset']()
        self.assertEqual(json.loads(target.read_text()), {'reviewed': True})
        self.assertEqual(validator.call_args.args, ('validate',))
        self.assertTrue(validator.call_args.kwargs['dataset_path'].is_relative_to(namespace['WORK_DIR']))
        self.assertFalse(list(namespace['WORK_DIR'].glob('upload-*.json')))

    def test_notebook_cli_uses_arguments_handles_failures_and_never_echoes_raw_stdout(self):
        namespace = self.notebook_namespace()
        namespace['KIT_DIR'].mkdir()
        (namespace['KIT_DIR'] / 'train_intent_standalone.py').write_text('# mocked CLI\n')
        payload = {'status': 'valid', 'validation': {'training_allowed': False, 'case_count': 600}}
        completed = types.SimpleNamespace(returncode=0, stdout=json.dumps(payload))
        with patch.object(subprocess, 'run', return_value=completed) as execute:
            self.assertEqual(namespace['run_cli']('validate'), payload)
        args, kwargs = execute.call_args
        self.assertEqual(args[0][1], str(namespace['KIT_DIR'] / 'train_intent_standalone.py'))
        self.assertIsInstance(args[0], list)
        self.assertEqual(kwargs['cwd'], str(self.workspace))
        self.assertNotIn('shell', kwargs)
        for completed in [types.SimpleNamespace(returncode=2, stdout='{"status":"failed"}'),
                          types.SimpleNamespace(returncode=0, stdout='PRIVATE QUESTION NEVER ECHO')]:
            with patch.object(subprocess, 'run', return_value=completed), patch('sys.stdout', new_callable=io.StringIO) as output:
                with self.assertRaises(RuntimeError):
                    namespace['run_cli']('validate')
                self.assertNotIn('PRIVATE QUESTION', output.getvalue())

    def test_notebook_install_and_device_check_are_explicit(self):
        namespace = self.notebook_namespace()
        namespace['KIT_DIR'].mkdir()
        requirement = namespace['KIT_DIR'] / 'requirements-colab.txt'
        requirement.write_text('# mocked installation\n')
        with patch.object(subprocess, 'run') as install:
            namespace['install_dependencies']()
        self.assertEqual(install.call_args.args[0],
                         [sys.executable, '-m', 'pip', 'install', '-r', str(requirement)])
        self.assertTrue(install.call_args.kwargs['check'])
        namespace['MODEL_PATH'].mkdir()
        namespace['DEVICE'] = 'cuda'
        fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
        with patch.dict(sys.modules, {'torch': fake_torch}):
            with self.assertRaisesRegex(RuntimeError, 'CUDA unavailable'):
                namespace['check_environment']()
        self.assertEqual(namespace['DEVICE'], 'cuda')

    def test_notebook_train_stops_before_model_check_for_unreviewed_data(self):
        namespace = self.notebook_namespace()
        namespace['LAST_RUN'] = 'previous-selection'
        namespace['run_cli'] = Mock(return_value={'validation': {'training_allowed': False}})
        namespace['check_environment'] = Mock()
        with self.assertRaisesRegex(RuntimeError, 'Human review required'):
            exec(self.notebook_cell('train'), namespace)
        namespace['run_cli'].assert_called_once_with('validate')
        namespace['check_environment'].assert_not_called()
        self.assertIsNone(namespace['LAST_RUN'])

    def test_notebook_train_selects_only_completed_matching_run(self):
        namespace = self.notebook_namespace()
        run, manifest = self.notebook_fixture(namespace)
        def cli(command, *args, **kwargs):
            return ({'validation': {'training_allowed': True}} if command == 'validate'
                    else {'status': manifest['status'], 'run_directory': str(run)})
        namespace['run_cli'] = Mock(side_effect=cli)
        exec(self.notebook_cell('train'), namespace)
        self.assertEqual(namespace['LAST_RUN'], run)
        self.assertIn(str(namespace['MODEL_PATH']), namespace['run_cli'].call_args.args[1])
        manifest['complete'] = False
        (run / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RuntimeError, 'incomplete or failed'):
            namespace['train_once']()
        self.assertIsNone(namespace['LAST_RUN'])

    def test_notebook_export_download_is_allowlisted_and_bound_to_dataset(self):
        namespace = self.notebook_namespace()
        run, manifest = self.notebook_fixture(namespace, status='quality_failed')
        (run / '.env').write_text('FICTIONAL_PRIVATE_MARKER=excluded')
        namespace['LAST_RUN'] = run
        api = types.SimpleNamespace(download=Mock())
        namespace['colab_files'] = lambda: api
        exec(self.notebook_cell('download'), namespace)
        archive_path = Path(api.download.call_args.args[0])
        with zipfile.ZipFile(archive_path) as archive:
            prefix = 'runs/' + run.name + '/'
            self.assertEqual(set(archive.namelist()), {'reviewed-cases.json', *(
                prefix + name for name in ('validation.json', 'preflight.json', 'training-report.json',
                                          'candidate.json', 'manifest.json'))})
            self.assertNotIn(b'FICTIONAL_PRIVATE_MARKER', b''.join(archive.read(n) for n in archive.namelist()))
            self.assertEqual(bundle.digest(json.loads(archive.read('reviewed-cases.json'))),
                             manifest['dataset_sha256'])
        namespace['DATASET_PATH'].write_text('{"different": true}')
        api.download.reset_mock()
        with self.assertRaisesRegex(RuntimeError, 'Dataset hash mismatch'):
            exec(self.notebook_cell('download'), namespace)
        api.download.assert_not_called()

    def test_notebook_export_rejects_corrupt_outputs_and_unselected_run(self):
        namespace = self.notebook_namespace()
        with self.assertRaisesRegex(RuntimeError, 'No completed run selected'):
            namespace['export_run'](None)
        run, manifest = self.notebook_fixture(namespace)
        (run / 'candidate.json').write_text('{"changed": true}')
        with self.assertRaisesRegex(RuntimeError, 'output hash mismatch'):
            namespace['export_run'](run)
        self.assertFalse((namespace['WORK_DIR'] / 'exports').exists())

    def test_package_refuses_stale_or_failed_verification_and_public_output(self):
        tool = load_tool('prepare_upload')
        report_path, report = self.receipt(tool, successful=False)
        with self.assertRaisesRegex(ValueError, 'verification_failed_or_stale'):
            tool.package(self.private / 'dist', report_path)
        report['source_sha256'] = tool.verification_identity()
        report['successful'] = True
        report['passed'] = 3
        report_path.write_text(json.dumps(report), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid_verification_counts'):
            tool.package(self.private / 'dist', report_path)
        report['successful'] = True
        report['source_sha256']['src/intent/runtime.py'] = 'stale'
        report_path.write_text(json.dumps(report), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'verification_failed_or_stale'):
            tool.package(self.private / 'dist', report_path)
        with self.assertRaisesRegex(ValueError, 'output_must_be_under_repository_tmp'):
            tool.package(HERE / 'dist')
        self.assertFalse((HERE / 'dist').exists())

    def test_dataset_contract_counts_and_review_gate(self):
        result = bundle.inspect_dataset(self.data)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['case_count'], 600)
        self.assertTrue(result['review_required'])
        self.assertFalse(result['training_allowed'])
        for split, size in [('train', 60), ('calibration', 20), ('holdout', 20)]:
            self.assertEqual(set(result['counts'][split]['sources'].values()), {size})
        self.assertEqual(len({c['id'] for c in self.data['cases']}), 600)
        self.assertEqual(len({c['question'] for c in self.data['cases']}), 600)

    def test_review_report_is_bound_to_shipped_dataset(self):
        report = json.loads((HERE / 'datasets/dataset-review.json').read_text(encoding='utf-8'))
        self.assertEqual(report['dataset_sha256'], bundle.dataset_hash(self.data))
        self.assertFalse(report['human_reviewed'])
        self.assertFalse(self.data['reviewed'])
        self.assertEqual({c['id'] for c in report['case_audit']},
                         {c['id'] for c in self.data['cases']})
        self.assertFalse(any(g['declared_split_leak'] for g in report['families']))

    def test_invalid_dataset_matches_upstream(self):
        original = upstream_function('src/intent/dataset.py', 'inspect_dataset', {
            'Counter': bundle.Counter, 'unicodedata': bundle.unicodedata,
            'SOURCES': bundle.SOURCES, 'STYLES': bundle.STYLES})
        variants = [self.data, {}]
        for field, value in [('source', 'invalid'), ('styles', []), ('critical', None),
                             ('question', ''), ('split', 'other')]:
            data = copy.deepcopy(self.data)
            data['cases'][0][field] = value
            variants.append(data)
        data = copy.deepcopy(self.data)
        data['cases'][1]['split'] = 'holdout'
        variants.append(data)
        for data in variants:
            self.assertEqual(bundle.inspect_dataset(data), original(data))

    def test_preprocessing_uses_original_not_corrected_variant(self):
        original = upstream_function('src/retrieval/reranking.py', 'query_variants', {
            'unicodedata': bundle.unicodedata, 're': bundle.re})
        for question in ['  thai sản\n và phép  ', 'thai sảng', 'nguyên đáng',
                         'Điều kiện\tđăng ký', '  chào  bạn  ']:
            self.assertEqual(bundle.normalize_question(question), original(question.strip())[0])
        with self.assertRaises(bundle.Failure):
            bundle.normalize_question('x' * 8001)

    def test_model_fingerprint_matches_upstream(self):
        model = self.workspace / 'model'
        model.mkdir()
        (model / 'config.json').write_text('{}')
        (model / 'weights').mkdir()
        (model / 'weights' / 'fake.bin').write_bytes(b'fictional model bytes')
        packages = types.SimpleNamespace(canonical_hash=bundle.digest, Rejected=bundle.Failure,
                                         require=bundle.require)
        original = upstream_function('src/ingestion/embeddings.py', 'model_fingerprint', {
            'Path': Path, 'reject_links': bundle.reject_links, 'packages': packages,
            'file_fingerprint': lambda p: {'sha256': bundle.file_hash(p)}})
        self.assertEqual(bundle.model_fingerprint(model), original(model))
        before = bundle.model_fingerprint(model)
        (model / 'config.json').write_text('{"changed": true}')
        self.assertNotEqual(bundle.model_fingerprint(model), before)

    def test_dataset_outside_tmp_is_rejected(self):
        self.dataset = self.workspace / 'outside.json'
        self.save()
        result, code = bundle.execute(self.args('validate'))
        self.assertEqual(code, 2)
        self.assertEqual(result['error_code'], 'intent_path_outside_tmp')

    def test_unreviewed_train_rejected_before_ml(self):
        with patch.object(bundle, 'load_training_dependencies', side_effect=AssertionError('must not load')):
            result, code = bundle.execute(self.args())
        self.assertEqual((result['status'], code), ('review_required', 2))
        manifest = json.loads((Path(result['run_directory']) / 'manifest.json').read_text())
        self.assertFalse(manifest['complete'])
        self.assertFalse((Path(result['run_directory']) / 'candidate.json').exists())

    def test_model_output_overlap_rejected(self):
        args = self.args()
        args.model_path = str(self.private / 'runs' / 'model')
        result, code = bundle.execute(args)
        self.assertEqual(code, 2)
        self.assertEqual(result['error_code'], 'model_inside_output')

    def test_model_missing_and_symlink_rejection(self):
        with self.assertRaisesRegex(bundle.Failure, 'local_model_missing'):
            bundle.model_fingerprint(self.workspace / 'missing')
        try:
            (self.private / 'linked').symlink_to(self.dataset)
        except OSError:
            self.skipTest('Symlink creation unavailable on this Windows account')
        with self.assertRaisesRegex(bundle.Failure, 'link_path_rejected'):
            bundle.private_path(self.private / 'linked')

    def run_fake(self, failure=None, token_ok=True, passed=True, command='train', mutate=False):
        self.data.update(reviewed=True, review_notes='Synthetic orchestration fixture only, not human review.')
        self.save()
        dataset_path, dataset_data = self.dataset, self.data

        class FakeEncoder:
            def __init__(self, *args):
                self.model_identity = bundle.identity('a' * 40, 'f' * 64, True)
            def load(self):
                if failure:
                    raise failure
            def preflight(self, cases):
                return {'status': 'passed' if token_ok else 'failed', 'identity': self.model_identity}
            def numerical_comparison(self):
                return {'status': 'not_run', 'reason': 'synthetic_fixture'}
            def verify(self):
                pass
            def close(self):
                pass

        def fake_train(encoder, data):
            gates = {g: g != 'latency' for g in bundle.GATES}
            gates['source_f1'] = passed
            artifact = {'schema_version': 1, 'identity': encoder.model_identity, 'fictional_payload': True}
            report = {'dataset_sha256': bundle.dataset_hash(data),
                      'candidate_payload_sha256': bundle.digest(artifact), 'gates': gates}
            artifact['acceptance'] = {'reviewed': True, 'dataset_sha256': bundle.dataset_hash(data),
                                      'report_sha256': bundle.digest(report), 'gates': gates}
            if mutate:
                changed = copy.deepcopy(dataset_data)
                changed['review_notes'] += ' changed'
                dataset_path.write_text(json.dumps(changed), encoding='utf-8')
            return artifact, report

        with patch.object(bundle, 'Encoder', FakeEncoder), \
             patch.object(bundle, 'load_training_dependencies', return_value={'test': 'synthetic'}), \
             patch.object(bundle, 'train_loaded', side_effect=fake_train):
            return bundle.execute(self.args(command))

    def test_completed_candidate_hashes_and_unique_runs(self):
        result, code = self.run_fake()
        self.assertEqual((result['status'], code), ('candidate_ready_for_benchmark', 0))
        directory = Path(result['run_directory'])
        artifact = json.loads((directory / 'candidate.json').read_text())
        report = json.loads((directory / 'training-report.json').read_text())
        manifest = json.loads((directory / 'manifest.json').read_text())
        self.assertTrue(manifest['complete'])
        self.assertFalse(artifact['acceptance']['gates']['latency'])
        self.assertEqual(artifact['acceptance']['report_sha256'], bundle.digest(report))
        self.assertEqual(report['candidate_payload_sha256'], bundle.digest({
            k: v for k, v in artifact.items() if k != 'acceptance'}))
        self.assertEqual(artifact['acceptance']['dataset_sha256'], bundle.dataset_hash(self.data))
        second, _ = self.run_fake()
        self.assertNotEqual(second['run_directory'], result['run_directory'])

    def test_quality_failure_is_diagnostic_complete(self):
        result, code = self.run_fake(passed=False)
        self.assertEqual((result['status'], code), ('quality_failed', 1))
        manifest = json.loads((Path(result['run_directory']) / 'manifest.json').read_text())
        self.assertTrue(manifest['complete'])

    def test_token_failure_and_input_mutation_do_not_complete(self):
        for kwargs, expected in [({'token_ok': False}, 'preflight_failed'),
                                 ({'mutate': True}, 'failed')]:
            result, code = self.run_fake(**kwargs)
            self.assertEqual(result['status'], expected)
            self.assertIn(code, (1, 2))
            directory = Path(result['run_directory'])
            self.assertFalse(json.loads((directory / 'manifest.json').read_text())['complete'])
            self.assertFalse((directory / 'candidate.json').exists())

    def test_interruption_cuda_and_generic_failure_are_safe(self):
        for failure, expected in [(KeyboardInterrupt(), 'interrupted'),
                                   (bundle.Failure('cuda_unavailable'), 'cuda_unavailable'),
                                   (RuntimeError('PRIVATE QUESTION NEVER LOG'), 'intent_training_failed')]:
            result, code = self.run_fake(failure=failure)
            self.assertEqual(result['error_code'], expected)
            self.assertIn(code, (2, 130))
            self.assertNotIn('PRIVATE', json.dumps(result))
            directory = Path(result['run_directory'])
            self.assertFalse(json.loads((directory / 'manifest.json').read_text())['complete'])

    def test_write_failure_never_leaves_complete_manifest(self):
        original = bundle.atomic_json
        def fail_candidate(path, data):
            if path.name == 'candidate.json':
                raise OSError('synthetic interrupted write')
            return original(path, data)
        with patch.object(bundle, 'atomic_json', side_effect=fail_candidate):
            result, code = self.run_fake()
        self.assertEqual(code, 2)
        manifest = json.loads((Path(result['run_directory']) / 'manifest.json').read_text())
        self.assertFalse(manifest['complete'])

    def test_oom_is_explicit_without_automatic_retry(self):
        class FakeOOM(RuntimeError):
            pass
        fake_torch = types.SimpleNamespace(OutOfMemoryError=FakeOOM)
        with patch.dict(sys.modules, {'torch': fake_torch}):
            result, code = self.run_fake(failure=FakeOOM('synthetic GPU failure'))
        self.assertEqual(code, 2)
        self.assertEqual(result['error_code'], 'out_of_memory_reduce_batch_size')
        self.assertFalse((Path(result['run_directory']) / 'candidate.json').exists())

    def test_link_guard_without_creating_real_symlinks(self):
        import stat
        fake_info = types.SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        with patch.object(Path, 'lstat', return_value=fake_info):
            with self.assertRaisesRegex(bundle.Failure, 'link_path_rejected'):
                bundle.reject_links(self.dataset)

    def test_encoder_requires_cuda_and_rejects_model_drift(self):
        encoder = bundle.Encoder(self.workspace / 'model', 'a' * 40, 'cuda', 16)
        fake_torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False))
        with patch.dict(sys.modules, {'torch': fake_torch}):
            with self.assertRaisesRegex(bundle.Failure, 'cuda_unavailable'):
                encoder.load()
        encoder.fingerprint = 'initial'
        with patch.object(bundle, 'model_fingerprint', return_value='changed'):
            with self.assertRaisesRegex(bundle.Failure, 'model_changed'):
                encoder.verify()

    def test_cleanup_failure_does_not_complete_candidate(self):
        class BrokenCloseEncoder:
            def __init__(self, *args):
                self.model_identity = bundle.identity('a' * 40, 'f' * 64, True)
            def load(self):
                pass
            def preflight(self, cases):
                return {'status': 'passed', 'identity': self.model_identity}
            def numerical_comparison(self):
                return {'status': 'not_run'}
            def verify(self):
                pass
            def close(self):
                raise RuntimeError('synthetic close failure')
        self.data.update(reviewed=True, review_notes='Synthetic fixture, not acceptance.')
        self.save()
        with patch.object(bundle, 'Encoder', BrokenCloseEncoder), \
             patch.object(bundle, 'load_training_dependencies', return_value={}), \
             patch.object(bundle, 'train_loaded', return_value=({}, {'gates': {}})):
            result, code = bundle.execute(self.args())
        self.assertEqual(code, 2)
        directory = Path(result['run_directory'])
        self.assertFalse(json.loads((directory / 'manifest.json').read_text())['complete'])
        self.assertFalse((directory / 'candidate.json').exists())

    def test_preflight_does_not_write_run(self):
        result, code = self.run_fake(command='preflight')
        self.assertEqual((result['status'], code), ('preflight_passed', 0))
        self.assertFalse((self.private / 'runs').exists())

    def test_numerical_training_parity_when_available(self):
        try:
            import numpy as np
            import sklearn
        except ImportError:
            self.skipTest('NumPy/sklearn unavailable; real numerical parity not executed')
        # Load only training dependencies, not torch/SentenceTransformer or the DB.
        from sklearn.exceptions import ConvergenceWarning
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import f1_score, confusion_matrix, precision_score
        from sklearn.model_selection import StratifiedGroupKFold
        scope = dict(bundle.__dict__, np=np, sklearn=sklearn, ConvergenceWarning=ConvergenceWarning,
                     LogisticRegression=LogisticRegression, f1_score=f1_score,
                     confusion_matrix=confusion_matrix, precision_score=precision_score,
                     StratifiedGroupKFold=StratifiedGroupKFold)
        for name in ('fit_head', 'accepted_mask', 'source_metrics', 'calibrate', 'evaluate', 'train'):
            upstream_function('src/intent/training.py', name, scope)
        for name, value in scope.items():
            if name in ('np', 'sklearn', 'ConvergenceWarning', 'LogisticRegression', 'f1_score',
                        'confusion_matrix', 'precision_score', 'StratifiedGroupKFold'):
                setattr(bundle, name, value)
        vectors = np.zeros((600, 384))
        for i, case in enumerate(self.data['cases']):
            vectors[i, bundle.SOURCES.index(case['source'])] = 2
            vectors[i, 10:15] = [int(s in case['styles']) for s in bundle.STYLES]
        identity = bundle.identity('a' * 40, 'f' * 64, True)
        self.assertEqual(bundle.train(vectors, self.data['cases'], identity),
                         scope['train'](vectors, self.data['cases'], identity))


if __name__ == '__main__':
    unittest.main()
