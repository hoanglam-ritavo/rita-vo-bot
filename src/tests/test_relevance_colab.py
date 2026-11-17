"""Fictional standalone development exchange; no models, DB, or real labels."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from scripts import relevance_colab as exchange
from src.retrieval.scoring_training import fit_development

flow = exchange.runtime()
KIT = exchange.KIT


def save(path, value):
    path.write_bytes(flow.encode(value))
    return path


@pytest.fixture
def bundle(tmp_path):
    data = flow.example()
    data.update(dataset_sha256='a' * 64, collection_sha256='b' * 64,
                review_sha256='c' * 64, corpus_fingerprint='d' * 64,
                source_package_hashes={'chunks/fictional-path': 'e' * 64},
                review_notes='PRIVATE_REVIEW_TEXT', unknown='PRIVATE_UNKNOWN_TEXT')
    data['rows'][0]['source_excerpt'] = 'PRIVATE_SOURCE_TEXT'
    dataset = save(tmp_path / 'reviewed.json', data)
    output = tmp_path / 'input.zip'
    flow.export_input(dataset, output)
    return output


@pytest.fixture
def fitted(bundle, tmp_path):
    result = tmp_path / 'candidate.zip'
    flow.fit_bundle(bundle, result)
    return result


def rewrite(path, entries):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in entries.items():
            archive.writestr(name, raw)


def rebind(entries):
    manifest = flow.decode(entries['manifest.json'])
    manifest['files_sha256'] = {n: flow.sha(v) for n, v in entries.items() if n != 'manifest.json'}
    return entries | {'manifest.json': flow.encode(manifest)}


def test_source_copies_manifest_and_fitter_parity(bundle):
    files, _, data, _ = flow.load_input(bundle)
    for name in ('scoring.py', 'scoring_training.py'):
        assert (KIT / 'relevance_kit' / name).read_bytes() == (
            exchange.ROOT / 'src' / 'retrieval' / name).read_text(encoding='utf-8').encode('utf-8')
    canonical = fit_development(data)
    vendored = flow.fit_development(data)
    assert canonical == vendored
    all_bytes = b''.join(files.values())
    for private in (b'PRIVATE_', b'fictional-path', b'source_excerpt', b'unknown'):
        assert private not in all_bytes
    assert files['development.json']


def test_real_export_requires_review_bridge_hashes(tmp_path):
    data = flow.example()
    source = save(tmp_path / 'demo.json', data)
    with pytest.raises(ValueError, match='review_bridge_provenance_required'):
        flow.export_input(source, tmp_path / 'bad.zip')


def test_round_trip_is_cpu_rechecked_never_accepted(bundle, fitted, tmp_path):
    output = tmp_path / 'imported'
    receipt = flow.import_result(bundle, fitted, output)
    assert receipt['accepted_profile'] is False
    assert receipt['local_cpu']['status'] == 'passed'
    assert receipt['status'] == 'development_candidate_only'
    report = json.loads((output / 'training-report.json').read_bytes())
    assert report['accepted_profile'] is False and report['holdout_evaluated'] is False
    assert report['cases'] == 300
    assert not list(output.glob('*profile*'))
    with pytest.raises(FileExistsError):
        flow.import_result(bundle, fitted, output)


def test_synthetic_import_is_marked(tmp_path):
    source = save(tmp_path / 'fictional.json', flow.example())
    bundle = tmp_path / 'input.zip'
    result = tmp_path / 'output.zip'
    flow.export_input(source, bundle, synthetic=True)
    flow.fit_bundle(bundle, result)
    assert flow.import_result(bundle, result, tmp_path / 'imported')['status'] == 'synthetic_candidate_only'


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(feature_names=list(reversed(d['feature_names']))),
    lambda d: d.update(reviewed=False),
    lambda d: d.update(split='holdout'),
    lambda d: d['rows'][0].update(split='calibration'),
    lambda d: d['rows'][0].update(relevant=None),
    lambda d: d['rows'][0].update(features=[1.] * 7),
    lambda d: d['rows'][0]['features'].__setitem__(0, True),
    lambda d: d['rows'].__delitem__(slice(0, 10)),
])
def test_invalid_training_is_rejected_before_output(tmp_path, mutation):
    data = flow.example()
    mutation(data)
    source = save(tmp_path / 'bad.json', data)
    result = tmp_path / 'bad.zip'
    with pytest.raises(ValueError):
        flow.export_input(source, result, synthetic=True)
    assert not result.exists()


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'])
def test_strict_json(raw):
    with pytest.raises(ValueError):
        flow.decode(raw)


def test_input_mutation_and_contract_drift_rejected(bundle, tmp_path):
    entries = flow.read_archive(bundle, flow.INPUTS)
    damaged = tmp_path / 'damaged.zip'
    rewrite(damaged, entries | {'development.json': b'{}'})
    with pytest.raises(ValueError, match='binding'):
        flow.load_input(damaged)
    manifest = flow.decode(entries['manifest.json'])
    manifest['kit_identity'] = '0' * 64
    rewrite(damaged, entries | {'manifest.json': flow.encode(manifest)})
    with pytest.raises(ValueError, match='binding'):
        flow.load_input(damaged)


def test_refit_rejects_rehashed_forged_coefficients(bundle, fitted, tmp_path):
    from relevance_kit.scoring import scoring_digest
    entries = flow.read_archive(fitted, flow.OUTPUTS)
    candidate = flow.decode(entries['candidate.json'])
    candidate['bias'] += 1.0
    candidate['digest'] = scoring_digest(candidate)
    report = flow.decode(entries['training-report.json'])
    report['scoring_digest'] = candidate['digest']
    entries.update({'candidate.json': flow.encode(candidate), 'training-report.json': flow.encode(report)})
    altered = tmp_path / 'altered.zip'
    rewrite(altered, rebind(entries))
    output = tmp_path / 'not-created'
    with pytest.raises(ValueError, match='local_refit_mismatch'):
        flow.import_result(bundle, altered, output)
    assert not output.exists()


def test_candidate_bound_to_exact_original_input(bundle, fitted, tmp_path):
    entries = flow.read_archive(bundle, flow.INPUTS)
    manifest = flow.decode(entries['manifest.json'])
    manifest['source_file_sha256'] = '0' * 64
    other = tmp_path / 'other.zip'
    rewrite(other, entries | {'manifest.json': flow.encode(manifest)})
    with pytest.raises(ValueError, match='candidate_input_mismatch'):
        flow.import_result(other, fitted, tmp_path / 'not-created')


def test_archive_rejects_traversal_duplicate_link_and_size(bundle, tmp_path, monkeypatch):
    entries = flow.read_archive(bundle, flow.INPUTS)
    bad = tmp_path / 'unsafe.zip'
    rewrite(bad, entries | {'../escaped': b'bad'})
    with pytest.raises(ValueError, match='members'):
        flow.read_archive(bad, flow.INPUTS)
    with zipfile.ZipFile(bad, 'w') as archive:
        for name, raw in entries.items():
            member = zipfile.ZipInfo(name)
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, raw)
    with pytest.raises(ValueError, match='unsafe_archive'):
        flow.read_archive(bad, flow.INPUTS)
    with zipfile.ZipFile(bad, 'w') as archive:
        archive.writestr('development.json', b'{}')
        with pytest.warns(UserWarning, match='Duplicate'):
            archive.writestr('development.json', b'{}')
        archive.writestr('manifest.json', b'{}')
    with pytest.raises(ValueError, match='members'):
        flow.read_archive(bad, flow.INPUTS)
    monkeypatch.setattr(flow, 'LIMIT', 1)
    with pytest.raises(ValueError, match='too_large'):
        flow.read_archive(bundle, flow.INPUTS)
    assert not (tmp_path.parent / 'escaped').exists()


def test_gpu_missing_and_numerical_mismatch_are_explicit(monkeypatch):
    from relevance_kit.gpu import compare_scores, diagnose
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    assert diagnose([], {})['reason'] == 'cuda_unavailable'
    monkeypatch.setitem(sys.modules, 'torch', None)
    assert diagnose([], {})['reason'] == 'torch_not_installed'
    diagnostic = compare_scores([1., 2.], [3., 2.])
    assert diagnostic['within_tolerance'] is False and diagnostic['rank_positions_changed'] == 2
    assert diagnostic['acceptance_evidence'] is False
    with pytest.raises(ValueError):
        compare_scores([1.], [float('nan')])


def test_standalone_archive_smoke_without_repo_environment(bundle, tmp_path):
    kit_zip = tmp_path / 'kit.zip'
    manifest_raw = (KIT / 'kit-manifest.json').read_bytes()
    manifest = flow.decode(manifest_raw)
    flow.write_archive(kit_zip, {name: (KIT / name).read_bytes()
                               for name in manifest['files_sha256']} | {'kit-manifest.json': manifest_raw})
    extracted = tmp_path / 'extracted'
    with zipfile.ZipFile(kit_zip) as archive:
        archive.extractall(extracted)  # Trusted kit constructed above, not supplied exchange input.
    isolated_env = dict(os.environ)
    isolated_env.pop('PYTHONPATH', None)
    isolated_env['PYTHONDONTWRITEBYTECODE'] = '1'
    result = tmp_path / 'standalone.zip'
    for args in (['check'], ['fit', '--bundle', str(bundle), '--output', str(result), '--gpu']):
        completed = subprocess.run([sys.executable, '-B', '-m', 'relevance_kit', *args],
            cwd=extracted, env=isolated_env, capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr + completed.stdout
    assert flow.import_result(bundle, result, tmp_path / 'imported')['accepted_profile'] is False


def test_notebook_cells_compile_and_do_not_store_outputs():
    notebook = json.loads((KIT / 'notebooks/build_v3.ipynb').read_bytes())
    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            assert cell['execution_count'] is None and cell['outputs'] == []
            compile(''.join(cell['source']), 'colab-cell', 'exec')


def test_private_paths_and_upstream_drift(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='private_tmp'):
        exchange.private(exchange.ROOT / 'artifacts/rerank/relevance-profile.json')
    root = tmp_path / 'changed-root'
    source = root / 'src/retrieval'
    source.mkdir(parents=True)
    for name in ('scoring.py', 'scoring_training.py'):
        (source / name).write_bytes((KIT / 'relevance_kit' / name).read_bytes() + b'\n# Changed\n')
    monkeypatch.setattr(exchange, 'ROOT', root)
    with pytest.raises(ValueError, match='upstream_drift'):
        exchange.runtime()


def test_kit_file_drift_rejected_and_package_allowlist(tmp_path, monkeypatch):
    kit = tmp_path / 'copy'
    shutil.copytree(KIT, kit, ignore=shutil.ignore_patterns('__pycache__'))
    (kit / '.env').write_text('FICTIONAL_NOT_FOR_UPLOAD=example')
    monkeypatch.setattr(exchange, 'KIT', kit)
    monkeypatch.setattr(flow, 'ROOT', kit)
    output = tmp_path / 'kit.zip'
    exchange.package(output)
    with zipfile.ZipFile(output) as archive:
        assert '.env' not in archive.namelist()
        assert 'kit-manifest.json' in archive.namelist()
    (kit / 'relevance_kit/scoring.py').write_bytes(b'changed')
    with pytest.raises(ValueError, match='kit_file_changed'):
        flow.kit_identity()


@pytest.mark.parametrize('alter', ['digest', 'scale', 'report', 'acceptance'])
def test_invalid_returned_candidate_rejected(bundle, fitted, tmp_path, alter):
    entries = flow.read_archive(fitted, flow.OUTPUTS)
    if alter in ('digest', 'scale'):
        value = flow.decode(entries['candidate.json'])
        if alter == 'digest':
            value['digest'] = '0' * 64
        else:
            value['scales'][0] = 0.
        entries['candidate.json'] = flow.encode(value)
    elif alter == 'report':
        value = flow.decode(entries['training-report.json'])
        value['fold_assignments']['family-0'] = 17
        entries['training-report.json'] = flow.encode(value)
    else:
        value = flow.decode(entries['manifest.json'])
        value['accepted_profile'] = True
        entries['manifest.json'] = flow.encode(value)
    bad = tmp_path / 'bad-result.zip'
    rewrite(bad, rebind(entries))
    output = tmp_path / 'not-created'
    with pytest.raises(ValueError):
        flow.import_result(bundle, bad, output)
    assert not output.exists()
